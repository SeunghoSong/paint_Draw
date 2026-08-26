"""
Container B <-> Container C(Motion_Engine) 연동 확인용 웹캠 테스트 스크립트.

webcam_test.py와 달리 여기서는 검출한 랜드마크를 실제로 Motion_Engine의
WebSocket(/ws)에 전송하고, 돌아온 제스처 판별 결과(action/x/y 등)를 받아
화면에 표시한다. app.py의 extract_landmarks()를 그대로 재사용하므로
Container B 쪽 로직은 서비스 코드와 동일하게 보장된다.

사전 준비: Motion_Engine이 먼저 떠 있어야 한다.
  - docker compose: docker compose up --build motion_engine
  - 로컬 실행: (Motion_Engine 폴더에서) python app.py

실행: python webcam_motion_test.py
종료: 영상 창에서 'q' 키
"""

import asyncio
import json
import os
import time

import cv2
import websockets

from app import extract_landmarks

WINDOW_NAME = "Video Engine <-> Motion Engine (press q to quit)"
SESSION_ID = "poc-001"

# 이 스크립트는 호스트(노트북)에서 직접 실행되므로 도커 네트워크 서비스명이 아니라
# localhost 기준 기본값을 사용한다. docker-compose가 8002:8002로 포트를 매핑해준다.
MOTION_ENGINE_WS_URL = os.environ.get("MOTION_ENGINE_WS_URL", "ws://localhost:8002/ws")

# action 값에 따라 커서 색을 다르게 표시 (Motion_Engine의 6대 제스처 규칙 기준)
ACTION_COLORS = {
    "DRAW": (0, 255, 0),
    "ERASE": (0, 165, 255),
    "ZOOM_IN": (255, 0, 0),
    "ZOOM_OUT": (255, 0, 255),
    "PAN": (0, 255, 255),
    "HOVER": (200, 200, 200),
    "NONE": (128, 128, 128),
}


async def main():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("웹캠을 열 수 없습니다. 카메라 연결/권한을 확인하세요.")
        return

    # 실제 서비스와 동일한 조건에서 테스트하기 위해 스펙 기준 해상도(최대 640x480)로 설정
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    print(f"Motion_Engine 연결 시도: {MOTION_ENGINE_WS_URL}")
    try:
        ws = await websockets.connect(MOTION_ENGINE_WS_URL, open_timeout=5)
    except Exception as exc:
        print(f"Motion_Engine에 연결할 수 없습니다: {exc}")
        print("Motion_Engine을 먼저 실행하세요 (예: docker compose up --build motion_engine).")
        cap.release()
        return

    print("연결 성공. 웹캠 테스트 시작 (종료: 영상 창에서 q)")

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("프레임을 읽지 못했습니다.")
                break

            detected, landmarks = extract_landmarks(frame)
            h, w = frame.shape[:2]

            if detected:
                for x, y in landmarks:
                    cx, cy = int(x * w), int(y * h)
                    cv2.circle(frame, (cx, cy), 4, (0, 255, 0), -1)

            # Container B -> C 로 실제 전달되는 것과 동일한 포맷으로 전송
            landmarks_msg = {
                "session_id": SESSION_ID,
                "type": "landmarks",
                "detected": detected,
                "landmarks": landmarks,
                "ts": int(time.time() * 1000),
            }

            try:
                await ws.send(json.dumps(landmarks_msg))
                raw_reply = await ws.recv()
                gesture = json.loads(raw_reply)
            except Exception as exc:
                print(f"Motion_Engine 통신 중 오류(연결 종료됨): {exc}")
                break

            print(json.dumps(gesture, ensure_ascii=False))

            action = gesture.get("action", "NONE")
            color = ACTION_COLORS.get(action, (255, 255, 255))

            # Motion_Engine이 돌려준 커서 좌표(0~1 정규화)를 화면 픽셀 위치로 변환해 표시
            gx, gy = gesture.get("x", 0.5), gesture.get("y", 0.5)
            cv2.circle(frame, (int(gx * w), int(gy * h)), 10, color, 2)
            cv2.putText(
                frame,
                f"action: {action}",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                color,
                2,
            )
            cv2.putText(
                frame,
                "Hand detected" if detected else "No hand",
                (10, 60),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0) if detected else (0, 0, 255),
                2,
            )

            cv2.imshow(WINDOW_NAME, frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()
        await ws.close()


if __name__ == "__main__":
    asyncio.run(main())
