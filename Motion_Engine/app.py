import math
import os
import time
import json
import logging
import sys
import urllib.request
import urllib.error
from collections import deque
from typing import List, Dict, Any, Optional, Union
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

# 파라미터화: 세션/디바운스 인프라값 + 제스처 판별 임계값을 환경변수로 분리
# (기본값은 기존 하드코딩 값과 동일하므로 .env 미설정 시 동작 변화 없음)
DEFAULT_SESSION_ID = os.environ.get("DEFAULT_SESSION_ID", "poc-001")
SESSION_EXPIRY_SECONDS = int(os.environ.get("SESSION_EXPIRY_SECONDS", "600"))
ACTION_QUEUE_MAXLEN = int(os.environ.get("ACTION_QUEUE_MAXLEN", "3"))
ACTION_STABLE_MIN_COUNT = int(os.environ.get("ACTION_STABLE_MIN_COUNT", "2"))

GESTURE_THUMB_PALM_OPEN_THRESHOLD = float(os.environ.get("GESTURE_THUMB_PALM_OPEN_THRESHOLD", "0.15"))
GESTURE_THUMB_INDEX_BASE_OPEN_THRESHOLD = float(os.environ.get("GESTURE_THUMB_INDEX_BASE_OPEN_THRESHOLD", "0.12"))
GESTURE_THUMB_FOLDED_THRESHOLD = float(os.environ.get("GESTURE_THUMB_FOLDED_THRESHOLD", "0.13"))
GESTURE_ERASE_HEIGHT_DIFF_THRESHOLD = float(os.environ.get("GESTURE_ERASE_HEIGHT_DIFF_THRESHOLD", "0.12"))
GESTURE_ZOOM_DELTA = float(os.environ.get("GESTURE_ZOOM_DELTA", "0.008"))

EMA_MICRO_MOVE_THRESHOLD = float(os.environ.get("EMA_MICRO_MOVE_THRESHOLD", "0.001"))
EMA_ALPHA_MICRO = float(os.environ.get("EMA_ALPHA_MICRO", "0.35"))
EMA_PRECISE_MOVE_THRESHOLD = float(os.environ.get("EMA_PRECISE_MOVE_THRESHOLD", "0.05"))
EMA_ALPHA_PRECISE = float(os.environ.get("EMA_ALPHA_PRECISE", "0.50"))
EMA_ALPHA_FAST = float(os.environ.get("EMA_ALPHA_FAST", "0.85"))

app = FastAPI(
    title="Air Canvas - Motion Engine (Container C)",
    description="21개 랜드마크 분석, EMA 손떨림 보정, 6대 제어 규칙 엔진 (WebSocket & HTTP 하이브리드 지원)"
)

# ---- 공통 로깅 설정 ----
logger = logging.getLogger("motion_engine")
# logger.setLevel(logging.INFO)  # 로깅 이전: 레벨 고정값 (환경변수로 제어 불가능했음)
# 로깅: LOG_LEVEL 환경변수로 운영/개발 전환 (기본 INFO). Video_Engine과 동일한 패턴.
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
if not logger.handlers:
    logger.addHandler(logging.StreamHandler(sys.stdout))
logger.propagate = False

# 로깅: level 문자열에 맞는 logger 메서드로 실제 디스패치 (Video_Engine과 동일한 이유 - 기존엔 항상 info로만 출력됐음)
_LOG_METHODS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}

