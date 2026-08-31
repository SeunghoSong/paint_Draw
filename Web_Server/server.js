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
const pino = require('pino');

const app = express();

// 로깅: console.log/warn/error 혼용 대신 구조화 로거 도입. LOG_LEVEL 환경변수로 운영/개발 전환
// (기존엔 레벨 제어 자체가 불가능 - console.*는 전부 무조건 출력됨).
// Python 엔진은 logging 모듈 표기(WARNING)를 쓰지만 pino는 'warn'만 인식하므로,
// 공통 LOG_LEVEL 값 하나(info/warning/error/debug 등)를 양쪽 다 쓸 수 있도록 여기서 정규화.
const LOG_LEVEL = (process.env.LOG_LEVEL || 'info').toLowerCase().replace('warning', 'warn');
const logger = pino({
  level: LOG_LEVEL,
  // 로깅: docs/02-log-field-schema.md에서 지적한 남은 격차 해소.
  // (1) pino 기본값은 level이 숫자(30/40/50)라 Python의 문자열 레벨("INFO" 등)과 안 맞았음 → 대문자 문자열로 통일
  // (2) pino 기본 타임스탬프 키는 "time"인데 Python은 "ts"를 씀 → 키 이름을 "ts"로 맞춤
  formatters: {
    level: (label) => ({ level: label.toUpperCase() }),
  },
  timestamp: () => `,"ts":${Date.now()}`,
});

// 로깅: Python 엔진(Video_Engine/Motion_Engine)의 log_event()와 동일한 필드 구조
// (container/session_id/trace_id/event/detail)로 통일 - 3개 컨테이너 로그를 나중에 한곳에서
// 봐도 일관되게 파싱 가능하도록. trace_id는 요청(프레임) 단위 추적용 — session_id(세션 전체)와는
// 다른 축이라 별도 필드로 둠.
function logEvent(level, sessionId, event, detail = {}, traceId = null) {
  logger[level]({ container: 'A', session_id: sessionId, trace_id: traceId, event, detail });
  maybeAlertOnError(level, event, detail); // 로깅: 알림 정책(04번 문서) 반영
}

// 로깅: 알림 정책(docs/04-alert-policy.md) 구현. SLACK_WEBHOOK_URL이 비어있으면 완전히 비활성 —
// 실제 채널/웹훅은 팀이 정해서 넣어야 하는 값이라 여기서 아무 것도 지어내지 않음.
const SLACK_WEBHOOK_URL = process.env.SLACK_WEBHOOK_URL || '';
// 문서 규칙: "ERROR 반복 발생: 5분간 N회 이상". N(임계치)은 문서에도 "실제 트래픽 보고 정할 값"이라
// 적어뒀던 값이라, 여기 기본값은 어디까지나 안전한 초기값이고 실사용 트래픽을 보고 조정해야 함.
const ALERT_ERROR_WINDOW_MS = Number(process.env.ALERT_ERROR_WINDOW_MS) || 5 * 60 * 1000;
const ALERT_ERROR_THRESHOLD = Number(process.env.ALERT_ERROR_THRESHOLD) || 10;

let errorWindowStart = Date.now();
let errorWindowCount = 0;

function sendSlackAlert(text) {
  if (!SLACK_WEBHOOK_URL) return;
  axios.post(SLACK_WEBHOOK_URL, { text }).catch((err) => {
    logger.error({ err: err.message }, 'Slack 알림 전송 실패');
  });
}

// 디바운싱/그룹핑: 임계치를 "넘는 순간"에만 1번 보내고(===로 체크), 창이 갈릴 때만 다시 셈 —
// 같은 창 안에서 에러가 계속 나도 알림이 반복 발송되지 않는다(문서의 "같은 에러 반복 시 묶어서 1번만").
function maybeAlertOnError(level, event, detail) {
  if (!SLACK_WEBHOOK_URL) return;

  if (event === 'circuit_breaker_open') {
    // 문서 규칙: circuit_breaker_open은 발생 즉시 1회 — 쿨다운(CIRCUIT_BREAKER_COOLDOWN_MS) 동안은
    // 재발생 자체가 없으므로 별도 디바운싱 없이도 자연히 스팸이 안 됨.
    sendSlackAlert(`:warning: [Web_Server] 서킷브레이커 오픈 - ${JSON.stringify(detail)}`);
    return;
  }

  if (level !== 'error') return;

  const now = Date.now();
  if (now - errorWindowStart > ALERT_ERROR_WINDOW_MS) {
    errorWindowStart = now;
    errorWindowCount = 0;
  }
  errorWindowCount += 1;

  if (errorWindowCount === ALERT_ERROR_THRESHOLD) {
    sendSlackAlert(
      `:rotating_light: [Web_Server] 최근 ${Math.round(ALERT_ERROR_WINDOW_MS / 1000)}초 동안 `
      + `ERROR ${ALERT_ERROR_THRESHOLD}회 발생 (마지막 이벤트: ${event})`
    );
  }
}

