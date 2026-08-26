const express = require('express');
const http = require('http');
const path = require('path');
const cors = require('cors');
const QRCode = require('qrcode');
const { v4: uuidv4 } = require('uuid');
const { Server } = require('socket.io');
const WebSocket = require('ws');

const app = express();
const server = http.createServer(app);
const io = new Server(server, { cors: { origin: '*' } });

// ---------------------------
// 설정값 (PM 계획서 스펙 기준, 팀원과 합의된 값으로 필요시 수정)
// ---------------------------
const PORT = process.env.PORT || 8000;
const CONTAINER_B_URL = process.env.CONTAINER_B_URL || 'ws://localhost:8001/analyze'; // 영상 분석 엔진
const CANVAS_WIDTH = 1280;   // Container C가 좌표 스케일링할 때 쓰는 기준 해상도
const CANVAS_HEIGHT = 720;   // 주의: Container C 담당자와 반드시 이 값 합의할 것
const FIXED_SESSION_ID = 'poc-001'; // PM 계획서: 고정 세션 ID 하드코딩

app.use(cors());
app.use(express.json({ limit: '5mb' })); // base64 프레임 실어야 하니 limit 늘려둠
app.use(express.static(path.join(__dirname, 'public')));

// ---------------------------
// 세션 저장소 (메모리, 데모용)
// ---------------------------
const sessions = {};

function ensureSession(sessionId) {
  if (!sessions[sessionId]) {
    sessions[sessionId] = { createdAt: Date.now(), pcSocketId: null, mobileSocketId: null };
  }
  return sessions[sessionId];
}

// POC 고정 세션 미리 생성
ensureSession(FIXED_SESSION_ID);

// ---------------------------
// 페이지 라우트 (PM 계획서 네이밍에 맞춤: /pc, /mobile)
// ---------------------------
app.get('/pc', (req, res) => res.sendFile(path.join(__dirname, 'public', 'index.html')));
app.get('/mobile', (req, res) => res.sendFile(path.join(__dirname, 'public', 'mobile.html')));

// 기존 QR 기반 동적 세션 생성 (선택 기능, 필요시 사용)
app.get('/api/session', (req, res) => {
  const sessionId = uuidv4().slice(0, 8);
  ensureSession(sessionId);
  res.json({ sessionId });
});

app.get('/api/qr', async (req, res) => {
  const { session } = req.query;
  if (!session || !sessions[session]) {
    return res.status(400).json({ error: '유효하지 않은 session id 입니다.' });
  }
  const baseUrl = `${req.protocol}://${req.get('host')}`;
  const mobileUrl = `${baseUrl}/mobile?session=${session}`;
  try {
    const qrDataUrl = await QRCode.toDataURL(mobileUrl, { width: 300 });
    res.json({ qrDataUrl, mobileUrl });
  } catch (err) {
    res.status(500).json({ error: 'QR 생성 실패' });
  }
});

app.get('/api/config', (req, res) => {
  // Container C가 캔버스 해상도를 알아야 좌표 스케일링 가능
  res.json({ canvasWidth: CANVAS_WIDTH, canvasHeight: CANVAS_HEIGHT, fixedSessionId: FIXED_SESSION_ID });
});

// ---------------------------
// Socket.io - PC/모바일 브라우저 UI용 (연결 상태, 프리뷰 등)
// ---------------------------
io.on('connection', (socket) => {
  socket.on('join-as-pc', (sessionId) => {
    sessionId = sessionId || FIXED_SESSION_ID;
    ensureSession(sessionId);
    sessions[sessionId].pcSocketId = socket.id;
    socket.join(sessionId);
    console.log(`[socket.io] PC 연결됨 - session: ${sessionId}`);
  });

  socket.on('join-as-mobile', (sessionId) => {
    sessionId = sessionId || FIXED_SESSION_ID;
    ensureSession(sessionId);
    sessions[sessionId].mobileSocketId = socket.id;
    socket.join(sessionId);
    console.log(`[socket.io] 모바일 연결됨 - session: ${sessionId}`);
    socket.to(sessionId).emit('mobile-connected');
  });

  socket.on('disconnect', () => {
    for (const id in sessions) {
      if (sessions[id].pcSocketId === socket.id) sessions[id].pcSocketId = null;
      if (sessions[id].mobileSocketId === socket.id) sessions[id].mobileSocketId = null;
    }
  });
});

