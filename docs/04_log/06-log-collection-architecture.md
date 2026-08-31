# 로그 수집 아키텍처 (런북)

목적: 로그가 컨테이너 안에서 발생한 뒤 어디로 가는지, 장애 시 "어디를 봐야 하는지" 바로 찾을 수
있게 하는 문서. **아래 "현재 아키텍처"는 지금 실제로 동작하는 것이고, "제안 아키텍처"는 아직
구현되지 않은 미래 계획이다 — 이미 있는 것처럼 서술하지 않는다.**

## 현재 아키텍처 (실제로 존재하는 것)

```
Web_Server / Video_Engine / Motion_Engine (stdout, JSON 한 줄씩)
        │
        ▼
docker의 json-file 로그 드라이버 (컨테이너당 10MB × 3파일 회전)
        │
        ▼
`docker compose logs` / `docker logs <컨테이너명>` 으로 사람이 직접 조회
```

중앙 저장소도, 수집 에이전트(Fluent Bit 등)도, 대시보드(Grafana 등)도 **아직 없다.**
지금 유일한 조회 방법은 Docker CLI뿐이다.

### 접속 방법

이 프로젝트를 실행 중인 호스트에서 직접:

```bash
# 전체 서비스 로그 실시간 확인
docker compose logs -f

# 서비스 하나만
docker compose logs -f web_server
docker compose logs -f video_engine
docker compose logs -f motion_engine

# 컨테이너 이름으로 직접 (docker-compose.yml의 container_name 기준)
docker logs -f Web_Server
docker logs -f Video_Engine
docker logs -f Motion_Engine
```

별도 URL이나 로그인은 필요 없다 — Docker CLI에 접근 가능한 사람이면 누구나 위 명령으로 확인 가능.

### 실전 사용 예시 — 특정 세션ID로 전체 흐름 추적

Python 두 엔진과 Web_Server(logEvent 경로) 로그 모두 `session_id` 필드를 갖고 있으므로,
`grep`으로 3개 컨테이너 로그를 동시에 훑어 하나의 세션이 어떻게 흘러갔는지 재구성할 수 있다:

```bash
SESSION=poc-001
docker compose logs --no-color \
  | grep "\"session_id\":\s*\"$SESSION\"" \
  | sort   # 로그에 타임스탬프 필드(ts/time)가 있어 시간순 정렬 가능
```

특정 컨테이너에서 ERROR/WARNING만 뽑아보고 싶을 때:

```bash
docker compose logs --no-color video_engine | grep '"level": "ERROR"'
docker compose logs --no-color web_server | grep -E '"level":(40|50)'  # pino: 40=warn, 50=error
```

(Web_Server의 `level`이 지금 숫자인 이유는 [02-log-field-schema.md](02-log-field-schema.md)의
"후속 작업" 항목 참고 — 문자열로 통일되면 위 grep도 다른 두 엔진과 같은 패턴을 쓸 수 있음.)

### 장애 시 체크리스트 (현재 아키텍처 기준)

1. `docker compose ps` — 3개 서비스가 다 떠 있는지, `restart` 정책 때문에 반복 재시작 중인
   컨테이너가 있는지 먼저 확인 (`STATUS` 열에 `Restarting`이 보이면 crash loop 의심).
2. healthcheck 상태 확인: `docker inspect --format='{{json .State.Health}}' Web_Server` (Video_Engine,
   Motion_Engine도 동일) — `"Status":"unhealthy"`면 어느 컨테이너가 문제인지 바로 좁혀짐.
3. 문제로 지목된 컨테이너의 로그를 `docker compose logs -f <서비스>`로 확인, `"level": "ERROR"`
   위주로 필터링.
4. Web_Server가 원인으로 의심되면 `frame_stats` 이벤트에서 `dropped`가 비정상적으로 높은지
   확인 — backpressure(프레임 드롭)가 걸리고 있다는 신호.
5. Motion_Engine 호출 실패가 원인이면 `circuit_breaker_open`이 찍혔는지 확인 — 찍혔다면
   Motion_Engine 컨테이너 자체의 상태(2번)를 다시 확인.

## 제안 아키텍처 (미구현 — 도입 시 검토용)

```
컨테이너(stdout) → Fluent Bit(수집 에이전트) → Loki 또는 Elasticsearch(중앙 저장소) → Grafana(대시보드)
```

- 도입 이유: 지금은 사람이 `docker compose logs`로 직접 grep해야 하지만, 컨테이너가 여러 대·
  여러 호스트로 늘어나면 이 방식이 안 통한다. 중앙 저장소가 있어야 [05-log-retention-policy.md](05-log-retention-policy.md)의
  레벨별 보관 기간도 실제로 적용 가능하고, [04-alert-policy.md](04-alert-policy.md)의 임계치 기반
  알림도 이 위에서 구현하는 게 자연스럽다.
- 각 컴포넌트 접속 방법/URL, 대시보드 링크: `TODO(사람이 채울 것 — 실제로 구축한 뒤)`
- 도입 우선순위: 이 프로젝트는 현재 단일 호스트 POC 규모라 당장 필수는 아님. CI/CD(5번 항목)
  이후 실사용자 규모가 커지는 시점에 재검토 권장.
