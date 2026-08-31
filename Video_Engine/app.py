"""
Container B - 영상 분석 엔진 (Vision Engine)

역할: Container A로부터 프레임(base64 jpeg)을 WebSocket으로 수신하여
      MediaPipe Hands로 21개 손 랜드마크(x, y) 좌표를 추출하고,
      결과를 Container A에게 돌려준다. Container A가 그 결과를 Container C
      (동작 인식 엔진)로 전달할지 말지 결정한다 (버그수정 참고: 아래 및
      docs/03-layer-logging-responsibility.md).

책임 경계: 손 검출/좌표 추출까지만 담당한다. 좌표가 어떤 제스처인지
          해석하는 것은 Container C의 책임이며 여기서는 하지 않는다.

버그수정: 예전엔 여기서 Container C로 WebSocket 직접 전송도 같이 하고 있었는데,
Container A가 이 결과를 받아 Container C의 HTTP `/gesture`로 다시 호출하는 경로가
실제 렌더링에 쓰이는 경로였다. 두 경로가 같은 세션의 EMA/디바운스 상태를 동시에
건드려서 제스처 연산이 프레임마다 중복 실행되고 있었음 → Container C로의 직접 전송
(MotionEngineClient, websockets 의존성)을 제거하고 Container A 경유 경로 하나만 남김.
Container B↔C를 직접 검증하고 싶으면 `webcam_motion_test.py`(독립 실행 스크립트, Motion_Engine의
`/ws`에 직접 연결)를 사용할 것 — 그쪽은 이 프로덕션 경로와 무관하게 그대로 동작한다.
"""

import base64
import hashlib
import json
import logging
import os
import sys
import time
import urllib.request
import urllib.error

import cv2
import numpy as np
import mediapipe as mp
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from mediapipe.tasks import python as mp_tasks
from mediapipe.tasks.python import vision as mp_vision

# ---- 컨테이너/세션 상수 ----
CONTAINER = "B"
# 파라미터화: 세션ID/MediaPipe 임계값을 환경변수로 분리 (기본값은 기존 하드코딩 값과 동일)
# DEFAULT_SESSION_ID = "poc-001"  # 파라미터화 이전 하드코딩 값 (POC 범위에서는 세션 ID 고정값 사용)
DEFAULT_SESSION_ID = os.environ.get("DEFAULT_SESSION_ID", "poc-001")

# 아래 4개는 파라미터화 이전엔 HandLandmarkerOptions 생성 시 리터럴로 직접 하드코딩돼 있었음
# (num_hands=1, min_hand_detection_confidence=0.6, min_hand_presence_confidence=0.5, min_tracking_confidence=0.5)
HAND_NUM_HANDS = int(os.environ.get("HAND_NUM_HANDS", "1"))
HAND_MIN_DETECTION_CONFIDENCE = float(os.environ.get("HAND_MIN_DETECTION_CONFIDENCE", "0.6"))
HAND_MIN_PRESENCE_CONFIDENCE = float(os.environ.get("HAND_MIN_PRESENCE_CONFIDENCE", "0.5"))
HAND_MIN_TRACKING_CONFIDENCE = float(os.environ.get("HAND_MIN_TRACKING_CONFIDENCE", "0.5"))

app = FastAPI()

# ---- 공통 로깅 포맷 (프로젝트 전체 컨테이너 공통 스펙: 한 줄 JSON) ----
logger = logging.getLogger("video_engine")
# logger.setLevel(logging.INFO)  # 로깅 이전: 레벨 고정값 (환경변수로 제어 불가능했음)
# 로깅: LOG_LEVEL 환경변수로 운영/개발 전환 (기본 INFO). "WARN"도 logging 모듈이 WARNING의 별칭으로 인식.
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
if not logger.handlers:
    logger.addHandler(logging.StreamHandler(sys.stdout))
logger.propagate = False

