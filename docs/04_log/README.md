# docs/

로깅 관련 운영 문서 모음. 4번(로깅 레벨 분리) 작업 이후 정리한 것으로, 전부 실제 코드
(`Web_Server/server.js`, `Video_Engine/app.py`, `Motion_Engine/app.py`)를 감사해서 작성함.

1. [01-log-level-guidelines.md](01-log-level-guidelines.md) — 로그 레벨 기준 정의서
2. [02-log-field-schema.md](02-log-field-schema.md) — 로그 필드 스키마
3. [03-layer-logging-responsibility.md](03-layer-logging-responsibility.md) — 레이어별 로깅 책임표
4. [04-alert-policy.md](04-alert-policy.md) — 알림 정책 (규칙은 코드로 구현됨, 실제 웹훅/온콜/SLA만 사람이 채우면 됨)
5. [05-log-retention-policy.md](05-log-retention-policy.md) — 로그 보관 기간 정책
6. [06-log-collection-architecture.md](06-log-collection-architecture.md) — 로그 수집 아키텍처(런북)

## 이 문서들에서 발견/제안된 사항의 현재 상태

- ~~Motion_Engine의 제스처 연산이 감지된 프레임마다 두 번(WS 경로 + HTTP 경로) 실행되고 있음~~
  — **수정 완료.** Video_Engine의 중복 호출 경로(`MotionEngineClient`)를 제거함.
  [03번 문서](03-layer-logging-responsibility.md#해결된-이슈-실제로-중복-연산이-일어나고-있던-경로-수정-완료) 참고.
- ~~Web_Server의 로그 필드(`level` 숫자, `time` 키)가 Python 두 엔진과 통일되어 있지 않음~~
  — **수정 완료** (pino formatter/timestamp 옵션). 단 `logEvent()`를 안 거치는 일부 인프라 로그는
  구조적 필드(`container`/`session_id`/`event`)가 여전히 없음 — [02번 문서](02-log-field-schema.md#남은-후속-작업) 참고.
- 모바일 클라이언트(브라우저)의 실패가 서버 로그에 전혀 남지 않음 — **미구현**, [03번 문서](03-layer-logging-responsibility.md#발견된-갭-스마트폰-클라이언트-쪽-실패가-서버-로그에-전혀-안-남음) 참고.
- ~~"N초 이상 연속 미검출 시 WARNING으로 격상" 로직 미구현~~ — **구현 완료** (`hand_not_detected_prolonged`), [01번 문서](01-log-level-guidelines.md#헷갈리는-경계-사례) 참고.
- `trace_id` 전파, Slack 알림(디바운싱 포함)도 이번에 구현 완료 — 자세한 내용은 `docs/IMPROVEMENTS.md` 참고.
- Fluent Bit 로그 수집 사이드카(06번 문서의 "제안 아키텍처")는 사용자 확인 결과 **보류** — 목적지 저장소(Loki/ES 등)가 정해지기 전까지는 도입하지 않기로 함.
