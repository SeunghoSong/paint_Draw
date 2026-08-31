# 버전관리: 마이너 버전(20-slim)만 고정돼있어 재빌드마다 패치 버전이 바뀔 수 있던 것을 패치버전까지 고정
FROM node:20.20.2-slim

# 버전관리 2부: CI가 빌드 시점에 --build-arg로 커밋 SHA를 넘겨서 이미지 라벨에 남김
# (이미지 하나만 보고 "무슨 커밋으로 빌드됐는지" 바로 확인 가능하게)
ARG GIT_SHA=unknown
LABEL git.sha=$GIT_SHA

RUN apt-get update && apt-get install -y openssl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY package*.json ./
RUN npm install --production

COPY . .

ENV PORT=8000
EXPOSE 8000

CMD ["node", "server.js"]