// 예외처리: 처리되지 않은 예외/프로미스 거부로 프로세스가 알 수 없는 상태로 남는 것 방지.
// uncaughtException은 상태가 오염됐을 수 있어 로그 남기고 종료 → docker-compose의 restart 정책이 깨끗한 상태로 재기동시킴.
// unhandledRejection은 상대적으로 격리된 실패(주로 axios/ws 호출 실패)라 로그만 남기고 계속 운영.
process.on('uncaughtException', (err) => {
  // console.error('🔥 uncaughtException:', err); // 로깅 이전: console.error 사용
  logger.error({ err }, 'uncaughtException');
  maybeAlertOnError('error', 'uncaught_exception', { message: err.message });
  // 로깅: Slack 전송은 비동기라 process.exit()이 먼저 실행되면 요청이 안 나갈 수 있어 살짝 지연 후 종료
  setTimeout(() => process.exit(1), SLACK_WEBHOOK_URL ? 1000 : 0);
});
process.on('unhandledRejection', (reason) => {
  // console.error('🔥 unhandledRejection:', reason); // 로깅 이전: console.error 사용
  logger.error({ reason }, 'unhandledRejection');
  maybeAlertOnError('error', 'unhandled_rejection', { reason: String(reason) });
});

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
    // console.log('🔒 HTTPS 자체 서명 SSL 인증서 자동 생성 중...'); // 로깅 이전: console.log 사용
    logger.info('🔒 HTTPS 자체 서명 SSL 인증서 자동 생성 중...');
    execSync(`openssl req -x509 -newkey rsa:2048 -nodes -keyout "${keyPath}" -out "${certPath}" -days 365 -subj "/CN=AirCanvas"`, { stdio: 'ignore' });
    // console.log('✅ SSL 인증서 생성 완료!'); // 로깅 이전: console.log 사용
    logger.info('✅ SSL 인증서 생성 완료!');
  } catch (err) {
    // console.warn('⚠️ OpenSSL 생성 실패 (HTTP 대체 실행):', err.message); // 로깅 이전: console.warn 사용
    logger.warn({ err: err.message }, '⚠️ OpenSSL 생성 실패 (HTTP 대체 실행)');
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
// 파라미터화: 인프라성 값(포트/URL/타임아웃/기본 세션ID/호스트) 전부 환경변수로 분리
const PORT = process.env.PORT || 8000;
const CONTAINER_B_URL = process.env.CONTAINER_B_URL || 'ws://video_engine:8001/ws';
const MOTION_ENGINE_URL = process.env.MOTION_ENGINE_URL || 'http://motion_engine:8002/gesture';
// const FIXED_SESSION_ID = 'poc-001'; // 파라미터화 이전 하드코딩 값
const FIXED_SESSION_ID = process.env.DEFAULT_SESSION_ID || 'poc-001';
// (기존에는 아래 두 곳에서 '192.168.55.208'을 직접 하드코딩해서 사용) // 파라미터화: 상수로 통합 + 환경변수화
const DEFAULT_HOST = process.env.DEFAULT_HOST || '192.168.55.208';
// (기존: setTimeout(connectToContainerB, 5000) 하드코딩) // 파라미터화
const RECONNECT_DELAY_MS = Number(process.env.RECONNECT_DELAY_MS) || 5000;
// (기존: axios.post(..., { timeout: 2000 }) 하드코딩) // 파라미터화
const MOTION_ENGINE_TIMEOUT_MS = Number(process.env.MOTION_ENGINE_TIMEOUT_MS) || 2000;
// const JSON_BODY_LIMIT = '10mb'; // 파라미터화 이전 하드코딩 값
const JSON_BODY_LIMIT = process.env.JSON_BODY_LIMIT || '10mb';

// 예외처리: Motion_Engine 서킷브레이커 - 연속 실패가 임계치를 넘으면 잠시 호출을 끊고 즉시 NONE으로 응답
const CIRCUIT_BREAKER_FAILURE_THRESHOLD = Number(process.env.CIRCUIT_BREAKER_FAILURE_THRESHOLD) || 5;
const CIRCUIT_BREAKER_COOLDOWN_MS = Number(process.env.CIRCUIT_BREAKER_COOLDOWN_MS) || 10000;
// 예외처리: idle 세션(소켓 다 끊긴 채 방치된 세션) 정리 주기/기준
const SESSION_IDLE_TIMEOUT_MS = Number(process.env.SESSION_IDLE_TIMEOUT_MS) || 30 * 60 * 1000;
const SESSION_SWEEP_INTERVAL_MS = Number(process.env.SESSION_SWEEP_INTERVAL_MS) || 5 * 60 * 1000;
// 예외처리: 세션별 프레임 수신/전달/드롭 카운터를 주기적으로 로그로 남김 (프레임 유실률 파악용)
const FRAME_STATS_LOG_INTERVAL_MS = Number(process.env.FRAME_STATS_LOG_INTERVAL_MS) || 30000;

app.use(cors());
app.use(express.json({ limit: JSON_BODY_LIMIT })); // 파라미터화: express.json({ limit: '10mb' }) 대체
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
      // 예외처리: idle 세션 정리 시 사용하는 마지막 활동 시각
      lastActivity: Date.now(),
      // 예외처리: 프레임 backpressure - 이전 프레임 처리 결과가 아직 안 돌아왔으면 새 프레임은 포워딩하지 않고 드롭
      frameInFlight: false,
      // 예외처리: 프레임 유실률 파악용 카운터 (주기적으로 로그 남기고 리셋)
      framesReceived: 0,
      framesForwarded: 0,
      framesDropped: 0,
    };
  }
  sessions[sessionId].lastActivity = Date.now(); // 예외처리: 세션 조회/재사용 시점마다 갱신
  return sessions[sessionId];
}

