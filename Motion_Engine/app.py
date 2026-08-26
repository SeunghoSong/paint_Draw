import math
import time
from collections import deque
from typing import List, Dict, Any, Optional
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(
    title="Air Canvas - Motion Engine (Container C)",
    description="21개 랜드마크 분석, 지수 이동 평균(EMA) 손떨림 보정, 디바운스, 6대 제어 규칙 판별 엔진"
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
# 🌟 세션별 상태 관리 클래스 (EMA + 디바운스 + 선 끊김 방지)
# ==========================================================
class SessionState:
    def __init__(self):
        # 1. 손떨림 보정 좌표 (EMA)
        self.smooth_x: Optional[float] = None
        self.smooth_y: Optional[float] = None
        
        # 2. 선 끊김 방지용 유예(Grace Period) 카운터
        self.draw_grace_frames: int = 0
        self.is_currently_drawing: bool = False
        
        # 3. ✊ 주먹 이동(Pan) 이전 위치
        self.prev_pan_x: Optional[float] = None
        self.prev_pan_y: Optional[float] = None
        
        # 4. 모드 전환 디바운스 큐 (3프레임 다수결)
        self.action_queue: deque = deque(maxlen=3)
        self.current_stable_action: str = "HOVER"
        
        # 5. 세션 마지막 갱신 시간 (가비지 컬렉션용)
        self.last_updated: float = time.time()

sessions: Dict[str, SessionState] = {}

def get_or_create_session(session_id: str) -> SessionState:
    """10분 이상 지난 유휴 세션 메모리 자동 정리 및 세션 상태 반환"""
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
    """두 랜드마크 간의 2D 유클리드 거리 계산"""
    return math.sqrt((p1.x - p2.x) ** 2 + (p1.y - p2.y) ** 2)

# ==========================================================
# 🩺 헬스체크 엔드포인트 (팀 프로젝트 공통 스펙 준수)
# ==========================================================
@app.get("/health")
def health():
    return {"status": "ok", "service": "motion_engine"}

# ==========================================================
# 🚀 메인 모션 인식 엔드포인트
# ==========================================================
@app.post("/gesture")
async def process_gesture(payload: LandmarkPayload):
    lm = payload.landmarks
    session_id = payload.session_id

    # 랜드마크가 유효하지 않은 경우 안전 기본값 반환
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

    # ----------------------------------------------------------
    # 1단계: 5개 손가락 펴짐/접힘 기하학 상태 정밀 분석
    # ----------------------------------------------------------
    index_open = lm[8].y < lm[6].y       # 검지 펴짐 여부
    middle_open = lm[12].y < lm[10].y    # 중지 펴짐 여부
    ring_open = lm[16].y < lm[14].y      # 약지 펴짐 여부
    pinky_open = lm[20].y < lm[18].y     # 새끼 펴짐 여부

    dist_thumb_to_palm = calculate_distance(lm[4], lm[9])
    dist_thumb_to_index_base = calculate_distance(lm[4], lm[5])
    thumb_open = (dist_thumb_to_palm > 0.15) and (dist_thumb_to_index_base > 0.12)
    thumb_folded = dist_thumb_to_palm < 0.13

    palm_center_x = (lm[0].x + lm[9].x) / 2.0
    palm_center_y = (lm[0].y + lm[9].y) / 2.0

    # ----------------------------------------------------------
    # 2단계: 6대 제어 규칙 판별
    # ----------------------------------------------------------
    raw_action = "HOVER"
    raw_x = lm[8].x
    raw_y = lm[8].y
    delta = 0.0
    pan_dx = 0.0
    pan_dy = 0.0

    # 🖊️ [규칙 1: DRAW (펜 그리기 모드)] - 검지만 펴지고 나머지 모두 접힘
    if index_open and not middle_open and not ring_open and not pinky_open and not thumb_open:
        raw_action = "DRAW"
        raw_x = lm[8].x
        raw_y = lm[8].y
        state.is_currently_drawing = True
        state.draw_grace_frames = 3  # 3프레임 동안 미세 깜빡임 유예
        state.prev_pan_x = None
        state.prev_pan_y = None

    # 👍 [규칙 2: ZOOM_IN (화면 천천히 확대)] - 주먹 쥐고 엄지만 폄 (엄지 척)
    elif thumb_open and not index_open and not middle_open and not ring_open and not pinky_open:
        raw_action = "ZOOM_IN"
        raw_x = lm[4].x
        raw_y = lm[4].y
        delta = 0.008  # 부드러운 저감도 줌 속도
        state.is_currently_drawing = False
        state.draw_grace_frames = 0
        state.prev_pan_x = None
        state.prev_pan_y = None

    # 🤙 [규칙 3: ZOOM_OUT (화면 천천히 축소)] - 주먹 쥐고 새끼손가락만 폄
    elif pinky_open and not thumb_open and not index_open and not middle_open and not ring_open:
        raw_action = "ZOOM_OUT"
        raw_x = lm[20].x
        raw_y = lm[20].y
        delta = -0.008  # 부드러운 저감도 줌 속도
        state.is_currently_drawing = False
        state.draw_grace_frames = 0
        state.prev_pan_x = None
        state.prev_pan_y = None

    # ✊ [규칙 4: PAN (화면 드래그 이동)] - 5개 손가락 모두 쥔 주먹
    elif not index_open and not middle_open and not ring_open and not pinky_open and thumb_folded:
        raw_action = "PAN"
        raw_x = palm_center_x
        raw_y = palm_center_y
        state.is_currently_drawing = False
        state.draw_grace_frames = 0

        if state.prev_pan_x is not None and state.prev_pan_y is not None:
            pan_dx = - (palm_center_x - state.prev_pan_x)
            pan_dy = (palm_center_y - state.prev_pan_y)
        
        state.prev_pan_x = palm_center_x
        state.prev_pan_y = palm_center_y

    # 🧹 [규칙 5: ERASE (지우개 모드)] - 검지와 중지 2개 펴짐
    elif index_open and middle_open and not ring_open and not pinky_open:
        raw_action = "ERASE"
        raw_x = (lm[8].x + lm[12].x) / 2.0
        raw_y = (lm[8].y + lm[12].y) / 2.0
        state.is_currently_drawing = False
        state.draw_grace_frames = 0
        state.prev_pan_x = None
        state.prev_pan_y = None

    # 🖐️ [규칙 6: HOVER (대기 모드)] - 손바닥 전체 펴기 또는 기타 상태
    else:
        if state.draw_grace_frames > 0 and index_open and not middle_open:
            state.draw_grace_frames -= 1
            raw_action = "DRAW"
            raw_x = lm[8].x
            raw_y = lm[8].y
        else:
            raw_action = "HOVER"
            raw_x = lm[8].x
            raw_y = lm[8].y
            state.is_currently_drawing = False
            state.draw_grace_frames = 0

        state.prev_pan_x = None
        state.prev_pan_y = None

    # ----------------------------------------------------------
    # 3단계: [디바운스 필터 (Debounce)]
    # ----------------------------------------------------------
    state.action_queue.append(raw_action)
    action_counts = {}
    for act in state.action_queue:
        action_counts[act] = action_counts.get(act, 0) + 1
    
    most_common_action = max(action_counts, key=action_counts.get)
    if action_counts[most_common_action] >= 2:
        state.current_stable_action = most_common_action

    final_action = state.current_stable_action

    # ----------------------------------------------------------
    # 4단계: [손떨림 보정 (Exponential Moving Average, EMA)]
    # ----------------------------------------------------------
    alpha = 0.45
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
