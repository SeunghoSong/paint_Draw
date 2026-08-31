# Container B — 영상 분석 엔진 (Vision Engine)

## 역할
프레임 이미지에서 손을 검출하고 21개 손 랜드마크의 (x, y) 좌표를 추출한다.
좌표가 어떤 제스처(그리기/지우기 등)를 의미하는지는 판별하지 않는다 — 그 역할은 Container C의 책임이다.

## 핵심 흐름
1. Container A로부터 WebSocket(`/ws`)으로 프레임(base64 jpeg) 수신
2. base64 → OpenCV(BGR) 이미지 디코딩
3. MediaPipe Hands로 21개 (x, y) 정규화(0~1) 좌표 추출 (z는 사용하지 않음)
4. 손 미검출 시 `detected: false` + 빈 배열로 처리 (예외로 인한 크래시 없음)
5. 결과를 같은 WebSocket으로 Container A에게 반환 — Container C 호출 여부는 Container A가 결정

## 인터페이스

### A → B (수신, WebSocket `/ws`)
```json
{ "session_id": "poc-001", "type": "frame", "frame": "<base64 jpeg>", "ts": 1735200000000 }
```

### B → A (송신, 같은 WebSocket 연결로 반환)
```json
{ "session_id": "poc-001", "type": "landmarks", "detected": true, "landmarks": [[0.42, 0.55], ...], "ts": 1735200000050 }
```
- `landmarks`는 항상 길이 21 (미검출 시 빈 배열)
- 좌표는 0~1 정규화 값 그대로 전달 (픽셀 변환 금지, 변환은 Container C 책임)
- Container A는 이 응답을 받아 `detected: true`일 때만 Container C의 HTTP `/gesture`를 호출한다
  (`Web_Server/server.js`의 `bSocket.on('message', ...)` 참고)

> **버그수정 이력**: 예전엔 여기서 Container C에도 WebSocket으로 직접 전송(`MotionEngineClient`)하고
> 있었는데, 그 결과는 아무도 읽지 않으면서 Container C의 세션 상태(EMA 스무딩/디바운스)만
> 이중으로 갱신시키는 문제가 있었다(Container A 경유 경로가 실제로 렌더링에 쓰이는 경로였음).
> 그래서 이 파일에서 Container C로의 직접 전송은 제거하고, 위 흐름(B→A만)으로 정리함.
> Container B↔C 자체를 단독으로 검증하고 싶으면 `webcam_motion_test.py`(호스트에서 직접 실행,
> Motion_Engine의 `/ws`에 직접 접속)를 쓰면 된다 — 이 스크립트는 이 프로덕션 경로와 무관하다.

## 참고: 손 검출 모델
현재 mediapipe는 예전 `mp.solutions.hands` API를 제거하고 Tasks API(`HandLandmarker`)로 통일되어 있다.
Tasks API는 별도 모델 파일(`hand_landmarker.task`, 약 7.8MB)이 필요한데, `app.py`가 최초 실행 시
자동으로 공식 URL에서 내려받아 `Video_Engine/models/`에 캐시한다(이후 실행부터는 재다운로드 없음).
- Docker 이미지는 빌드 시점에 미리 받아두므로 컨테이너 실행 때는 인터넷이 필요 없다.
- 로컬 실행(`python app.py`, `python webcam_test.py`)은 최초 1회만 인터넷 연결이 필요하다.

## 로깅
프로젝트 공통 한 줄 JSON 로그 포맷을 그대로 따른다. 자세한 레벨 기준/필드는
[`docs/01-log-level-guidelines.md`](../docs/01-log-level-guidelines.md),
[`docs/02-log-field-schema.md`](../docs/02-log-field-schema.md) 참고.
```json
{"ts": 1735200000123, "container": "B", "session_id": "poc-001", "level": "DEBUG", "event": "landmarks_extracted", "detail": {"detected": true, "count": 21}}
```
사용 이벤트:
- `ws_connected` / `ws_disconnected` (INFO) — Container A와의 연결 생명주기
- `frame_received`, `landmarks_extracted`, `no_hand_in_frame`(정상 미검출) (DEBUG) — 프레임마다 발생하는 고빈도 이벤트라 운영 중엔 기본적으로 안 보임(`LOG_LEVEL=debug`일 때만)
- `invalid_frame_message` (WARNING) — Container A가 형식이 안 맞는 프레임 메시지를 보낸 경우
- `hand_detection_error` (ERROR) — 디코딩/검출 중 실제 예외 발생

어떤 예외가 발생해도 프로세스는 죽지 않고 `level: "ERROR"` 로그만 남긴다.

## 실행 방법

### 1) Docker Compose로 전체 스택과 함께 실행 (기본)
```bash
docker compose up --build video_engine
```
- 헬스체크: http://localhost:8001/health
- WebSocket: `ws://localhost:8001/ws`

### 2) Container B 단독 로컬 실행
```bash
cd Video_Engine
pip install -r requirements.txt
python app.py
```

### 3) 노트북 웹캠으로 단독 테스트 (Container A/C 불필요)
Container A, C가 없어도 손 검출 품질만 눈으로 바로 확인할 수 있는 스크립트다.
`app.py`의 실제 랜드마크 추출 함수를 그대로 재사용하므로, 여기서 확인되는 동작은
서비스 코드와 동일하게 보장된다.

```bash
cd Video_Engine
pip install -r requirements.txt
python webcam_test.py
```
- 노트북 웹캠 영상 창이 뜨고, 손이 검출되면 21개 랜드마크가 초록 점으로 표시된다.
- 콘솔에는 Container C로 전달될 것과 동일한 포맷의 JSON(`{"session_id","type":"landmarks","detected","landmarks","ts"}`)이 프레임마다 출력된다.
- 종료: 영상 창에서 `q` 키

## 파일 구성
```
Video_Engine/
├── app.py           # FastAPI 서버 (A로부터 프레임 수신 → 랜드마크 추출 → C로 전송)
├── webcam_test.py    # 노트북 웹캠 기반 단독 테스트 스크립트
├── requirements.txt
├── Dockerfile
└── README_VIDEO.md
```
