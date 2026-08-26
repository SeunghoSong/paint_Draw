"""
Container B - 영상 분석 엔진 (Vision Engine)

역할: Container A로부터 프레임(base64 jpeg)을 WebSocket으로 수신하여
      MediaPipe Hands로 21개 손 랜드마크(x, y) 좌표를 추출하고,
      결과를 Container C(동작 인식 엔진)로 WebSocket 전달한다.

책임 경계: 손 검출/좌표 추출까지만 담당한다. 좌표가 어떤 제스처인지
          해석하는 것은 Container C의 책임이며 여기서는 하지 않는다.
"""

import asyncio
import base64
import json
import logging
import os
import sys
import time
import urllib.request

import cv2
import numpy as np
import mediapipe as mp
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from mediapipe.tasks import python as mp_tasks
from mediapipe.tasks.python import vision as mp_vision

# ---- 컨테이너/세션 상수 ----
CONTAINER = "B"
DEFAULT_SESSION_ID = "poc-001"  # POC 범위에서는 세션 ID 고정값 사용

# Container C(동작 인식 엔진) WebSocket 주소.
# docker-compose 서비스명 기준 기본값이며, 필요 시 환경변수로 재정의 가능.
MOTION_ENGINE_WS_URL = os.environ.get("MOTION_ENGINE_WS_URL", "ws://motion_engine:8002/ws")

app = FastAPI()

# ---- 공통 로깅 포맷 (프로젝트 전체 컨테이너 공통 스펙: 한 줄 JSON) ----
logger = logging.getLogger("video_engine")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler(sys.stdout))
logger.propagate = False


def log_event(level: str, session_id: str, event: str, detail: dict = None) -> None:
    """공통 로그 포맷 {"ts","container","session_id","level","event","detail"} 으로 한 줄 출력."""
    record = {
        "ts": int(time.time() * 1000),
        "container": CONTAINER,
        "session_id": session_id,
        "level": level,
        "event": event,
        "detail": detail or {},
    }
    logger.info(json.dumps(record, ensure_ascii=False))


# ---- MediaPipe Hands 초기화 (Tasks API) ----
# mediapipe는 구 mp.solutions.hands API를 제거하고 HandLandmarker(Tasks API)로 통일했다.
# Tasks API는 모델 파일(.task)이 별도로 필요해서, 없으면 최초 1회 공식 URL에서 내려받는다.
MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
MODEL_PATH = os.environ.get("HAND_LANDMARKER_MODEL_PATH", os.path.join(MODEL_DIR, "hand_landmarker.task"))
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task"
)


def ensure_model() -> bytes:
    """Hand Landmarker 모델 파일이 로컬에 없으면 최초 1회 내려받고, 파일 내용을 bytes로 반환한다.

    mediapipe 네이티브 로더에 파일 경로를 직접 넘기면 프로젝트 경로에 한글 등
    비 ASCII 문자가 섞여 있을 때 파일을 열지 못하는 문제가 있어(Windows에서 확인됨),
    Python에서 직접 읽은 bytes를 model_asset_buffer로 넘겨 이를 피한다.
    """
    if not os.path.exists(MODEL_PATH):
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    with open(MODEL_PATH, "rb") as f:
        return f.read()


# 프레임마다 독립적으로 들어오는 base64 이미지를 처리하므로 IMAGE 모드 사용
# (VIDEO/LIVE_STREAM 모드는 연속 타임스탬프 관리가 필요해 이 구조와 맞지 않음)
_hand_landmarker_options = mp_vision.HandLandmarkerOptions(
    base_options=mp_tasks.BaseOptions(model_asset_buffer=ensure_model()),
    num_hands=1,  # POC 범위는 손 1개만 추적
    min_hand_detection_confidence=0.6,
    min_hand_presence_confidence=0.5,
    min_tracking_confidence=0.5,
    running_mode=mp_vision.RunningMode.IMAGE,
)
hands_detector = mp_vision.HandLandmarker.create_from_options(_hand_landmarker_options)


