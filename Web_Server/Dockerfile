FROM node:20-slim

WORKDIR /app

COPY package*.json ./
RUN npm install --production

COPY . .

# docker-compose.yml에서 "8000:80"으로 매핑되어 있다면 컨테이너 내부는 80번이어야 함
# (팀원과 확인 후 8000:8000이 맞다면 아래 ENV PORT를 8000으로 바꿀 것)
ENV PORT=80
EXPOSE 80

CMD ["node", "server.js"]