# 로깅: level 문자열에 맞는 logger 메서드로 실제 디스패치.
# (기존엔 level 인자가 JSON 필드로만 쓰이고 항상 logger.info()로만 출력돼서,
#  logger.setLevel()을 아무리 조여도 ERROR 로그조차 필터링되지 않는 문제가 있었음)
_LOG_METHODS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def log_event(level: str, session_id: str, event: str, detail: dict = None, trace_id: str = None) -> None:
    """공통 로그 포맷 {"ts","container","session_id","trace_id","level","event","detail"} 으로 한 줄 출력.

    trace_id: 프레임 1개가 A→B→(A→C)로 흘러가는 과정을 추적하는 요청 단위 ID.
    session_id(세션 전체를 묶는 축)와는 다른 축이라 별도 필드로 둔다.
    """
    record = {
        "ts": int(time.time() * 1000),
        "container": CONTAINER,
        "session_id": session_id,
        "trace_id": trace_id,
        "level": level,
        "event": event,
        "detail": detail or {},
    }
    # logger.info(json.dumps(record, ensure_ascii=False))  # 로깅 이전: 레벨 무관하게 항상 info로 출력
    logger.log(_LOG_METHODS.get(level.upper(), logging.INFO), json.dumps(record, ensure_ascii=False))
    _maybe_alert_on_error(level, event, detail or {})


# 로깅: 알림 정책(docs/04-alert-policy.md) 구현. SLACK_WEBHOOK_URL이 비어있으면 완전히 비활성 —
# 실제 채널/웹훅은 팀이 정해서 넣어야 하는 값이라 여기서 아무 것도 지어내지 않음.
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
# 문서 규칙: "ERROR 반복 발생: 5분간 N회 이상". 기본값은 안전한 초기값일 뿐, 실사용 트래픽을
# 보고 조정해야 한다(문서에도 그렇게 명시돼 있음).
# Node(Web_Server)와 동일한 ALERT_ERROR_WINDOW_MS(밀리초) 환경변수를 공유해서 3개 서비스가 같은
# 값 하나로 맞춰지게 함 (Python 내부에서는 초 단위로 다루는 게 자연스러워 여기서 변환)
ALERT_ERROR_WINDOW_SECONDS = float(os.environ.get("ALERT_ERROR_WINDOW_MS", "300000")) / 1000.0
ALERT_ERROR_THRESHOLD = int(os.environ.get("ALERT_ERROR_THRESHOLD", "10"))

_error_window_start = time.time()
_error_window_count = 0