// 예외처리: 소켓이 하나도 안 남고 일정 시간 이상 방치된 세션을 주기적으로 정리 (메모리 누수 방지)
setInterval(() => {
  const now = Date.now();
  for (const [sessionId, session] of Object.entries(sessions)) {
    const hasSockets = session.pcSockets.size > 0 || session.mobileSocket !== null;
    if (!hasSockets && now - session.lastActivity > SESSION_IDLE_TIMEOUT_MS) {
      delete sessions[sessionId];
      // console.log(`[session] idle 세션 정리: ${sessionId}`); // 로깅 이전: console.log 사용
      logEvent('info', sessionId, 'session_cleaned', {});
    }
  }
}, SESSION_SWEEP_INTERVAL_MS);

// 예외처리: 세션별 프레임 수신/전달/드롭 카운터를 주기적으로 로그로 남기고 리셋 (프레임 유실률 파악용)
setInterval(() => {
  for (const [sessionId, session] of Object.entries(sessions)) {
    if (session.framesReceived === 0) continue;
    // console.log(`[frame-stats] session=${sessionId} received=... forwarded=... dropped=...`); // 로깅 이전: console.log 사용
    logEvent('info', sessionId, 'frame_stats', {
      received: session.framesReceived,
      forwarded: session.framesForwarded,
      dropped: session.framesDropped,
    });
    session.framesReceived = 0;
    session.framesForwarded = 0;
    session.framesDropped = 0;
  }
}, FRAME_STATS_LOG_INTERVAL_MS);

// 예외처리: docker-compose healthcheck가 사용하는 엔드포인트 (기존엔 아예 없었음)
app.get('/health', (req, res) => res.json({ status: 'ok', service: 'web_server' }));

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
  
  // const host = req.query.host || '192.168.55.208'; // 파라미터화 이전 하드코딩 값
  const host = req.query.host || DEFAULT_HOST; // 파라미터화: DEFAULT_HOST 환경변수 사용
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

// 예외처리: Motion_Engine 서킷브레이커 상태 (프로세스 전역 - Motion_Engine 자체의 장애이므로 세션별로 나눌 이유가 없음)
let motionEngineConsecutiveFailures = 0;
let motionEngineCircuitOpenUntil = 0;

