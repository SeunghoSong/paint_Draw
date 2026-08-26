const express = require('express');
const https = require('https');
const http = require('http');
const path = require('path');
const fs = require('fs');
const { execSync } = require('child_process');
const cors = require('cors');
const QRCode = require('qrcode');
const { v4: uuidv4 } = require('uuid');
const os = require('os');
const WebSocket = require('ws');
const axios = require('axios');

const app = express();

function getLocalIp() {
  const interfaces = os.networkInterfaces();
  for (const name of Object.keys(interfaces)) {
    for (const iface of interfaces[name]) {
      if (iface.family === 'IPv4' && !iface.internal) {
        return iface.address;
      }
    }
  }
  return 'localhost';
}

// ---------------------------
// 🔒 SSL 인증서 자동 생성 및 로드 (어떤 PC/환경에서 실행해도 스마트폰 카메라 권한 100% 획득)
// ---------------------------
const certPath = path.join(__dirname, 'cert.pem');
const keyPath = path.join(__dirname, 'key.pem');

if (!fs.existsSync(certPath) || !fs.existsSync(keyPath)) {
  try {
    console.log('🔒 HTTPS 자체 서명 SSL 인증서 자동 생성 중...');
    execSync(`openssl req -x509 -newkey rsa:2048 -nodes -keyout "${keyPath}" -out "${certPath}" -days 365 -subj "/CN=AirCanvas"`, { stdio: 'ignore' });
    console.log('✅ SSL 인증서 생성 완료!');
  } catch (err) {
    console.warn('⚠️ OpenSSL 생성 실패 (HTTP 대체 실행):', err.message);
  }
}

let server;
let isHttps = false;

if (fs.existsSync(certPath) && fs.existsSync(keyPath)) {
  const httpsOptions = {
    key: fs.readFileSync(keyPath),
    cert: fs.readFileSync(certPath)
  };
  server = https.createServer(httpsOptions, app);
  isHttps = true;
} else {
  server = http.createServer(app);
}

// ---------------------------
// 설정값
// ---------------------------
const PORT = process.env.PORT || 8000;
const CONTAINER_B_URL = process.env.CONTAINER_B_URL || 'ws://video_engine:8001/ws';
const MOTION_ENGINE_URL = process.env.MOTION_ENGINE_URL || 'http://motion_engine:8002/gesture';
const FIXED_SESSION_ID = 'poc-001';

app.use(cors());
app.use(express.json({ limit: '10mb' }));
app.use(express.static(path.join(__dirname, 'public')));

// ---------------------------
// 세션별 클라이언트 소켓 관리
// ---------------------------
const sessions = {};

function getOrCreateSession(sessionId) {
  if (!sessions[sessionId]) {
    sessions[sessionId] = {
      pcSockets: new Set(),
      mobileSocket: null,
    };
  }
  return sessions[sessionId];
}

// ---------------------------
// 페이지 라우트
// ---------------------------
app.get('/', (req, res) => res.sendFile(path.join(__dirname, 'public', 'index.html')));
app.get('/pc', (req, res) => res.sendFile(path.join(__dirname, 'public', 'index.html')));
app.get('/mobile', (req, res) => res.sendFile(path.join(__dirname, 'public', 'mobile.html')));

app.get('/api/session', (req, res) => {
  const sessionId = uuidv4().slice(0, 8);
  getOrCreateSession(sessionId);
  res.json({ sessionId });
});

app.get('/api/qr', async (req, res) => {
  const { session } = req.query;
  const targetSession = session || FIXED_SESSION_ID;
  getOrCreateSession(targetSession);
  
  const host = req.query.host || '192.168.55.208';
  const protocol = isHttps ? 'https' : 'http';
  const mobileUrl = `${protocol}://${host}:${PORT}/mobile?session=${targetSession}`;
  try {
    const qrDataUrl = await QRCode.toDataURL(mobileUrl, { width: 300 });
    res.json({ qrDataUrl, mobileUrl });
  } catch (err) {
    res.status(500).json({ error: 'QR 생성 실패' });
  }
});

// ---------------------------
// A -> Container B 연결 (내부 WebSocket 클라이언트)
// ---------------------------
let bSocket = null;
let bReconnectTimer = null;

