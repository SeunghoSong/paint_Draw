# 로그 레벨 기준 정의서

목적: "이 상황엔 어느 레벨을 써야 하는가"를 팀원마다 다르게 판단하지 않도록 하는 기준표.
아래 예시는 전부 현재 `Web_Server/server.js`, `Video_Engine/app.py`, `Motion_Engine/app.py`에
실제로 구현되어 있는 이벤트를 기준으로 작성함 (가상의 예시 아님).

## 현재 코드에 구현된 레벨: DEBUG / INFO / WARNING / ERROR

이 프로젝트는 현재 **4단계만 실제로 구현**되어 있다. CRITICAL은 이 문서에 기준만 정의해두고,
코드(`log_event()`의 `_LOG_METHODS` 매핑, Node의 `logEvent()`)에는 아직 반영되지 않았다 —
도입하려면 별도 작업 필요.

### DEBUG — "정상 동작의 세부 과정, 매 프레임 발생"

운영 중엔 안 보이고(`LOG_LEVEL=info`가 기본값), 디버깅할 때(`LOG_LEVEL=debug`)만 확인.

| 이벤트 | 위치 | 설명 |
|---|---|---|
| `frame_received` | Video_Engine | 프레임 1개 수신 (초당 수십 회) |
| `landmarks_extracted` | Video_Engine | 손 검출 성공 |
| `no_hand_in_frame` | Video_Engine | 손 미검출 — **정상 케이스** |
| `gesture_computed` | Motion_Engine | 제스처 연산 결과 1건 |

기준: **매 프레임/매 요청마다 발생하고, 그 자체로는 문제 상황이 아닌 것**은 DEBUG.
INFO로 찍으면 운영 중 로그가 초당 수십 줄씩 쌓여 정작 봐야 할 로그를 덮어버린다
(실제로 이번 로깅 작업 전엔 이 4개가 전부 INFO였어서 이 문제가 있었음).

### INFO — "생명주기 이벤트, 저빈도"

| 이벤트 | 위치 | 설명 |
|---|---|---|
| `ws_connected` / `ws_disconnected` | 3개 서비스 전부 | PC/모바일/컨테이너 간 연결·해제 |
| `session_cleaned` | Web_Server | idle 세션 정리 |
| `frame_stats` | Web_Server | 세션별 프레임 수신/전달/드롭 카운터 (30초 주기) |

기준: **정상 흐름이지만 "무슨 일이 있었는지" 운영 중에도 남겨두고 싶은 저빈도 이벤트**.
연결 1번, 세션 정리 1번, 통계 요약 1번처럼 초당 여러 번 발생하지 않는 것.

### WARNING — "자동으로 복구되거나, 우리 시스템 결함이 아닌 것"

| 이벤트 | 위치 | 설명 |
|---|---|---|
| `invalid_frame_message` | Video_Engine | 클라이언트가 보낸 프레임 메시지가 JSON 파싱 불가 |
| `hand_not_detected_prolonged` | Video_Engine | `NO_HAND_WARNING_SECONDS`(기본 3초) 넘게 연속 미검출 — 세션당 1회만(재검출 전까지 재알림 안 함) |
| `circuit_breaker_open` | Web_Server | Motion_Engine 연속 호출 실패로 서킷브레이커 오픈 |
| Container B 연결 끊김(재연결 예정) | Web_Server | 재연결 로직이 자동으로 처리함 |
| OpenSSL 생성 실패 (HTTP 대체 실행) | Web_Server | 대체 경로로 계속 동작 가능 |

기준: **(a) 외부(클라이언트/네트워크)가 원인이라 우리 코드 결함이 아니거나,
(b) 자동 복구 로직이 이미 있어서 사람이 당장 안 봐도 되는 것**.

### ERROR — "우리 시스템에서 실제로 실패한 것, 원인 파악이 필요한 것"

| 이벤트 | 위치 | 설명 |
|---|---|---|
| `hand_detection_error` | Video_Engine | 디코딩/추출 중 예외 발생 (진짜 버그/손상된 프레임) |
| `motion_engine_call_failed` | Web_Server | Motion_Engine `/gesture` 호출 실패 |
| Container B 연결 에러/시도 실패 | Web_Server | |
| `uncaughtException` / `unhandledRejection` | Web_Server | 처리되지 않은 예외 (프로세스 재기동 유발) |

