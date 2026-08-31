# 알림(Alert) 정책

> **이 문서에서 정의한 규칙은 코드로 구현되어 있다** (`Web_Server/server.js`의
> `maybeAlertOnError`/`sendSlackAlert`, `Video_Engine`·`Motion_Engine`의 `_maybe_alert_on_error`/
> `_send_slack_alert`). 다만 **`SLACK_WEBHOOK_URL`이 비어있으면 완전히 비활성** — 실제 채널/온콜
> 담당자/SLA는 팀의 운영 체계와 직결되는 결정이라 LLM이 임의로 채우지 않았다.
> `TODO(사람이 채울 것)`로 표시된 부분은 실제 값으로 교체해야 그 부분이 의미를 가진다.

## 현재 상태 (코드 기준)

3개 서비스 모두 아래 "알림 발생 조건"의 두 규칙(ERROR 반복 발생 / `circuit_breaker_open` 즉시)을
실제로 구현해서 로컬에서 가짜 웹훅으로 직접 검증했다 (임계치를 넘는 순간 딱 1번만 발송, 그 전/후로는
재발송하지 않는 디바운싱까지 확인). `SLACK_WEBHOOK_URL` 환경변수가 비어있는 게 기본값이라, 실제
채널을 연결하기 전까지는 알림이 어디로도 나가지 않는다(안전한 기본 상태). PagerDuty 등 다른 채널은
아직 구현 안 함 — 필요해지면 `sendSlackAlert`와 같은 패턴으로 추가하면 된다.

## 알림 발생 조건 (레벨/임계치)

레벨 정의는 [01-log-level-guidelines.md](01-log-level-guidelines.md) 기준.

| 조건 | 임계치 | 근거 |
|---|---|---|
| ERROR 반복 발생 | `ALERT_ERROR_WINDOW_MS`(기본 5분)간 `ALERT_ERROR_THRESHOLD`(기본 10)회 이상 — **구현 완료** | 개별 ERROR 1건마다 알림을 걸면 일시적 네트워크 지연에도 알림이 폭주함(알람 피로). 기본값은 안전한 초기값일 뿐, 실사용 트래픽을 보고 조정 권장(`TODO(사람이 조정)`) |
| `circuit_breaker_open` | 발생 즉시 1회 — **구현 완료** | 이미 코드에서 "연속 실패 임계치(`CIRCUIT_BREAKER_FAILURE_THRESHOLD`, 기본 5회)를 넘었을 때만" 발생하도록 필터링되어 있어, 이 이벤트 자체가 이미 "임계치를 넘은 신호"임 |
| CRITICAL | 즉시 1회 | CRITICAL은 [01-log-level-guidelines.md](01-log-level-guidelines.md)에 정의만 있고 코드 미구현 — 도입 후 재검토 |
| healthcheck 연속 실패(컨테이너 재시작 반복) | `TODO(사람이 결정: 몇 회/몇 분?)` — 미구현 | `docker-compose.yml`에 `restart: unless-stopped` + healthcheck는 있지만, "재시작이 반복되고 있다"는 것 자체를 감지해서 알리는 로직은 별도 구현 필요 |

## 알림 채널 및 온콜 담당자

- 알림 채널: `TODO(사람이 채울 것 — 예: Slack #ops-alerts, PagerDuty 서비스명 등)`
- 온콜 지정 방식: `TODO(사람이 채울 것 — 고정 담당자? 주차별 로테이션? PagerDuty 스케줄?)`
- 에스컬레이션 정책(1차 미응답 시 2차 담당자로): `TODO(사람이 채울 것)`

## 디바운싱 / 그룹핑 규칙 — 구현 완료

- ERROR 카운터는 창(`ALERT_ERROR_WINDOW_MS`) 안에서 임계치를 "넘는 순간"에만 1번 발송하고
  (카운트가 정확히 임계치와 같아지는 시점), 창이 갈릴 때만 카운터를 리셋한다 — 같은 창 안에서
  에러가 계속 나도 알림이 반복 발송되지 않는다. 실제로 창 안에서 에러 6번 발생시켜 알림이 딱
  1번만 나가는 것을 로컬에서 확인함.
- `circuit_breaker_open`은 쿨다운(`CIRCUIT_BREAKER_COOLDOWN_MS`, 기본 10초) 동안은 이벤트
  자체가 재발생하지 않아 별도 디바운싱 코드 없이도 자연히 스팸이 안 된다.

## 심각도별 대응 SLA

| 심각도 | 대응 시한 |
|---|---|
| CRITICAL | `TODO(사람이 결정 — 예: 15분 내 확인)` |
| ERROR (임계치 초과) | `TODO(사람이 결정)` |
| WARNING | 실시간 알림 대상 아님 — 대시보드에서 사후 확인 |

## 다음 단계

1. 팀에서 실제 사용할 알림 도구(Slack/PagerDuty/기타)를 확정하고, `SLACK_WEBHOOK_URL`에 실제
   웹훅 URL을 채운다 (코드는 이미 준비되어 있음 — 이 값만 채우면 바로 동작).
2. 온콜 로테이션 방식을 정한다.
3. `ALERT_ERROR_WINDOW_MS`/`ALERT_ERROR_THRESHOLD` 기본값을 실사용 트래픽을 보고 조정한다.
4. healthcheck 반복 실패(crash loop) 감지 알림은 아직 없음 — 별도 구현 필요.