def decode_base64_frame(frame_b64: str):
    """base64 인코딩된 jpeg 문자열을 OpenCV(BGR) 이미지로 디코딩한다."""
    jpg_bytes = base64.b64decode(frame_b64)
    np_arr = np.frombuffer(jpg_bytes, dtype=np.uint8)
    return cv2.imdecode(np_arr, cv2.IMREAD_COLOR)


def extract_landmarks(image_bgr):
    """
    이미지에서 손 21개 랜드마크의 (x, y) 정규화(0~1) 좌표를 추출한다.
    손이 검출되지 않거나 이미지가 유효하지 않으면 (False, []) 를 반환하며
    예외를 던지지 않는다 (호출부 크래시 방지).
    """
    if image_bgr is None:
        return False, []

    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)
    result = hands_detector.detect(mp_image)

    if not result.hand_landmarks:
        return False, []

    # 여러 손이 검출돼도 POC 범위에서는 첫 번째 손만 사용
    first_hand = result.hand_landmarks[0]
    # z는 사용하지 않고 x, y만 사용. 픽셀 변환은 하지 않는다 (Container C 책임)
    landmarks = [[lm.x, lm.y] for lm in first_hand]
    return True, landmarks


class MotionEngineClient:
    """
    Container C로 랜드마크 결과를 전달하는 WebSocket 클라이언트.
    연결이 없거나 끊긴 상태면 다음 전송 시 자동으로 재연결을 시도하고,
    실패해도 예외를 흡수해서 Container B 프로세스는 죽지 않게 한다.
    """

    def __init__(self, url: str):
        self.url = url
        self._ws = None
        self._lock = asyncio.Lock()

    async def send(self, payload: dict, session_id: str) -> None:
        async with self._lock:
            try:
                if self._ws is None:
                    self._ws = await websockets.connect(self.url)
                    log_event("INFO", session_id, "ws_connected", {"peer": "container_c"})
                await self._ws.send(json.dumps(payload))
            except Exception as exc:  # 연결/전송 실패는 여기서 흡수하고 로그만 남김
                log_event("ERROR", session_id, "frame_forward_failed", {"error": str(exc)})
                self._ws = None


motion_client = MotionEngineClient(MOTION_ENGINE_WS_URL)


@app.get("/health")
def health():
    return {"status": "ok", "service": "video_engine"}


@app.websocket("/ws")
async def receive_frames(websocket: WebSocket):
    """Container A로부터 프레임을 수신하고, 랜드마크 추출 후 Container C로 전달한다."""
    await websocket.accept()
    session_id = DEFAULT_SESSION_ID
    log_event("INFO", session_id, "ws_connected", {"peer": "container_a"})

    try:
        while True:
            raw = await websocket.receive_text()

            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                log_event("ERROR", session_id, "frame_forward_failed", {"error": "invalid_json"})
                continue

            session_id = message.get("session_id", session_id)
            if message.get("type") != "frame":
                continue  # frame 타입이 아닌 메시지는 무시

            log_event("INFO", session_id, "frame_received", {})

            # 디코딩/검출 중 어떤 예외가 나도 크래시 대신 미검출로 처리
            try:
                image = decode_base64_frame(message.get("frame", ""))
                detected, landmarks = extract_landmarks(image)
            except Exception as exc:
                detected, landmarks = False, []
                log_event("ERROR", session_id, "hand_not_detected", {"error": str(exc)})

            if detected:
                log_event("INFO", session_id, "landmarks_extracted", {"detected": True, "count": len(landmarks)})
            else:
                log_event("INFO", session_id, "hand_not_detected", {"detected": False})

            landmarks_message = {
                "session_id": session_id,
                "type": "landmarks",
                "detected": detected,
                "landmarks": landmarks,
                "ts": int(time.time() * 1000),
            }
            await motion_client.send(landmarks_message, session_id)

    except WebSocketDisconnect:
        log_event("INFO", session_id, "ws_disconnected", {"peer": "container_a"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