function connectToContainerB() {
  try {
    bSocket = new WebSocket(CONTAINER_B_URL);

    bSocket.on('open', () => {
      // console.log(`✅ Container B(${CONTAINER_B_URL})에 성공적으로 연결됨!`); // 로깅 이전: console.log 사용
      logger.info({ url: CONTAINER_B_URL }, '✅ Container B에 성공적으로 연결됨');
    });

    bSocket.on('message', async (data) => {
      let msg;
      try {
        msg = JSON.parse(data);
      } catch (err) {
        return;
      }

      const sessionId = msg.session_id || FIXED_SESSION_ID;
      const traceId = msg.trace_id || null; // 로깅: Video_Engine이 그대로 돌려준 trace_id
      const session = sessions[sessionId];
      if (!session) return;

      // 예외처리: 이 세션으로 포워딩했던 프레임의 응답이 돌아온 시점 → backpressure 플래그 해제 (다음 프레임 포워딩 허용)
      session.frameInFlight = false;

      const sendNoHand = () => {
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
      };

      if (!msg.detected || !msg.landmarks || msg.landmarks.length === 0) {
        sendNoHand();
        return;
      }

      // 예외처리: 서킷브레이커 - 연속 실패 임계치를 넘었으면 쿨다운 동안 Motion_Engine 호출 자체를 건너뛰고 즉시 NONE 응답
      if (Date.now() < motionEngineCircuitOpenUntil) {
        sendNoHand();
        return;
      }

      try {
        // Container C (모션 엔진) 호출
        // const res = await axios.post(MOTION_ENGINE_URL, { session_id: sessionId, landmarks: msg.landmarks }, { timeout: 2000 }); // 파라미터화 이전 하드코딩 값
        const res = await axios.post(MOTION_ENGINE_URL, {
          session_id: sessionId,
          landmarks: msg.landmarks,
          trace_id: traceId, // 로깅: Motion_Engine이 body에서 읽어 자기 로그에도 같은 trace_id를 남김
        }, {
          timeout: MOTION_ENGINE_TIMEOUT_MS, // 파라미터화: MOTION_ENGINE_TIMEOUT_MS 환경변수 사용
          headers: traceId ? { 'X-Trace-Id': traceId } : undefined, // 로깅: raw HTTP 로그/프록시에서도 보이도록 헤더로도 전달(body가 source of truth)
        });

        motionEngineConsecutiveFailures = 0; // 예외처리: 성공하면 연속 실패 카운트 리셋

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
        // console.error('Motion_Engine 호출 실패:', err.message); // 로깅 이전: console.error 사용
        logEvent('error', sessionId, 'motion_engine_call_failed', { error: err.message }, traceId);
        // 예외처리: 연속 실패가 임계치를 넘으면 서킷브레이커 오픈 (쿨다운 동안 호출 스킵)
        motionEngineConsecutiveFailures += 1;
        if (motionEngineConsecutiveFailures >= CIRCUIT_BREAKER_FAILURE_THRESHOLD) {
          motionEngineCircuitOpenUntil = Date.now() + CIRCUIT_BREAKER_COOLDOWN_MS;
          // console.warn(`⚡ Motion_Engine 서킷브레이커 오픈 (${CIRCUIT_BREAKER_COOLDOWN_MS}ms 동안 호출 스킵)`); // 로깅 이전: console.warn 사용
          logEvent('warn', sessionId, 'circuit_breaker_open', { cooldown_ms: CIRCUIT_BREAKER_COOLDOWN_MS }, traceId);
        }
        sendNoHand();
      }
    });

    bSocket.on('error', (err) => {
      // console.error('Container B 연결 에러:', err.message); // 로깅 이전: console.error 사용
      logger.error({ err: err.message }, 'Container B 연결 에러');
      maybeAlertOnError('error', 'container_b_connection_error', { error: err.message });
    });

    bSocket.on('close', () => {
      // console.warn('Container B 연결 끊김. 5초 후 재연결 시도...'); // 로깅 이전: console.warn 사용
      logger.warn({ retry_delay_ms: RECONNECT_DELAY_MS }, 'Container B 연결 끊김, 재연결 예정');
      bSocket = null;
      clearTimeout(bReconnectTimer);
      // bReconnectTimer = setTimeout(connectToContainerB, 5000); // 파라미터화 이전 하드코딩 값
      bReconnectTimer = setTimeout(connectToContainerB, RECONNECT_DELAY_MS); // 파라미터화: RECONNECT_DELAY_MS 환경변수 사용
    });
  } catch (err) {
    // console.error('Container B 연결 시도 실패:', err.message); // 로깅 이전: console.error 사용
    logger.error({ err: err.message }, 'Container B 연결 시도 실패');
    maybeAlertOnError('error', 'container_b_connect_failed', { error: err.message });
    // bReconnectTimer = setTimeout(connectToContainerB, 5000); // 파라미터화 이전 하드코딩 값
    bReconnectTimer = setTimeout(connectToContainerB, RECONNECT_DELAY_MS); // 파라미터화: RECONNECT_DELAY_MS 환경변수 사용
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
  // console.log(`[ws/pc] PC 연결됨 - session: ${sessionId} (총 ${session.pcSockets.size}대)`); // 로깅 이전: console.log 사용
  logEvent('info', sessionId, 'ws_connected', { peer: 'pc', total_pc_sockets: session.pcSockets.size });

  // 현재 모바일 연결 상태 즉시 통지
  ws.send(JSON.stringify({
    type: 'STATUS',
    mobile_connected: !!session.mobileSocket
  }));

  ws.on('close', () => {
    session.pcSockets.delete(ws);
    // console.log(`[ws/pc] PC 연결 해제 - session: ${sessionId}`); // 로깅 이전: console.log 사용
    logEvent('info', sessionId, 'ws_disconnected', { peer: 'pc' });
  });
});

