# paint_Draw

## 구조

역할별로 컨테이너를 분리했습니다. 각자 자기 디렉터리 안에서만 작업하면 됩니다.

```
paint_Draw/
├── docker-compose.yml   # 전체 서비스 정의
├── .env.example         # 환경변수 템플릿 (복사해서 .env로 사용)
├── Web_Server/           # 담당자 A - 화면/웹 서버 (nginx, :8000)
├── Video_Engine/         # 담당자 B - 영상 처리 엔진 (Flask 자리표시자, :8001)
└── Motion_Engine/        # 담당자 C - 모션 인식 엔진 (Flask 자리표시자, :8002)
```

## 시작하기

```bash
cp .env.example .env
docker compose up --build
```

- Web_Server: http://localhost:8000
- Video_Engine: http://localhost:8001/health
- Motion_Engine: http://localhost:8002/health

특정 서비스만 다시 빌드/실행하려면:

```bash
docker compose up --build web_server
docker compose up --build video_engine
docker compose up --build motion_engine
```

## 작업 규칙

- 각자 자기 폴더(`Web_Server/`, `Video_Engine/`, `Motion_Engine/`) 안의 코드와 `Dockerfile`만 수정합니다.
- 서비스 간 인터페이스(API 스펙 등)가 바뀌면 반드시 팀원과 공유 후 진행합니다.
- 프레임워크/스택은 현재 자리표시자(placeholder)이므로 각 담당자가 자유롭게 교체 가능합니다.