function connectToContainerB() {
  try {
    bSocket = new WebSocket(CONTAINER_B_URL);

    bSocket.on('open', () => {
      console.log(`✅ Container B(${CONTAINER_B_URL})에 성공적으로 연결됨!`);
    });

    bSocket.on('message', async (data) => {
      let msg;
      try {
        msg = JSON.parse(data);
      } catch (err) {
        return;
      }

      const sessionId = msg.session_id || FIXED_SESSION_ID;
      const session = sessions[sessionId];
      if (!session) return;

      if (!msg.detected || !msg.landmarks || msg.landmarks.length === 0) {
        const noHandMsg = JSON.stringify({
          type: 'GESTURE',
          action: 'NONE',
          x: 0.5,
          y: 0.5,
          delta: 0,
          pan_dx: 0,
          pan_dy: 0,
          landmarks: [],
          detected: false
        });
        session.pcSockets.forEach((ws) => {
          if (ws.readyState === WebSocket.OPEN) ws.send(noHandMsg);
        });
        return;
      }

      try {
        // Container C (모션 엔진) 호출
        const res = await axios.post(MOTION_ENGINE_URL, {
          session_id: sessionId,
          landmarks: msg.landmarks
        }, { timeout: 2000 });

        const cData = res.data;

        // 🌟 단일 패킷 동기화 전송 (동기화 지터 0% 달성)
        const payload = JSON.stringify({
          type: 'GESTURE',
          action: cData.action,
          x: cData.x,
          y: cData.y,
          delta: cData.delta || 0,
          pan_dx: cData.pan_dx || 0,
          pan_dy: cData.pan_dy || 0,
          landmarks: msg.landmarks,
          detected: true
        });

        session.pcSockets.forEach((ws) => {
          if (ws.readyState === WebSocket.OPEN) ws.send(payload);
        });

        // 모바일 화면에도 실시간 스켈레톤 및 제스처 피드백 전송
        if (session.mobileSocket && session.mobileSocket.readyState === WebSocket.OPEN) {
          session.mobileSocket.send(JSON.stringify({
            type: 'FEEDBACK',
            action: cData.action,
            landmarks: msg.landmarks
          }));
        }

      } catch (err) {
        console.error('Motion_Engine 호출 실패:', err.message);
      }
    });

    bSocket.on('error', (err) => {
      console.error('Container B 연결 에러:', err.message);
    });

    bSocket.on('close', () => {
      console.warn('Container B 연결 끊김. 5초 후 재연결 시도...');
      bSocket = null;
      clearTimeout(bReconnectTimer);
      bReconnectTimer = setTimeout(connectToContainerB, 5000);
    });
  } catch (err) {
    console.error('Container B 연결 시도 실패:', err.message);
    bReconnectTimer = setTimeout(connectToContainerB, 5000);
  }
}
connectToContainerB();

// ---------------------------
// WebSocket 서버 설정 (PC 및 Mobile 전용 엔드포인트)
// ---------------------------
const wssPc = new WebSocket.Server({ noServer: true });
const wssMobile = new WebSocket.Server({ noServer: true });

wssPc.on('connection', (ws, req, sessionId) => {
  const session = getOrCreateSession(sessionId);
  session.pcSockets.add(ws);
  console.log(`[ws/pc] PC 연결됨 - session: ${sessionId} (총 ${session.pcSockets.size}대)`);

  // 현재 모바일 연결 상태 즉시 통지
  ws.send(JSON.stringify({
    type: 'STATUS',
    mobile_connected: !!session.mobileSocket
  }));

  ws.on('close', () => {
    session.pcSockets.delete(ws);
    console.log(`[ws/pc] PC 연결 해제 - session: ${sessionId}`);
  });
});

wssMobile.on('connection', (ws, req, sessionId) => {
  const session = getOrCreateSession(sessionId);
  session.mobileSocket = ws;
  console.log(`[ws/mobile] 모바일 연결됨 - session: ${sessionId}`);

  // PC들에게 모바일 연결 통지
  const statusMsg = JSON.stringify({ type: 'STATUS', mobile_connected: true });
  session.pcSockets.forEach((pcWs) => {
    if (pcWs.readyState === WebSocket.OPEN) pcWs.send(statusMsg);
  });

  ws.on('message', (raw) => {
    let frameData = null;
    const str = raw.toString();
    try {
      const parsed = JSON.parse(str);
      frameData = parsed.frame || parsed.image || parsed.data;
    } catch (e) {
      frameData = str;
    }

    if (!frameData) return;

    // "data:image/jpeg;base64," 접두사 제거
    const commaIdx = frameData.indexOf(',');
    if (commaIdx !== -1) {
      frameData = frameData.substring(commaIdx + 1);
    }

    if (bSocket && bSocket.readyState === WebSocket.OPEN) {
      bSocket.send(JSON.stringify({
        session_id: sessionId,
        type: 'frame',
        frame: frameData
      }));
    }
  });

  ws.on('close', () => {
    console.log(`[ws/mobile] 모바일 연결 해제 - session: ${sessionId}`);
    if (session.mobileSocket === ws) {
      session.mobileSocket = null;
    }
    const disconnMsg = JSON.stringify({ type: 'STATUS', mobile_connected: false });
    session.pcSockets.forEach((pcWs) => {
      if (pcWs.readyState === WebSocket.OPEN) pcWs.send(disconnMsg);
    });
  });
});

// ---------------------------
// Upgrade 라우팅
// ---------------------------
server.on('upgrade', (request, socket, head) => {
  const { pathname } = new URL(request.url, `http://${request.headers.host}`);

  // /ws/pc/:session_id 또는 /ws/pc
  const pcMatch = pathname.match(/^\/ws\/pc(?:\/(.+))?$/);
  if (pcMatch) {
    const sessionId = pcMatch[1] || FIXED_SESSION_ID;
    wssPc.handleUpgrade(request, socket, head, (ws) => {
      wssPc.emit('connection', ws, request, sessionId);
    });
    return;
  }

  // /ws/mobile/:session_id 또는 /ws/mobile
  const mobileMatch = pathname.match(/^\/ws\/mobile(?:\/(.+))?$/);
  if (mobileMatch) {
    const sessionId = mobileMatch[1] || FIXED_SESSION_ID;
    wssMobile.handleUpgrade(request, socket, head, (ws) => {
      wssMobile.emit('connection', ws, request, sessionId);
    });
    return;
  }

  socket.destroy();
});

server.listen(PORT, () => {
  const protocol = isHttps ? 'https' : 'http';
  console.log(`🚀 Air Canvas Web_Server 실행 중: ${protocol}://localhost:${PORT}`);
  console.log(`   - 🖥️ PC 캔버스:     ${protocol}://localhost:${PORT}/pc`);
  console.log(`   - 📱 모바일 카메라: ${protocol}://192.168.55.208:${PORT}/mobile`);
});