wssMobile.on('connection', (ws, req, sessionId) => {
  const session = getOrCreateSession(sessionId);

  // 예외처리: 네트워크 전환(WiFi↔LTE) 등으로 재연결할 때 기존 소켓이 안 끊긴 채 남아있으면 정리 (좀비 소켓 방지)
  if (session.mobileSocket && session.mobileSocket !== ws && session.mobileSocket.readyState === WebSocket.OPEN) {
    session.mobileSocket.terminate();
  }
  session.mobileSocket = ws;
  session.frameInFlight = false; // 예외처리: 재연결 시 이전 세션의 backpressure 상태를 이어받지 않도록 리셋
  // console.log(`[ws/mobile] 모바일 연결됨 - session: ${sessionId}`); // 로깅 이전: console.log 사용
  logEvent('info', sessionId, 'ws_connected', { peer: 'mobile' });

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

    session.framesReceived += 1; // 예외처리: 프레임 유실률 파악용 카운터

    // 예외처리: backpressure - 직전에 포워딩한 프레임의 처리 결과가 아직 안 돌아왔으면 이번 프레임은 드롭.
    // (Video_Engine 쪽 처리가 프레임 유입 속도를 못 따라갈 때 큐가 무한정 쌓이는 걸 막기 위한 최소한의 안전장치)
    if (session.frameInFlight) {
      session.framesDropped += 1;
      return;
    }

    // "data:image/jpeg;base64," 접두사 제거
    const commaIdx = frameData.indexOf(',');
    if (commaIdx !== -1) {
      frameData = frameData.substring(commaIdx + 1);
    }

    if (bSocket && bSocket.readyState === WebSocket.OPEN) {
      session.frameInFlight = true; // 예외처리: 응답(bSocket 'message')이 돌아올 때까지 다음 프레임은 대기
      session.framesForwarded += 1;
      // 로깅: 이 프레임 하나가 A→B→(A→C)로 흘러가는 전체 과정을 하나의 trace_id로 묶어서 추적
      // 가능하게 함(session_id는 세션 전체를 묶는 축이라 프레임 단위 추적엔 안 맞음). Video_Engine이
      // 응답(landmarks 메시지)에 같은 trace_id를 그대로 실어 돌려준다.
      const traceId = uuidv4();
      bSocket.send(JSON.stringify({
        session_id: sessionId,
        type: 'frame',
        frame: frameData,
        trace_id: traceId,
      }));
    }
  });

  ws.on('close', () => {
    // console.log(`[ws/mobile] 모바일 연결 해제 - session: ${sessionId}`); // 로깅 이전: console.log 사용
    logEvent('info', sessionId, 'ws_disconnected', { peer: 'mobile' });
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
  // 로깅 이전: 아래 3줄 모두 console.log 사용
  logger.info(`🚀 Air Canvas Web_Server 실행 중: ${protocol}://localhost:${PORT}`);
  logger.info(`   - 🖥️ PC 캔버스:     ${protocol}://localhost:${PORT}/pc`);
  // console.log(`   - 📱 모바일 카메라: ${protocol}://192.168.55.208:${PORT}/mobile`); // 파라미터화 이전 하드코딩 값
  logger.info(`   - 📱 모바일 카메라: ${protocol}://${DEFAULT_HOST}:${PORT}/mobile`); // 파라미터화: DEFAULT_HOST 환경변수 사용
});
