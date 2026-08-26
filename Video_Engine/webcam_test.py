"""
Container B 단독 테스트 스크립트.

Container A/C가 없어도 노트북 웹캠만으로 손 검출 품질을 눈으로 확인하기 위한
로컬 전용 도구다. app.py의 실제 추출 로직(extract_landmarks)을 그대로 재사용하므로
여기서 확인한 동작은 서비스 코드와 동일하게 보장된다.

실행: python webcam_test.py
종료: 영상 창에서 'q' 키
"""

import json
import time

import cv2

from app import extract_landmarks

WINDOW_NAME = "Video Engine - Webcam Test (press q to quit)"
SESSION_ID = "poc-001"


def main():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("웹캠을 열 수 없습니다. 카메라 연결/권한을 확인하세요.")
        return

    # 실제 서비스와 동일한 조건에서 테스트하기 위해 스펙 기준 해상도(최대 640x480)로 설정
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    print("웹캠 테스트 시작 (종료: 영상 창에서 q)")

    while True:
        ok, frame = cap.read()
        if not ok:
            print("프레임을 읽지 못했습니다.")
            break

        detected, landmarks = extract_landmarks(frame)

        if detected:
            # Container B -> C 로 실제 전달될 것과 동일한 포맷을 콘솔에 출력
            payload = {
                "session_id": SESSION_ID,
                "type": "landmarks",
                "detected": True,
                "landmarks": landmarks,
                "ts": int(time.time() * 1000),
            }
            print(json.dumps(payload, ensure_ascii=False))

            # 검출 품질을 눈으로 확인할 수 있도록 21개 랜드마크를 화면에 표시
            h, w = frame.shape[:2]
            for x, y in landmarks:
                cx, cy = int(x * w), int(y * h)
                cv2.circle(frame, (cx, cy), 4, (0, 255, 0), -1)

        cv2.putText(
            frame,
            "Hand detected" if detected else "No hand",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 0) if detected else (0, 0, 255),
            2,
        )

        cv2.imshow(WINDOW_NAME, frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
