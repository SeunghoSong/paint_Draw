import math
import time
from collections import deque
from typing import List, Dict, Any, Optional
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(
    title="Air Canvas - Motion Engine (Container C)",
    description="21개 랜드마크 분석, EMA 손떨림 보정, 획 삐침 0% 즉시 차단, 6대 제어 규칙 엔진"
)

# ==========================================================
# 📋 Pydantic 데이터 모델
# ==========================================================
class LandmarkItem(BaseModel):
    x: float
    y: float
    z: float

class LandmarkPayload(BaseModel):
    session_id: str
    landmarks: List[LandmarkItem]

# ==========================================================
# 🌟 세션별 상태 관리 클래스 (EMA + 스마트 디바운스)
# ==========================================================
class SessionState:
    def __init__(self):
        self.smooth_x: Optional[float] = None
        self.smooth_y: Optional[float] = None
        
        self.prev_pan_x: Optional[float] = None
        self.prev_pan_y: Optional[float] = None
        
        self.action_queue: deque = deque(maxlen=3)
        self.current_stable_action: str = "HOVER"
        self.last_updated: float = time.time()

sessions: Dict[str, SessionState] = {}

def get_or_create_session(session_id: str) -> SessionState:
    now = time.time()
    expired = [sid for sid, s in sessions.items() if now - s.last_updated > 600]
    for sid in expired:
        del sessions[sid]

    if session_id not in sessions:
        sessions[session_id] = SessionState()
    
    session = sessions[session_id]
    session.last_updated = now
    return session

def calculate_distance(p1: LandmarkItem, p2: LandmarkItem) -> float:
    return math.sqrt((p1.x - p2.x) ** 2 + (p1.y - p2.y) ** 2)

@app.get("/health")
def health():
    return {"status": "ok", "service": "motion_engine"}

@app.post("/gesture")
async def process_gesture(payload: LandmarkPayload):
    lm = payload.landmarks
    session_id = payload.session_id

    if not lm or len(lm) < 21:
        return {
            "action": "NONE",
            "x": 0.5,
            "y": 0.5,
            "delta": 0.0,
            "pan_dx": 0.0,
            "pan_dy": 0.0
        }

    state = get_or_create_session(session_id)

    # 1. 5개 손가락 상태 정밀 분석
    index_open = lm[8].y < lm[6].y
    
    # 🌟 중지가 조금이라도 들리면 즉시 감지하여 펜 삐침 원천 차단
    middle_open = lm[12].y < lm[10].y or (lm[12].y < lm[9].y + 0.02)
    ring_open = lm[16].y < lm[14].y
    pinky_open = lm[20].y < lm[18].y

    dist_thumb_to_palm = calculate_distance(lm[4], lm[9])
    dist_thumb_to_index_base = calculate_distance(lm[4], lm[5])
    thumb_open = (dist_thumb_to_palm > 0.15) and (dist_thumb_to_index_base > 0.12)
    thumb_folded = dist_thumb_to_palm < 0.13

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

    # 👍 [규칙 2: ZOOM_IN (화면 확대)]
    elif thumb_open and not index_open and not middle_open and not ring_open and not pinky_open:
        raw_action = "ZOOM_IN"
        raw_x = lm[4].x
        raw_y = lm[4].y
        delta = 0.008
        state.prev_pan_x = None
        state.prev_pan_y = None

    # 🤙 [규칙 3: ZOOM_OUT (화면 축소)]
    elif pinky_open and not thumb_open and not index_open and not middle_open and not ring_open:
        raw_action = "ZOOM_OUT"
        raw_x = lm[20].x
        raw_y = lm[20].y
        delta = -0.008
        state.prev_pan_x = None
        state.prev_pan_y = None

    # ✊ [규칙 4: PAN (화면 드래그)]
    elif not index_open and not middle_open and not ring_open and not pinky_open and thumb_folded:
        raw_action = "PAN"
        raw_x = palm_center_x
        raw_y = palm_center_y

        if state.prev_pan_x is not None and state.prev_pan_y is not None:
            pan_dx = - (palm_center_x - state.prev_pan_x)
            pan_dy = (palm_center_y - state.prev_pan_y)
        
        state.prev_pan_x = palm_center_x
        state.prev_pan_y = palm_center_y

    # 🧹 [규칙 5: ERASE (지우개)]
    elif index_open and middle_open and not ring_open and not pinky_open:
        raw_action = "ERASE"
        raw_x = (lm[8].x + lm[12].x) / 2.0
        raw_y = (lm[8].y + lm[12].y) / 2.0
        state.prev_pan_x = None
        state.prev_pan_y = None

    # 🖐️ [규칙 6: HOVER (손 펴기 - 즉시 0초 차단)]
    else:
        raw_action = "HOVER"
        raw_x = lm[8].x
        raw_y = lm[8].y
        state.prev_pan_x = None
        state.prev_pan_y = None

    # 3. 스마트 컷오프 디바운스 (손을 펼 땐 즉시 컷!)
    if raw_action == "HOVER" or raw_action == "ERASE" or raw_action == "PAN":
        state.current_stable_action = raw_action
        state.action_queue.clear()
    else:
        state.action_queue.append(raw_action)
        action_counts = {}
        for act in state.action_queue:
            action_counts[act] = action_counts.get(act, 0) + 1
        
        most_common_action = max(action_counts, key=action_counts.get)
        if action_counts[most_common_action] >= 2:
            state.current_stable_action = most_common_action

    final_action = state.current_stable_action

    # 4. EMA 손떨림 보정
    alpha = 0.50
    if state.smooth_x is None or state.smooth_y is None:
        state.smooth_x = raw_x
        state.smooth_y = raw_y
    else:
        state.smooth_x = alpha * raw_x + (1.0 - alpha) * state.smooth_x
        state.smooth_y = alpha * raw_y + (1.0 - alpha) * state.smooth_y

    return {
        "action": final_action,
        "x": round(float(state.smooth_x), 4),
        "y": round(float(state.smooth_y), 4),
        "delta": round(float(delta), 4),
        "pan_dx": round(float(pan_dx), 4),
        "pan_dy": round(float(pan_dy), 4)
    }
