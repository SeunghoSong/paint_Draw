FROM python:3.11-slim

WORKDIR /app

# OpenCV(GUI 포함 빌드)/MediaPipe 실행에 필요한 시스템 라이브러리 (slim 이미지에는 기본 포함 안 됨)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libegl1 \
    libgles2 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# app.py를 import하면 MediaPipe Hand Landmarker 모델을 자동으로 내려받는다(ensure_model()).
# 빌드 시점에 미리 실행해서 컨테이너가 실행될 때는 네트워크 없이도 바로 뜨게 한다.
RUN python -c "import app"

EXPOSE 8001

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
