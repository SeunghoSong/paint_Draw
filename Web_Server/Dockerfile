FROM node:20-slim

RUN apt-get update && apt-get install -y openssl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY package*.json ./
RUN npm install --production

COPY . .

ENV PORT=8000
EXPOSE 8000

CMD ["node", "server.js"]