// ---------------------------
// [4.2] A -> Container B 연결 (내부 WebSocket 클라이언트)
// A는 B에 대해 클라이언트 역할. 프레임을 B로 전달만 함.
// ---------------------------
let bSocket = null;
let bReconnectTimer = null;

function connectToContainerB() {
  try {
    bSocket = new WebSocket(CONTAINER_B_URL);

    bSocket.on('open', () => {
      console.log(`Container B(${CONTAINER_B_URL})에 연결됨`);
    });

    bSocket.on('message', (data) => {
      // 참고용: B->C는 직접 연결이라 A가 굳이 처리 안 해도 됨. 디버깅용 로그만.
      // console.log('B로부터 응답:', data.toString());
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
// [4.1] 모바일 <-> Container A : 프레임 수신용 순수 WebSocket 서버
// 경로: ws://<host>:8000/ws/mobile
// 수신 포맷: {"session_id": "poc-001", "frame": "<base64 jpeg>"}
// ---------------------------
const wssMobile = new WebSocket.Server({ noServer: true });

wssMobile.on('connection', (ws) => {
  console.log('[ws] 모바일 프레임 채널 연결됨');

  ws.on('message', (raw) => {
    let msg;
    try {
      msg = JSON.parse(raw);
    } catch (err) {
      console.error('모바일 프레임 JSON 파싱 실패:', err.message);
      return;
    }

    const sessionId = msg.session_id || FIXED_SESSION_ID;
    const frame = msg.frame;
    if (!frame) return;

    // Container B로 그대로 전달 (session_id, frame 동일 스키마)
    if (bSocket && bSocket.readyState === WebSocket.OPEN) {
      bSocket.send(JSON.stringify({ session_id: sessionId, frame }));
    }
  });

  ws.on('close', () => console.log('[ws] 모바일 프레임 채널 종료'));
  ws.on('error', (err) => console.error('[ws] 모바일 채널 에러:', err.message));
});

// ---------------------------
// [4.4] Container C <-> Container A : 제어 명령 수신용 순수 WebSocket 서버
// 경로: ws://<host>:8000/ws/command
// 수신 포맷: {"session_id": "poc-001", "cmd": "DRAW"|"ERASE"|"NONE", "x": int, "y": int}
// ---------------------------
const wssCommand = new WebSocket.Server({ noServer: true });

wssCommand.on('connection', (ws) => {
  console.log('[ws] Container C 명령 채널 연결됨');

  ws.on('message', (raw) => {
    let msg;
    try {
      msg = JSON.parse(raw);
    } catch (err) {
      console.error('명령 JSON 파싱 실패:', err.message);
      return;
    }

    const sessionId = msg.session_id || FIXED_SESSION_ID;
    const { cmd, x, y } = msg;
    if (!cmd) return;

    // 해당 세션의 PC 브라우저로 명령 전달 (Socket.io 통해 캔버스 렌더링)
    io.to(sessionId).emit('control-command', { cmd, x, y });
  });

  ws.on('close', () => console.log('[ws] Container C 명령 채널 종료'));
  ws.on('error', (err) => console.error('[ws] 명령 채널 에러:', err.message));
});

// ---------------------------
// HTTP 서버에 두 WebSocket 경로 라우팅 연결
// ---------------------------
server.on('upgrade', (request, socket, head) => {
  const { pathname } = new URL(request.url, `http://${request.headers.host}`);

  if (pathname === '/ws/mobile') {
    wssMobile.handleUpgrade(request, socket, head, (ws) => {
      wssMobile.emit('connection', ws, request);
    });
  } else if (pathname === '/ws/command') {
    wssCommand.handleUpgrade(request, socket, head, (ws) => {
      wssCommand.emit('connection', ws, request);
    });
  }
  // socket.io 자체 upgrade 요청은 socket.io 내부에서 이미 처리하므로 여기선 별도 처리 안 함
});

server.listen(PORT, () => {
  console.log(`Container A 서버 실행 중: http://localhost:${PORT}`);
  console.log(`   - PC 페이지:      http://localhost:${PORT}/pc`);
  console.log(`   - 모바일 페이지:  http://localhost:${PORT}/mobile`);
  console.log(`   - 고정 세션 ID:   ${FIXED_SESSION_ID}`);
  console.log(`   - 모바일 프레임:  ws://localhost:${PORT}/ws/mobile`);
  console.log(`   - 명령 수신:      ws://localhost:${PORT}/ws/command`);
  console.log(`ngrok 쓸 경우: ngrok http ${PORT}`);
});
