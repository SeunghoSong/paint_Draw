# 로그 필드 스키마

목적: 모든 로그가 공통된 구조를 갖게 해서, 나중에 로그를 한곳에 모아 필드로
검색/집계할 수 있게 하는 것. 아래는 **현재 실제 코드의 상태를 그대로 감사(audit)한 결과**이며,
"이미 다 통일되어 있다"고 포장하지 않고 남아있는 불일치를 그대로 적었다.

## 목표 스키마 (모든 로그가 지향해야 할 형태)

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `ts` | integer (epoch ms) | 필수 | 로그 발생 시각 |
| `container` | string (`"A"`\|`"B"`\|`"C"`) | 필수 | A=Web_Server, B=Video_Engine, C=Motion_Engine |
| `session_id` | string | 필수 | 세션에 속하지 않는 전역 이벤트는 `null` 허용 |
| `trace_id` | string \| null | 필수 | 프레임 1개가 A→B→(A→C)로 흘러가는 과정을 묶는 요청 단위 ID. `session_id`(세션 전체)와는 다른 축. Web_Server가 모바일 프레임 수신 시 발급하고, 이후 모든 홉에서 그대로 전달 |
| `level` | string (`DEBUG`\|`INFO`\|`WARNING`\|`ERROR`) | 필수 | 항상 대문자 문자열 |
| `event` | string (snake_case) | 필수 | 예: `ws_connected`, `frame_stats` |
| `detail` | object | 필수(빈 객체 허용) | 이벤트별 부가 정보 |

네이밍 규칙: **필드명은 snake_case로 통일** (`session_id`, `total_pc_sockets`처럼).
카멜케이스(`sessionId`)는 코드 내부 변수명으로는 써도 되지만, **로그로 남는 JSON 키에는 쓰지 않는다** —
팀마다 컨벤션이 다르면 로그 검색 쿼리가 서비스마다 달라져서 검색이 깨진다.

## 현재 실제 상태 (감사 결과)

### Python 엔진 (Video_Engine, Motion_Engine) — `log_event()`를 거치는 로그

목표 스키마와 **완전히 일치**한다 (`trace_id` 포함, 실제 실행해서 확인):
```json
{"ts": 1788154895904, "container": "B", "session_id": "s1", "trace_id": "trace-123", "level": "INFO", "event": "test_event", "detail": {"a": 1}}
```

### Web_Server — (A)/(B) 두 경로 모두 `level`/`ts`는 이제 통일됨, (B)는 구조적 필드가 여전히 없음

**(A) `logEvent()`를 거치는 로그** (`ws_connected`, `session_cleaned`, `frame_stats`,
`motion_engine_call_failed`, `circuit_breaker_open` 등) — **수정 완료**. pino에
`formatters.level`(대문자 문자열로 변환)과 `timestamp`(`ts` 키로 출력) 옵션을 추가해서
Python과 완전히 같은 형태가 되는 것을 직접 실행해서 확인함:
```json
{"level":"INFO","ts":1788154295548,"pid":15176,"hostname":"...","container":"A","session_id":"s1","trace_id":"t1","event":"ws_connected","detail":{}}
```
(`pid`/`hostname`은 pino가 자동으로 붙이는 필드라 Python 쪽엔 없음 — 불일치라기보단 Node 쪽에만
있는 추가 필드라 문제되지 않음.)

**(B) `logEvent()`를 거치지 않는 로그** (SSL 인증서 생성, Container B 연결 성공/실패,
서버 시작 배너 등 — `logger.info('메시지 문자열')` 형태로 직접 호출하는 것들) — **`level`/`ts`
포맷은 위 수정으로 (A)와 동일하게 맞춰졌지만, `container`/`session_id`/`event`/`detail` 구조는
여전히 없고 `msg`만 있는 평문 로그다**:
```json
{"level":"INFO","ts":...,"pid":...,"hostname":"...","msg":"✅ Container B에 성공적으로 연결됨"}
```
이 로그들은 세션에 속하지 않는 **전역 인프라 이벤트**라 `session_id: null`이 맞는 모델이지만,
아직 그 구조로 옮겨지진 않았다.

### 남은 후속 작업

- (B) 그룹의 전역 인프라 로그도 `logEvent('info', null, 'container_b_connected', {...})`처럼
  `logEvent()`를 거치도록 통일한다 (코드 변경 필요, 아직 안 함).

## 이벤트별 선택 필드 (`detail` 안에 들어가는 값)

지금 실제로 쓰이고 있는 것만 기록. 아직 구현되지 않은 `latency_ms`(요청 처리 지연시간)는
**현재 어디에서도 채워지지 않는다** — 도입하려면 Web_Server의 요청 처리 구간에 타이머를 추가하는
별도 작업이 필요하다. (`frame_id`가 하던 역할은 이제 `trace_id`가 대신함 — 프레임 단위 추적은 구현 완료.)

| 이벤트 | detail 필드 | 타입 |
|---|---|---|
| `ws_connected` (pc/mobile) | `peer`, `total_pc_sockets` | string, integer |
| `frame_stats` | `received`, `forwarded`, `dropped` | integer |
| `landmarks_extracted` | `detected`, `count` | boolean, integer |
| `hand_not_detected_prolonged` | `seconds` | float |
| `gesture_computed` (Motion_Engine) | `action`, `transport`(`"ws"`\|`"http"`) | string, string |
| `hand_detection_error` / `motion_engine_call_failed` | `error` | string |
| `circuit_breaker_open` | `cooldown_ms` | integer |
| `/health` 응답(Video_Engine, 로그는 아니지만 같은 원칙 적용) | `model_version`, `model_loaded` | string, boolean |
