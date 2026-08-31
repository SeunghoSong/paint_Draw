# 버전관리: 마이너 버전(3.11-slim)만 고정돼있어 재빌드마다 패치 버전이 바뀔 수 있던 것을 패치버전까지 고정
FROM python:3.11.16-slim

# 버전관리 2부: CI가 빌드 시점에 --build-arg로 커밋 SHA를 넘겨서 이미지 라벨에 남김
ARG GIT_SHA=unknown
LABEL git.sha=$GIT_SHA

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8002

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8002"]
