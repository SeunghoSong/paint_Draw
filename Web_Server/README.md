# Container A - 웹/보안 서버 (PM POC 계획서 반영 버전)

⚠️ **PM 계획서(Python FastAPI 기준) 대신 Node.js를 유지하되, 인터페이스(포트/프로토콜/JSON 포맷)는 계획서 스펙에 정확히 맞춤.**

## 변경 이력 (기존 버전 대비)
- 포트: 3000 → **8000**
- 페이지 경로: `/pc`, `/mobile` 추가 (계획서 네이밍)
- 세션: QR 동적 세션은 유지하되, **고정 세션 ID `poc-001`을 기본으로 사용**
- 프레임 전송: 모바일 → 서버 구간은 **순수 WebSocket** (`/ws/mobile`)으로 전송 (Socket.io 아님)
- 명령 수신: Container C → 서버 구간도 **순수 WebSocket** (`/ws/command`)
- JSON 필드명: `session_id`, `frame`, `cmd`, `x`, `y` (계획서와 동일하게 snake_case로 통일)
- 캔버스 해상도: **1280x720 고정** (`/api/config`에서 확인 가능) — Container C가 이 값 기준으로 좌표 스케일링해야 함

## 실행 순서

### 1. 패키지 설치
```bash
npm install
```

### 2. 서버 실행
```bash
node server.js
```
아래 로그가 뜨면 성공:
```
Container A 서버 실행 중: http://localhost:8000
   - PC 페이지:      http://localhost:8000/pc
   - 모바일 페이지:  http://localhost:8000/mobile
   - 고정 세션 ID:   poc-001
   - 모바일 프레임:  ws://localhost:8000/ws/mobile
   - 명령 수신:      ws://localhost:8000/ws/command
```
`Container B 연결 에러: ECONNREFUSED` 로그는 **Container B가 아직 안 떠서 나는 정상 로그**입니다 (5초마다 자동 재시도).

### 3. (모바일에서 접속해야 하면) ngrok 실행
같은 와이파이라면 ngrok 없이 PC의 로컬 IP로도 접속 가능하지만, 안전하게 하려면:
```bash
ngrok http 8000
```

### 4. 접속
- PC: `http://localhost:8000/pc` (또는 ngrok 주소 + `/pc`)
- 모바일: `http://<PC-IP 또는 ngrok주소>:8000/mobile` (또는 QR 스캔)

## 지금까지 구현된 것
- [x] 고정 세션(`poc-001`) 기반 PC-모바일 연결 (QR도 병행 지원)
- [x] 모바일 카메라 권한 요청 + 프리뷰
- [x] 모바일 → `/ws/mobile` 로 프레임(base64 jpeg, 7fps, 640x480) 실전송 시작
- [x] 서버 → Container B로 프레임 포워딩 (`ws://localhost:8001/analyze`, 자동 재연결)
- [x] `/ws/command`에서 Container C 명령 수신 → PC 캔버스에 DRAW/ERASE/NONE 반영

## Container B, C 담당자에게 공유할 것

### [A ↔ B] Container A가 B로 보내는 데이터
```
ws://localhost:8001/analyze  (B가 서버 역할, A가 클라이언트로 접속)
```
```json
{ "session_id": "poc-001", "frame": "<base64 jpeg, data URI 접두어 제외>" }
```

### [C → A] Container A가 C로부터 받는 데이터
```
A가 서버 역할로 ws://localhost:8000/ws/command 를 열어둠. C가 여기로 접속(클라이언트)해서 메시지 전송.
```
```json
{ "session_id": "poc-001", "cmd": "DRAW", "x": 150, "y": 200 }
```
- `cmd`는 `"DRAW" | "ERASE" | "NONE"`
- `x`, `y`는 **1280x720 캔버스 기준 픽셀 좌표**로 변환 완료된 값 (Container C가 변환 책임)
- 필요하면 `GET http://localhost:8000/api/config` 호출해서 캔버스 해상도 확인 가능

## 남은 작업 / 확인할 것
- [ ] Container B 담당자와 B의 실제 포트가 8001 맞는지, 엔드포인트가 `/analyze` 맞는지 재확인
- [ ] Container C 담당자와 캔버스 1280x720 기준으로 좌표 스케일링하는지 확인
- [ ] 전체 파이프라인 통합 테스트 (검지 하나로 선 그리기 되는지가 Must 항목)
