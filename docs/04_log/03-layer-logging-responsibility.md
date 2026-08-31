# 레이어별 로깅 책임표

목적: "이 로그는 누가 남겨야 하는가"를 명확히 해서, 같은 이벤트가 중복으로 찍히거나
아무도 안 찍는 상황을 막는 것.

## 레이어 구성 (실제 데이터 흐름 기준)

```
스마트폰 브라우저 (mobile.html)
    │  WebSocket (프레임 base64)
    ▼
Web_Server (Container A) ── WebSocket ──▶ Video_Engine (Container B)
    │                                          │
    │  HTTP POST /gesture                      │  (landmarks를 다시 Web_Server에 반환)
    ▼                                          ▼
Motion_Engine (Container C) ◀─────────── (A가 결과 받아서 C를 호출)
    │
    ▼
Web_Server ── WebSocket ──▶ PC 브라우저 (index.html)
```
(과거엔 Video_Engine이 Motion_Engine에도 WebSocket으로 직접 전송하는 부가 경로가 있었으나
중복 연산 버그로 제거됨 — 아래 "해결된 이슈" 참고. `Motion_Engine`의 `/ws` 엔드포인트 자체는
`webcam_motion_test.py`라는 독립 테스트 스크립트가 여전히 직접 사용 중이라 남아있음.)

## 책임표

| 레이어 | 로깅 책임이 있는 이벤트 | 책임이 없는 것 |
|---|---|---|
| **스마트폰 브라우저**(mobile.html) | 없음 (서버로 전송되는 로그가 아예 없음 — 아래 "발견된 갭" 참고) | 서버 쪽 이벤트 전부 |
| **Web_Server (A)** | PC/모바일 WebSocket 연결·해제(`ws_connected`/`ws_disconnected`), idle 세션 정리(`session_cleaned`), 프레임 통계(`frame_stats`), **Motion_Engine 호출 실패/서킷브레이커**(`motion_engine_call_failed`, `circuit_breaker_open`), Container B와의 연결 상태, 프로세스 레벨 예외 | Video_Engine 내부의 프레임 디코딩/추론 실패, Motion_Engine 내부의 제스처 연산 |
| **Video_Engine (B)** | Web_Server(A)와의 WebSocket 연결·해제, 프레임 수신(`frame_received`), 손 검출 결과(`landmarks_extracted`/`no_hand_in_frame`), 디코딩/추론 예외(`hand_detection_error`) | Motion_Engine 호출(더 이상 B가 직접 호출하지 않음 — 아래 참고), Motion_Engine의 제스처 연산 결과가 맞는지, Web_Server의 세션 관리 |
| **Motion_Engine (C)** | Video_Engine과의 WebSocket 연결·해제, 제스처 연산 결과(`gesture_computed`) | 프레임이 어떻게 Web_Server까지 왔는지(호출자 책임), 호출 실패/재시도 |

## 레이어 간 중복 로깅 방지 규칙

**"재시도/실패는 호출하는 쪽에서만 로깅하고, 호출받는 쪽은 정상 처리만 로깅한다."**

예: Web_Server → Motion_Engine `/gesture` 호출이 실패하면 `motion_engine_call_failed`는
**Web_Server(호출자)만** 남긴다. Motion_Engine(호출받는 쪽)은 애초에 요청이 도달하지 못했으니
로깅할 대상 자체가 없고, 요청이 도달했다면 그건 성공 처리(`gesture_computed`)로 남긴다 —
"실패"를 양쪽에서 각자의 관점으로 중복 기록하지 않는다.

## 해결된 이슈: 실제로 중복 연산이 일어나고 있던 경로 (수정 완료)

로깅 문서를 작성하며 코드를 감사하다가 **로깅 중복이 아니라 연산 자체의 중복**을 발견해서
바로 수정했다. 아래는 문제였던 구조와 수정 내용의 기록이다 (참고용으로 남김).

**문제였던 구조**
- `Video_Engine`이 프레임마다 `landmarks_message`를 **두 곳에 동시에 보내고 있었다**:
  (1) `motion_client.send()`로 Motion_Engine의 `/ws`에, (2) `websocket.send_text()`로
  자신을 호출한 Web_Server에게.
- Motion_Engine의 `/ws` 핸들러는 `type: "landmarks"` 메시지를 받으면 `compute_gesture_logic()`을
  실제로 실행한다 (`gesture_computed` 로그도 여기서 찍힘).
- 그런데 Video_Engine의 `MotionEngineClient`는 이 WS 연결에서 **응답을 읽지 않았다**
  (`send()`만 있고 `recv()`가 없음) — 즉 (1) 경로에서 계산된 결과는 아무도 소비하지 않고 있었다.
- 한편 Web_Server는 (2) 경로로 받은 landmarks를 갖고 **HTTP `/gesture`로 다시** Motion_Engine을
  호출했고, 이 결과(만)가 실제로 PC 화면 렌더링에 쓰이고 있었다.
- 결과적으로 `compute_gesture_logic()`이 감지된 프레임마다 두 번 실행되고, 두 호출 모두
  Motion_Engine의 같은 `SessionState`(EMA 스무딩 값, 디바운스 큐)를 공유해서 변형시키고 있었다 —
  실제 렌더링에 쓰이는 결과의 EMA/디바운스가 "프레임당 1번 갱신"을 가정한 원래 설계와 다르게
  동작할 수 있는 정확성 버그였다.

**수정 내용** (`Video_Engine/app.py`)
- `MotionEngineClient` 클래스와 `motion_client` 인스턴스, 그 호출(`motion_client.send(...)`)을 제거.
- 이제 Video_Engine은 (2) 경로(Web_Server에게 landmarks 반환)만 남기고, Motion_Engine 호출은
  전적으로 Web_Server(HTTP `/gesture`) 책임으로 단일화됨.
- 더 이상 쓰이지 않게 된 `websockets` import, `MOTION_ENGINE_WS_URL` 환경변수, `requirements.txt`의
  `websockets==17.0.1` 명시적 pin도 함께 정리.
- **Motion_Engine의 `/ws` 엔드포인트 자체는 그대로 둠** — `webcam_motion_test.py`(독립 실행
  웹캠 테스트 스크립트)가 Container B↔C 연동을 단독으로 검증하기 위해 실제로 사용 중이라
  프로덕션 경로가 아니어도 존재 이유가 있음을 확인했기 때문.

수정 후 위 다이어그램/책임표는 이 실제 구조를 반영해 갱신됨.

## 발견된 갭: 스마트폰 클라이언트 쪽 실패가 서버 로그에 전혀 안 남음

`mobile.html`의 `getUserMedia` 실패(카메라 권한 거부 등), WebSocket 재연결 시도 등은
**브라우저 콘솔에만 찍히고 서버로는 전혀 전달되지 않는다.** 즉 온콜 담당자가 서버 로그만
보고 있으면 "사용자가 카메라 권한을 거부해서 스트리밍이 안 됐다" 같은 상황을 알 방법이 없다.
지금 범위에서는 이 갭을 문서에 명시만 해두고, 클라이언트 에러를 서버로 보고하는 기능(예:
`/api/client-error` 같은 엔드포인트)은 별도 작업으로 남긴다.
