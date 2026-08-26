# Web_Server

정적 파일을 nginx로 서빙하는 컨테이너. 담당자가 원하는 스택(React/Vue/Vanilla JS 등)으로 자유롭게 교체 가능합니다.
빌드 산출물이 `public/` 아래에 생성되도록 구조를 잡거나, 빌드 스택을 쓴다면 Dockerfile을 멀티스테이지로 바꾸면 됩니다.

- 실행: `docker compose up --build web_server`
- 접속: http://localhost:3000