> Video_Engine에 있던 `frame_forward_failed`(Motion_Engine으로의 직접 WS 전달 실패)는
> 이 이벤트를 발생시키던 코드(`MotionEngineClient`) 자체가 중복 연산 버그로 제거되어 더 이상
> 존재하지 않는다. 자세한 내용은 [03-layer-logging-responsibility.md](03-layer-logging-responsibility.md) 참고.

### CRITICAL (아직 코드에 미구현 — 정책만 정의)

"자동 복구로 해결되지 않고, 서비스 전체가 사실상 멈춘 상태"를 위해 예약.
예시 후보(실제 코드 반영 시 재검토 필요):
- `docker-compose`의 `restart` 정책이 짧은 시간 안에 반복 재시작되는 경우(crash loop)
- 서킷브레이커가 열린 채로 쿨다운 이후에도 계속 재오픈되는 경우(=Motion_Engine이 장시간 응답 불가)

## 헷갈리는 경계 사례

1. **"미검출 vs 연속 미검출"** — 손이 한 프레임 안 잡히는 건 `no_hand_in_frame`(DEBUG, 정상).
   하지만 **N초 이상 연속으로 미검출**이 이어지면(카메라가 가려졌거나 각도가 계속 안 맞는 등
   사용자 경험에 실제 영향을 주는 상태) WARNING으로 격상해야 한다.
   **→ 구현 완료.** `Video_Engine/app.py`가 세션별 마지막 검출 성공 시각(`_last_hand_seen_at`)을
   추적하다가 `NO_HAND_WARNING_SECONDS`(기본 3초)를 넘기면 `hand_not_detected_prolonged`를 딱
   1회만 WARNING으로 남긴다(재검출로 리셋되기 전까지 재알림 없음 — 스팸 방지).

2. **"1회 실패 vs 연속 실패"** — Motion_Engine 호출 1번 실패는 `motion_engine_call_failed`(ERROR,
   원인 추적용). 하지만 그게 누적되어 서킷브레이커가 열리는 시점(`circuit_breaker_open`)은
   ERROR가 아니라 WARNING이다. **같은 근본 원인이라도 "빈도/임계치를 넘었는지"에 따라 레벨이
   달라지는 예시** — 이건 현재 코드에 이미 이렇게 구현되어 있음(참고용 모범 사례).

3. **"클라이언트 입력 문제 vs 우리 시스템 문제"** — `invalid_frame_message`는 예전엔 ERROR였다가
   이번에 WARNING으로 낮췄음. 클라이언트가 이상한 데이터를 보낸 건 우리 서버의 결함이 아니기
   때문. 반면 `hand_detection_error`(디코딩 자체가 예외를 던진 경우)는 우리 파이프라인 안에서
   일어난 일이라 ERROR를 유지.

## 안티패턴 (이렇게 찍지 말 것)

- **정상 대기/미검출 상태를 ERROR로 찍지 말 것** — 알람 피로(alert fatigue)를 유발해서 진짜
  ERROR가 와도 무시하게 만든다. (과거에 `hand_not_detected`가 이 문제를 갖고 있었음 — 이번에
  `no_hand_in_frame`/DEBUG로 분리해서 해결)
- **매 프레임 발생하는 정상 처리 로그를 INFO 이상으로 찍지 말 것** — 디스크/로그 저장 비용만
  키우고, 정작 찾아야 할 로그가 파묻힌다.
- **외부(클라이언트) 입력 문제를 우리 시스템 장애처럼 ERROR로 찍지 말 것** — 온콜 담당자가
  대응할 수 없는 알림(우리가 고칠 수 없는 문제)이 반복되면 알림 자체를 신뢰하지 않게 된다.
- **재시도로 자동 복구되는 개별 실패 하나하나에 반응해서 알림을 걸지 말 것** — 알림은 레벨이
  아니라 "임계치를 넘었는지"로 걸어야 한다 (자세한 내용은 [04-alert-policy.md](04-alert-policy.md)).