def log_event(level: str, session_id: str, event: str, detail: dict = None, trace_id: str = None) -> None:
    """trace_id: 프레임 1개가 A→B→(A→C)로 흘러가는 과정을 추적하는 요청 단위 ID (session_id와는 다른 축)."""
    record = {
        "ts": int(time.time() * 1000),
        "container": "C",
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
# 실제 채널/웹훅은 팀이 정해서 넣어야 하는 값이라 여기서 아무 것도 지어내지 않음. (Video_Engine과 동일)
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
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
    """디바운싱/그룹핑: 임계치를 넘는 순간에만 1번 보내고, 창이 갈릴 때만 다시 셈."""
    global _error_window_start, _error_window_count

    if not SLACK_WEBHOOK_URL or level.upper() != "ERROR":
        return

    now = time.time()
    if now - _error_window_start > ALERT_ERROR_WINDOW_SECONDS:
        _error_window_start = now
        _error_window_count = 0
    _error_window_count += 1

    if _error_window_count == ALERT_ERROR_THRESHOLD:
        _send_slack_alert(
            f":rotating_light: [Motion_Engine] 최근 {int(ALERT_ERROR_WINDOW_SECONDS)}초 동안 "
            f"ERROR {ALERT_ERROR_THRESHOLD}회 발생 (마지막 이벤트: {event})"
        )

# ==========================================================
# 📋 Pydantic 데이터 모델 (HTTP POST용)
# ==========================================================
class LandmarkItem(BaseModel):
    x: float
    y: float
    z: float = 0.0

class LandmarkPayload(BaseModel):
    session_id: str
    landmarks: List[Union[LandmarkItem, List[float], Dict[str, float]]]
    trace_id: Optional[str] = None  # 로깅: Web_Server가 프레임 단위로 발급/전달하는 추적 ID (없어도 동작)

# ==========================================================
# 🌟 세션별 상태 관리 클래스 (EMA + 3프레임 디바운스)
# ==========================================================
class SessionState:
    def __init__(self):
        self.smooth_x: Optional[float] = None
        self.smooth_y: Optional[float] = None
        
        self.prev_pan_x: Optional[float] = None
        self.prev_pan_y: Optional[float] = None
        
        # self.action_queue: deque = deque(maxlen=3)  # 파라미터화 이전 하드코딩 값
        self.action_queue: deque = deque(maxlen=ACTION_QUEUE_MAXLEN)  # 파라미터화: 환경변수 사용
        self.current_stable_action: str = "HOVER"
        self.last_updated: float = time.time()

sessions: Dict[str, SessionState] = {}

def get_or_create_session(session_id: str) -> SessionState:
    now = time.time()
    # expired = [sid for sid, s in sessions.items() if now - s.last_updated > 600]  # 파라미터화 이전 하드코딩 값
    expired = [sid for sid, s in sessions.items() if now - s.last_updated > SESSION_EXPIRY_SECONDS]  # 파라미터화: 환경변수 사용
    for sid in expired:
        del sessions[sid]

    if session_id not in sessions:
        sessions[session_id] = SessionState()
    
    session = sessions[session_id]
    session.last_updated = now
    return session

class Point:
    def __init__(self, x: float, y: float, z: float = 0.0):
        self.x = x
        self.y = y
        self.z = z

def parse_landmarks(raw_landmarks: list) -> List[Point]:
    """2차원 리스트 [[x, y], ...] 또는 딕셔너리 [{'x':x, 'y':y}, ...]를 Point 객체 배열로 변환"""
    points = []
    for item in raw_landmarks:
        if isinstance(item, (list, tuple)):
            x = float(item[0])
            y = float(item[1])
            z = float(item[2]) if len(item) > 2 else 0.0
            points.append(Point(x, y, z))
        elif isinstance(item, dict):
            points.append(Point(float(item.get("x", 0.0)), float(item.get("y", 0.0)), float(item.get("z", 0.0))))
        elif hasattr(item, "x") and hasattr(item, "y"):
            points.append(Point(float(item.x), float(item.y), float(getattr(item, "z", 0.0))))
    return points

def calculate_distance(p1: Point, p2: Point) -> float:
    return math.sqrt((p1.x - p2.x) ** 2 + (p1.y - p2.y) ** 2)

def compute_gesture_logic(session_id: str, raw_landmarks: list) -> dict:
    """핵심 제스처 연산, EMA 보정, 디바운스 로직 (HTTP 및 WebSocket 공통 실행)"""
    lm = parse_landmarks(raw_landmarks)

    if not lm or len(lm) < 21:
        return {
            "session_id": session_id,
            "action": "NONE",
            "x": 0.5,
            "y": 0.5,
            "delta": 0.0,
            "pan_dx": 0.0,
            "pan_dy": 0.0,
            "detected": False
        }

    state = get_or_create_session(session_id)

    # 1. 5개 손가락 상태 정밀 분석
    index_open = lm[8].y < lm[6].y
    middle_open = lm[12].y < lm[10].y
    ring_open = lm[16].y < lm[14].y
    pinky_open = lm[20].y < lm[18].y

    dist_thumb_to_palm = calculate_distance(lm[4], lm[9])
    dist_thumb_to_index_base = calculate_distance(lm[4], lm[5])
    # thumb_open = (dist_thumb_to_palm > 0.15) and (dist_thumb_to_index_base > 0.12)  # 파라미터화 이전 하드코딩 값
    # thumb_folded = dist_thumb_to_palm < 0.13  # 파라미터화 이전 하드코딩 값
    thumb_open = (dist_thumb_to_palm > GESTURE_THUMB_PALM_OPEN_THRESHOLD) and (dist_thumb_to_index_base > GESTURE_THUMB_INDEX_BASE_OPEN_THRESHOLD)  # 파라미터화: 환경변수 사용
    thumb_folded = dist_thumb_to_palm < GESTURE_THUMB_FOLDED_THRESHOLD  # 파라미터화: 환경변수 사용

    palm_center_x = (lm[0].x + lm[9].x) / 2.0
    palm_center_y = (lm[0].y + lm[9].y) / 2.0

    # 2. 6대 제어 규칙 판별
    raw_action = "HOVER"
    raw_x = lm[8].x
    raw_y = lm[8].y
    delta = 0.0
    pan_dx = 0.0
    pan_dy = 0.0

    # 🖊️ [규칙 1: DRAW (펜 모드)] - 검지만 펴지고 나머지 모두 접힘
    if index_open and not middle_open and not ring_open and not pinky_open and not thumb_open:
        raw_action = "DRAW"
        raw_x = lm[8].x
        raw_y = lm[8].y
        state.prev_pan_x = None
        state.prev_pan_y = None

    # 🧹 [규칙 2: ERASE (지우개)] - 의도된 브이(✌️) 제스처
    elif index_open and middle_open and not ring_open and not pinky_open and not thumb_open:
        height_diff = abs(lm[8].y - lm[12].y)
        # if height_diff < 0.12:  # 파라미터화 이전 하드코딩 값
        if height_diff < GESTURE_ERASE_HEIGHT_DIFF_THRESHOLD:  # 파라미터화: 환경변수 사용
            raw_action = "ERASE"
            raw_x = (lm[8].x + lm[12].x) / 2.0
            raw_y = (lm[8].y + lm[12].y) / 2.0
        else:
            raw_action = "HOVER"
        state.prev_pan_x = None
        state.prev_pan_y = None

    # 👍 [규칙 3: ZOOM_IN (화면 확대)] - 주먹 쥐고 엄지만 폄
    elif thumb_open and not index_open and not middle_open and not ring_open and not pinky_open:
        raw_action = "ZOOM_IN"
        raw_x = lm[4].x
        raw_y = lm[4].y
        # delta = 0.008  # 파라미터화 이전 하드코딩 값
        delta = GESTURE_ZOOM_DELTA  # 파라미터화: 환경변수 사용
        state.prev_pan_x = None
        state.prev_pan_y = None

    # 🤙 [규칙 4: ZOOM_OUT (화면 축소)] - 주먹 쥐고 새끼손가락만 폄
    elif pinky_open and not thumb_open and not index_open and not middle_open and not ring_open:
        raw_action = "ZOOM_OUT"
        raw_x = lm[20].x
        raw_y = lm[20].y
        # delta = -0.008  # 파라미터화 이전 하드코딩 값
        delta = -GESTURE_ZOOM_DELTA  # 파라미터화: 환경변수 사용
        state.prev_pan_x = None
        state.prev_pan_y = None

    # ✊ [규칙 5: PAN (화면 드래그)] - 5개 손가락 모두 쥔 주먹
    elif not index_open and not middle_open and not ring_open and not pinky_open and thumb_folded:
        raw_action = "PAN"
        raw_x = palm_center_x
        raw_y = palm_center_y

        if state.prev_pan_x is not None and state.prev_pan_y is not None:
            pan_dx = - (palm_center_x - state.prev_pan_x)
            pan_dy = (palm_center_y - state.prev_pan_y)
        
        state.prev_pan_x = palm_center_x
        state.prev_pan_y = palm_center_y

    # 🖐️ [규칙 6: HOVER (대기 모드)]
    else:
        raw_action = "HOVER"
        raw_x = lm[8].x
        raw_y = lm[8].y
        state.prev_pan_x = None
        state.prev_pan_y = None

    # 3. 🌟 비대칭 무지연 펜-업 디바운스 필터 (Asymmetric Instant Cutoff)
    # 손을 펴는 순간(DRAW -> 비DRAW)에는 다수결 지연 없이 0초 만에 칼같이 펜-업하여 한자 삐침 100% 차단!
    if state.current_stable_action == "DRAW" and raw_action != "DRAW":
        state.action_queue.clear()
        state.action_queue.append(raw_action)
        state.current_stable_action = raw_action
    else:
        state.action_queue.append(raw_action)
        action_counts = {}
        for act in state.action_queue:
            action_counts[act] = action_counts.get(act, 0) + 1
        
        most_common_action = max(action_counts, key=action_counts.get)
        # if action_counts[most_common_action] >= 2:  # 파라미터화 이전 하드코딩 값
        if action_counts[most_common_action] >= ACTION_STABLE_MIN_COUNT:  # 파라미터화: 환경변수 사용
            state.current_stable_action = most_common_action

    final_action = state.current_stable_action

    # 4. 🌟 속도 적응형 최적화 EMA 손떨림 보정 (끊김 없는 고속 반응 튜닝)
    if state.smooth_x is None or state.smooth_y is None:
        state.smooth_x = raw_x
        state.smooth_y = raw_y
    else:
        # 손가락 이동 거리(속도) 계산
        move_dist = math.sqrt((raw_x - state.smooth_x) ** 2 + (raw_y - state.smooth_y) ** 2)
        
        # 1) 초미세 진동 필터 (0.001 이하 미세 떨림만 부드럽게 흡수)
        # if move_dist < 0.001:      # 파라미터화 이전 하드코딩 값
        #     alpha = 0.35
        # elif move_dist < 0.05:
        #     alpha = 0.50
        # else:
        #     alpha = 0.85
        if move_dist < EMA_MICRO_MOVE_THRESHOLD:  # 파라미터화: 환경변수 사용
            alpha = EMA_ALPHA_MICRO
        # 2) 정밀 글씨 쓰기 / 드로잉 구간 (alpha = 0.50)
        elif move_dist < EMA_PRECISE_MOVE_THRESHOLD:
            alpha = EMA_ALPHA_PRECISE
        # 3) 빠른 이동 구간 (alpha = 0.85, 렉/지연 0%)
        else:
            alpha = EMA_ALPHA_FAST

        state.smooth_x = alpha * raw_x + (1.0 - alpha) * state.smooth_x
        state.smooth_y = alpha * raw_y + (1.0 - alpha) * state.smooth_y

    return {
        "session_id": session_id,
        "type": "gesture",
        "action": final_action,
        "x": round(float(state.smooth_x), 4),
        "y": round(float(state.smooth_y), 4),
        "delta": round(float(delta), 4),
        "pan_dx": round(float(pan_dx), 4),
        "pan_dy": round(float(pan_dy), 4),
        "landmarks": raw_landmarks,
        "detected": True
    }

# ==========================================================
# 🩺 헬스체크 엔드포인트
# ==========================================================
# 예외처리: 헬스체크 전용 세션ID/더미 랜드마크. 실제 세션과 절대 충돌하지 않도록 예약어로 사용하고,
# 검사 후 즉시 sessions에서 제거해 idle 세션 집계에 영향을 주지 않는다.
_HEALTHCHECK_SESSION_ID = "__healthcheck__"
_HEALTHCHECK_LANDMARKS = [[0.5, 0.5] for _ in range(21)]


@app.get("/health")
def health():
    # 예외처리: 단순 "프로세스 생존"이 아니라 제스처 연산 파이프라인이 실제로 동작하는지까지 확인.
    # ML 모델이 없는 순수 연산이라 실제 세션에 영향 없이 저비용으로 검증 가능.
    try:
        result = compute_gesture_logic(_HEALTHCHECK_SESSION_ID, _HEALTHCHECK_LANDMARKS)
        pipeline_ok = "action" in result
    except Exception:
        pipeline_ok = False
    finally:
        sessions.pop(_HEALTHCHECK_SESSION_ID, None)

    return {
        "status": "ok" if pipeline_ok else "degraded",
        "service": "motion_engine",
        "pipeline_ok": pipeline_ok,
    }

# ==========================================================
# 🌐 1. WebSocket 엔드포인트 (2번 Video_Engine 전용 초고속 연결)
# ==========================================================
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    # session_id = "default"  # 파라미터화 이전 값 (Web_Server/Video_Engine과 세션ID 기본값이 달랐던 불일치 버그)
    session_id = DEFAULT_SESSION_ID  # 파라미터화: 다른 컨테이너와 동일한 환경변수로 통일
    log_event("INFO", session_id, "ws_connected", {"peer": "video_engine"})

    try:
        while True:
            raw_text = await websocket.receive_text()
            try:
                msg = json.loads(raw_text)
            except json.JSONDecodeError:
                continue

            session_id = msg.get("session_id", session_id)
            trace_id = msg.get("trace_id")  # 로깅: webcam_motion_test.py 등이 실어 보내면 사용, 없으면 None
            msg_type = msg.get("type", "")

            if msg_type == "landmarks":
                detected = msg.get("detected", False)
                landmarks = msg.get("landmarks", [])

                if detected and landmarks:
                    result = compute_gesture_logic(session_id, landmarks)
                else:
                    result = {
                        "session_id": session_id,
                        "type": "gesture",
                        "action": "HOVER",
                        "x": 0.5,
                        "y": 0.5,
                        "delta": 0.0,
                        "pan_dx": 0.0,
                        "pan_dy": 0.0,
                        "detected": False
                    }

                # log_event("INFO", session_id, "gesture_computed", {"action": result["action"]})  # 로깅 이전: 프레임마다(초당 수십회) INFO로 찍힘
                # 로깅: transport 필드로 WS/HTTP 두 경로를 같은 이벤트명 아래 구분 (HTTP 쪽도 아래서 동일하게 기록)
                log_event("DEBUG", session_id, "gesture_computed", {"action": result["action"], "transport": "ws"}, trace_id)  # 로깅: 운영 중엔 안 보이고 디버깅할 때만 확인
                await websocket.send_text(json.dumps(result, ensure_ascii=False))

    except WebSocketDisconnect:
        log_event("INFO", session_id, "ws_disconnected", {"peer": "video_engine"})

# ==========================================================
# 🚀 2. HTTP POST 엔드포인트 (기존 규격 및 단독 테스트 호환)
# ==========================================================
@app.post("/gesture")
async def process_gesture_http(payload: LandmarkPayload):
    # 로깅: 이 경로엔 원래 로그가 전혀 없었음 (Web_Server가 실제로 매 프레임 호출하는 경로인데도).
    # 실제 사용되는 유일한 프로덕션 경로이므로, WS 경로와 대칭으로 gesture_computed를 남긴다.
    result = compute_gesture_logic(payload.session_id, payload.landmarks)
    log_event("DEBUG", payload.session_id, "gesture_computed", {"action": result["action"], "transport": "http"}, payload.trace_id)
    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