def _send_slack_alert(text: str) -> None:
    if not SLACK_WEBHOOK_URL:
        return
    try:
        req = urllib.request.Request(
            SLACK_WEBHOOK_URL,
            data=json.dumps({"text": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)
    except urllib.error.URLError as exc:
        logger.error(json.dumps({"event": "slack_alert_failed", "error": str(exc)}))


def _maybe_alert_on_error(level: str, event: str, detail: dict) -> None:
    """디바운싱/그룹핑: 임계치를 "넘는 순간"에만 1번 보내고, 창이 갈릴 때만 다시 셈 —
    같은 창 안에서 에러가 반복돼도 알림이 스팸으로 반복 발송되지 않는다."""
    global _error_window_start, _error_window_count

    if not SLACK_WEBHOOK_URL:
        return

    if level.upper() != "ERROR":
        return

    now = time.time()
    if now - _error_window_start > ALERT_ERROR_WINDOW_SECONDS:
        _error_window_start = now
        _error_window_count = 0
    _error_window_count += 1

    if _error_window_count == ALERT_ERROR_THRESHOLD:
        _send_slack_alert(
            f":rotating_light: [Video_Engine] 최근 {int(ALERT_ERROR_WINDOW_SECONDS)}초 동안 "
            f"ERROR {ALERT_ERROR_THRESHOLD}회 발생 (마지막 이벤트: {event})"
        )


# ---- MediaPipe Hands 초기화 (Tasks API) ----
# mediapipe는 구 mp.solutions.hands API를 제거하고 HandLandmarker(Tasks API)로 통일했다.
# Tasks API는 모델 파일(.task)이 별도로 필요해서, 없으면 최초 1회 공식 URL에서 내려받는다.
MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
MODEL_PATH = os.environ.get("HAND_LANDMARKER_MODEL_PATH", os.path.join(MODEL_DIR, "hand_landmarker.task"))
MODEL_MANIFEST_PATH = os.path.join(MODEL_DIR, "manifest.json")
# 버전관리: URL에 "latest"가 박혀있으면 재빌드 시점마다 다른 모델이 내려받아질 수 있어
# manifest.json(고정 버전 + 체크섬)을 단일 소스로 사용하도록 변경.
# MODEL_URL = (
#     "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
#     "hand_landmarker/float16/latest/hand_landmarker.task"
# )  # 버전관리 이전: "latest" 경로 사용 (재빌드마다 모델이 바뀔 수 있는 위험)


def _load_model_manifest() -> dict:
    """models/manifest.json에서 고정된 모델 버전/다운로드 URL/체크섬을 읽는다."""
    with open(MODEL_MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def ensure_model() -> bytes:
    """Hand Landmarker 모델 파일이 로컬에 없으면 manifest.json에 명시된 고정 버전을 내려받고,
    체크섬을 검증한 뒤 파일 내용을 bytes로 반환한다.

    mediapipe 네이티브 로더에 파일 경로를 직접 넘기면 프로젝트 경로에 한글 등
    비 ASCII 문자가 섞여 있을 때 파일을 열지 못하는 문제가 있어(Windows에서 확인됨),
    Python에서 직접 읽은 bytes를 model_asset_buffer로 넘겨 이를 피한다.
    """
    manifest = _load_model_manifest()  # 버전관리: model_url을 manifest.json 기준으로 결정
    model_url = manifest["source_url"]
    expected_checksum = manifest["checksum"]["value"]
    checksum_algorithm = manifest["checksum"]["algorithm"]

    if not os.path.exists(MODEL_PATH):
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        urllib.request.urlretrieve(model_url, MODEL_PATH)

    with open(MODEL_PATH, "rb") as f:
        data = f.read()

    # 버전관리: 다운로드된 모델이 manifest.json에 명시된 버전과 일치하는지 체크섬으로 검증
    actual_checksum = hashlib.new(checksum_algorithm, data).hexdigest()
    if actual_checksum != expected_checksum:
        raise RuntimeError(
            f"모델 체크섬 불일치 (manifest version={manifest['version']}): "
            f"expected={expected_checksum} actual={actual_checksum}"
        )

    return data


# VIDEO 모드: 이전 프레임의 추적 정보를 활용해 프레임 간 손 위치를 더 안정적으로 추적한다.
# IMAGE 모드와 달리 detect_for_video(image, timestamp_ms)를 호출해야 하며
# timestamp_ms는 항상 이전 호출보다 커야 한다 (아래 _next_video_timestamp_ms 참고).
#
# 주의: hands_detector 인스턴스 하나를 모든 WebSocket 연결이 공유하므로, VIDEO 모드의
# "연속된 하나의 영상" 가정은 동시에 여러 세션이 프레임을 보낼 경우 깨진다. 현재는
# DEFAULT_SESSION_ID가 고정값인 POC 범위(단일 세션 가정)라 문제되지 않지만, 세션별
# 동시 처리를 지원하려면 세션마다 별도의 HandLandmarker 인스턴스가 필요하다.
_hand_landmarker_options = mp_vision.HandLandmarkerOptions(
    base_options=mp_tasks.BaseOptions(model_asset_buffer=ensure_model()),
    # num_hands=1, min_hand_detection_confidence=0.6, min_hand_presence_confidence=0.5, min_tracking_confidence=0.5,  # 파라미터화 이전 하드코딩 값
    num_hands=HAND_NUM_HANDS,  # 파라미터화: 환경변수 사용 (POC 범위는 손 1개만 추적이 기본값)
    min_hand_detection_confidence=HAND_MIN_DETECTION_CONFIDENCE,
    min_hand_presence_confidence=HAND_MIN_PRESENCE_CONFIDENCE,
    min_tracking_confidence=HAND_MIN_TRACKING_CONFIDENCE,
    running_mode=mp_vision.RunningMode.VIDEO,
)
hands_detector = mp_vision.HandLandmarker.create_from_options(_hand_landmarker_options)

_video_timestamp_ms = 0


def _next_video_timestamp_ms() -> int:
    """VIDEO 모드가 요구하는 단조증가 타임스탬프를 생성한다.

    실제 시각(wall clock)을 기준으로 하되, 같은 밀리초 안에 프레임이 연달아
    들어오거나 시스템 시각이 역행해도 항상 이전 값보다 커지도록 보정한다.
    """
    global _video_timestamp_ms
    now_ms = int(time.time() * 1000)
    _video_timestamp_ms = max(now_ms, _video_timestamp_ms + 1)
    return _video_timestamp_ms


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
    result = hands_detector.detect_for_video(mp_image, _next_video_timestamp_ms())

    if not result.hand_landmarks:
        return False, []

    # 여러 손이 검출돼도 POC 범위에서는 첫 번째 손만 사용
    first_hand = result.hand_landmarks[0]
    # z는 사용하지 않고 x, y만 사용. 픽셀 변환은 하지 않는다 (Container C 책임)
    landmarks = [[lm.x, lm.y] for lm in first_hand]
    return True, landmarks


# 로깅: "정상 vs 오류" 경계 사례 구현 (docs/01-log-level-guidelines.md 참고).
# 손이 한 프레임 안 잡히는 건 정상(DEBUG)이지만, 일정 시간 이상 연속으로 안 잡히면
# 카메라가 가려졌거나 각도가 안 맞는 등 실제 사용자 경험에 영향을 주는 상태라 WARNING으로 격상.
NO_HAND_WARNING_SECONDS = float(os.environ.get("NO_HAND_WARNING_SECONDS", "3.0"))
_SESSION_STATE_MAX_AGE_SECONDS = 3600  # 오래 안 쓰인 세션 항목 정리 기준 (메모리 누수 방지)
_last_hand_seen_at: dict = {}
_no_hand_warned: dict = {}


def _note_hand_seen(session_id: str, now: float) -> None:
    _last_hand_seen_at[session_id] = now
    _no_hand_warned[session_id] = False
    # Motion_Engine의 세션 정리(get_or_create_session)와 동일한 방식의 인라인 정리
    stale = [sid for sid, ts in _last_hand_seen_at.items() if now - ts > _SESSION_STATE_MAX_AGE_SECONDS]
    for sid in stale:
        _last_hand_seen_at.pop(sid, None)
        _no_hand_warned.pop(sid, None)


def _check_prolonged_no_hand(session_id: str, now: float, trace_id: str) -> None:
    last_seen = _last_hand_seen_at.get(session_id, now)  # 이 세션에서 한 번도 손을 못 봤으면 지금을 기준으로 삼아 오탐 방지
    elapsed = now - last_seen
    if elapsed > NO_HAND_WARNING_SECONDS and not _no_hand_warned.get(session_id, False):
        log_event("WARNING", session_id, "hand_not_detected_prolonged", {"seconds": round(elapsed, 1)}, trace_id)
        _no_hand_warned[session_id] = True


# 버그수정: 여기 있던 MotionEngineClient(Container C로의 직접 WebSocket 전송)를 제거함.
# Container A가 아래 receive_frames()의 반환값을 받아 Container C의 HTTP /gesture를 호출하는
# 경로가 실제로 쓰이는 경로였고, 이 클래스는 그 경로와 별개로 같은 프레임을 Container C에 한 번
# 더 보내서 (아무도 응답을 읽지 않는데도) Container C의 세션 상태(EMA/디바운스)를 이중으로
# 갱신시키고 있었다. 자세한 내용은 docs/03-layer-logging-responsibility.md 참고.


@app.get("/health")
def health():
    # 예외처리: 단순 "프로세스 생존"이 아니라 모델이 실제로 로드된 상태인지까지 확인.
    # hands_detector는 여러 연결이 공유하는 단일 인스턴스라, 헬스체크에서 실제 추론을 실행하면
    # 진행 중인 세션의 VIDEO 모드 monotonic timestamp 시퀀스를 건드릴 위험이 있어 여기서는 하지 않음.
    try:
        manifest = _load_model_manifest()
        model_ok = hands_detector is not None and os.path.exists(MODEL_PATH)
    except Exception:
        manifest = {}
        model_ok = False

    return {
        "status": "ok" if model_ok else "degraded",
        "service": "video_engine",
        "model_version": manifest.get("version"),
        "model_loaded": model_ok,
    }


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
                # log_event("ERROR", session_id, "frame_forward_failed", {"error": "invalid_json"})  # 로깅 이전: 시스템 장애가 아닌데도 ERROR였음
                log_event("WARNING", session_id, "invalid_frame_message", {"error": "invalid_json"})  # 로깅: 클라이언트가 보낸 잘못된 데이터라 WARNING으로 하향
                continue

            session_id = message.get("session_id", session_id)
            trace_id = message.get("trace_id")  # 로깅: Container A가 프레임마다 발급한 추적 ID (없으면 None)
            if message.get("type") != "frame":
                continue  # frame 타입이 아닌 메시지는 무시

            # log_event("INFO", session_id, "frame_received", {})  # 로깅 이전: 프레임마다(초당 수십회) INFO로 찍혀서 로그가 순식간에 불어남
            log_event("DEBUG", session_id, "frame_received", {}, trace_id)  # 로깅: 운영 중엔 안 보이고 디버깅할 때만 LOG_LEVEL=DEBUG로 확인

            # 디코딩/검출 중 어떤 예외가 나도 크래시 대신 미검출로 처리
            try:
                image = decode_base64_frame(message.get("frame", ""))
                detected, landmarks = extract_landmarks(image)
            except Exception as exc:
                detected, landmarks = False, []
                # log_event("ERROR", session_id, "hand_not_detected", {"error": str(exc)})  # 로깅 이전: 정상적인 "미검출" 케이스와 이벤트명이 같아서 혼동됨
                log_event("ERROR", session_id, "hand_detection_error", {"error": str(exc)}, trace_id)  # 로깅: 진짜 예외는 별도 이벤트명으로 분리

            now = time.time()
            if detected:
                # log_event("INFO", session_id, "landmarks_extracted", {"detected": True, "count": len(landmarks)})  # 로깅 이전: 프레임마다 INFO로 찍힘
                log_event("DEBUG", session_id, "landmarks_extracted", {"detected": True, "count": len(landmarks)}, trace_id)  # 로깅: 손 검출도 프레임마다 발생하는 빈번한 이벤트라 DEBUG로
                _note_hand_seen(session_id, now)
            else:
                # log_event("INFO", session_id, "hand_not_detected", {"detected": False})  # 로깅 이전: "손 없음"은 정상 케이스인데 INFO+예외와 이벤트명 공유
                log_event("DEBUG", session_id, "no_hand_in_frame", {"detected": False}, trace_id)  # 로깅: 정상적인 미검출은 DEBUG + 별도 이벤트명(hand_detection_error와 구분)
                # 로깅: "연속 미검출" 경계 사례 - 일정 시간 넘게 계속 미검출이면 WARNING으로 1회 격상
                _check_prolonged_no_hand(session_id, now, trace_id)

            landmarks_message = {
                "session_id": session_id,
                "type": "landmarks",
                "detected": detected,
                "landmarks": landmarks,
                "ts": int(time.time() * 1000),
                "trace_id": trace_id,  # 로깅: Container A에게 그대로 되돌려줘서 A→C 구간까지 같은 ID로 추적 가능하게 함
            }
            # await motion_client.send(landmarks_message, session_id)  # 버그수정: 중복 연산의 원인이라 제거 (위 클래스 제거 사유 참고)
            try:
                await websocket.send_text(json.dumps(landmarks_message))
            except Exception:
                pass

    except WebSocketDisconnect:
        log_event("INFO", session_id, "ws_disconnected", {"peer": "container_a"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
