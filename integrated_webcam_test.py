"""
🎉 Video_Engine(2번) + Motion_Engine(3번) 초고화질/고정밀 웹캠 통합 테스트 도구

원인 해결:
1. MediaPipe 입력 전 불필요한 이미지 왜곡 제거 (원본 프레임으로 AI 추론)
2. 거울 뷰(1 - x) 정밀 보정
3. 부드러운 30FPS 실시간 에어 드로잉 지원
"""

import sys
import os
import time
import cv2
import numpy as np

# 2번, 3번 모듈 경로 추가
sys.path.append(os.path.join(os.path.dirname(__file__), "Video_Engine"))
sys.path.append(os.path.join(os.path.dirname(__file__), "Motion_Engine"))

from Video_Engine.app import extract_landmarks
from Motion_Engine.app import compute_gesture_logic

WINDOW_NAME = "Paint Draw - Video(2) + Motion(3) Integrated Test (Press Q to quit)"
SESSION_ID = "test-session-01"

HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (0, 9), (9, 10), (10, 11), (11, 12),
    (0, 13), (13, 14), (14, 15), (15, 16),
    (0, 17), (17, 18), (18, 19), (19, 20),
    (5, 9), (9, 13), (13, 17), (0, 17)
]

def main():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("❌ 웹캠을 열 수 없습니다. 카메라 연결 및 권한을 확인하세요.")
        return

    # 안정적인 640x480 해상도 세팅
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    print("=" * 60)
    print("🚀 Video_Engine(2번) + Motion_Engine(3번) 초정밀 통합 테스트")
    print("👉 검지만 펴기: 🖊️ DRAW (그리기)")
    print("👉 검지+중지(V): 🧹 ERASE (지우개)")
    print("👉 5개 모두 주먹: ✊ PAN (화면 이동)")
    print("👉 엄지 척: 👍 ZOOM_IN (확대)")
    print("👉 새끼손가락: 🤙 ZOOM_OUT (축소)")
    print("👉 손바닥 펴기: 🖐️ HOVER (대기)")
    print("=" * 60)

    drawing_canvas = None
    last_draw_pt = None

    while True:
        ok, raw_frame = cap.read()
        if not ok:
            break

        # -------------------------------------------------------------
        # 1. 2번 비전 엔진(Video_Engine)에 원본 프레임 전달하여 AI 추론
        # -------------------------------------------------------------
        detected, raw_landmarks = extract_landmarks(raw_frame)

        # 화면 표시는 거울 모드로 반전
        frame = cv2.flip(raw_frame, 1)
        h, w = frame.shape[:2]

        if drawing_canvas is None:
            drawing_canvas = np.zeros((h, w, 3), dtype=np.uint8)

        action_text = "HOVER"
        action_color = (180, 180, 180)
        smooth_pt = None

        if detected and len(raw_landmarks) == 21:
            # ---------------------------------------------------------
            # 2. 3번 모션 엔진(Motion_Engine)으로 제스처 계산 & EMA 보정
            # ---------------------------------------------------------
            result = compute_gesture_logic(SESSION_ID, raw_landmarks)
            action = result["action"]
            sx = result["x"]
            sy = result["y"]
            
            # 거울 뷰에 맞게 X 좌표 반전 (1 - sx)
            screen_x = int((1 - sx) * w)
            screen_y = int(sy * h)
            smooth_pt = (screen_x, screen_y)

            # 3. 손 뼈대 시각화 (거울 좌표 적용)
            mirrored_landmarks = [[(1 - lm[0]), lm[1]] for lm in raw_landmarks]

            for i, j in HAND_CONNECTIONS:
                p1 = (int(mirrored_landmarks[i][0] * w), int(mirrored_landmarks[i][1] * h))
                p2 = (int(mirrored_landmarks[j][0] * w), int(mirrored_landmarks[j][1] * h))
                cv2.line(frame, p1, p2, (217, 70, 239), 2)

            for idx, lm in enumerate(mirrored_landmarks):
                pt = (int(lm[0] * w), int(lm[1] * h))
                is_tip = idx in [4, 8, 12, 16, 20]
                radius = 6 if is_tip else 4
                cv2.circle(frame, pt, radius, (236, 72, 153), -1)

            # 4. 6대 액션 실시간 반응
            if action == "DRAW":
                action_text = "DRAW (펜 그리기)"
                action_color = (255, 191, 0)  # Cyan
                if last_draw_pt is not None and smooth_pt is not None:
                    cv2.line(drawing_canvas, last_draw_pt, smooth_pt, (0, 255, 255), 5)
                last_draw_pt = smooth_pt
                cv2.circle(frame, smooth_pt, 12, (0, 255, 255), -1)

            elif action == "ERASE":
                action_text = "ERASE (지우개)"
                action_color = (0, 0, 255)  # Red
                last_draw_pt = None
                if smooth_pt is not None:
                    cv2.circle(drawing_canvas, smooth_pt, 40, (0, 0, 0), -1)
                    cv2.circle(frame, smooth_pt, 40, (0, 0, 255), 2)

            elif action == "PAN":
                action_text = f"PAN (주먹 이동 dx:{result['pan_dx']:.2f}, dy:{result['pan_dy']:.2f})"
                action_color = (255, 128, 0)
                last_draw_pt = None
                if smooth_pt is not None:
                    cv2.circle(frame, smooth_pt, 30, (255, 128, 0), 2)

            elif action == "ZOOM_IN":
                action_text = "ZOOM_IN (👍 화면 확대)"
                action_color = (0, 215, 255)
                last_draw_pt = None

            elif action == "ZOOM_OUT":
                action_text = "ZOOM_OUT (🤙 화면 축소)"
                action_color = (255, 0, 255)
                last_draw_pt = None

            else:
                action_text = "HOVER (대기)"
                action_color = (180, 180, 180)
                last_draw_pt = None
        else:
            last_draw_pt = None
            action_text = "NO HAND (손 없음)"
            action_color = (100, 100, 100)

        # 5. 영상과 드로잉 레이어 합성
        combined = cv2.add(frame, drawing_canvas)

        # 6. 상단 HUD 상태창
        cv2.rectangle(combined, (0, 0), (w, 60), (20, 20, 30), -1)
        cv2.putText(
            combined,
            f"ACTION: {action_text}",
            (20, 42),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            action_color,
            2
        )

        cv2.imshow(WINDOW_NAME, combined)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        elif key == ord("c"):
            drawing_canvas = np.zeros((h, w, 3), dtype=np.uint8)

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
