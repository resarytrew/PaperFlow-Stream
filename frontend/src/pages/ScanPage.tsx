import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, wsUrl } from "../api/client";
import type { ScanResultMessage, ScanSession, ScanStateMessage } from "../api/types";
import { captureFrame, useCamera } from "../hooks/useCamera";
import { Badge, SESSION_STATUS_RU, useApi } from "../lib";

const FRAME_INTERVAL_MS = 120; // ~8 fps to the analyser; capped further by "busy" flow control
const FRAME_MAX_WIDTH = 1280;
const FRAME_QUALITY = 0.8;

interface RecentSheet {
  id: number | null;
  ok: boolean;
  label: string;
  sub: string;
  thumbnail: string | null;
  at: number;
}

export default function ScanPage() {
  const { id } = useParams();
  const sessionId = Number(id);
  const session = useApi<ScanSession>(() => api.get(`/sessions/${sessionId}`), [sessionId]);

  const camera = useCamera();
  const videoRef = useRef<HTMLVideoElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<number | null>(null);
  const awaitingRef = useRef(false);

  const [connected, setConnected] = useState(false);
  const [scanning, setScanning] = useState(false);
  const scanningRef = useRef(false);
  const [prompt, setPrompt] = useState("ПОЛОЖИТЕ ЛИСТ");
  const [promptColor, setPromptColor] = useState("neutral");
  const [hints, setHints] = useState<string[]>([]);
  const [counters, setCounters] = useState<Record<string, number>>({});
  const [speed, setSpeed] = useState(0);
  const [analysisMs, setAnalysisMs] = useState(0);
  const [wsError, setWsError] = useState<string | null>(null);
  const [calibrated, setCalibrated] = useState<boolean | null>(null);
  const [recent, setRecent] = useState<RecentSheet[]>([]);

  // Attach camera stream to the <video>.
  useEffect(() => {
    if (videoRef.current && camera.stream) {
      videoRef.current.srcObject = camera.stream;
      videoRef.current.play().catch(() => undefined);
    }
  }, [camera.stream]);

  const drawOverlay = useCallback((msg: ScanStateMessage) => {
    const canvas = overlayRef.current;
    const video = videoRef.current;
    if (!canvas || !video || !video.videoWidth) return;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // The analysed frame is a scaled copy — the quad arrives in the coordinates
    // of the frame we sent, so rescale to full video resolution.
    const sent = Math.min(1, FRAME_MAX_WIDTH / video.videoWidth);
    const inv = 1 / sent;

    const colors: Record<string, string> = {
      green: "#22c55e",
      red: "#ef4444",
      amber: "#f59e0b",
      blue: "#3b82f6",
      neutral: "#94a3b8",
    };
    const stroke = colors[msg.color] ?? colors.neutral;

    if (msg.overlay.workArea) {
      ctx.strokeStyle = "rgba(148, 163, 184, 0.6)";
      ctx.setLineDash([10, 8]);
      ctx.lineWidth = 2;
      ctx.beginPath();
      msg.overlay.workArea.forEach(([x, y], i) => (i ? ctx.lineTo(x * inv, y * inv) : ctx.moveTo(x * inv, y * inv)));
      ctx.closePath();
      ctx.stroke();
      ctx.setLineDash([]);
    }

    if (msg.overlay.quad) {
      ctx.strokeStyle = stroke;
      ctx.lineWidth = 4;
      ctx.beginPath();
      msg.overlay.quad.forEach(([x, y], i) => (i ? ctx.lineTo(x * inv, y * inv) : ctx.moveTo(x * inv, y * inv)));
      ctx.closePath();
      ctx.stroke();

      if (msg.progress > 0 && msg.progress < 1) {
        const [x0, y0] = msg.overlay.quad[0];
        ctx.fillStyle = "rgba(0,0,0,0.6)";
        ctx.fillRect(x0 * inv, y0 * inv - 26, 160, 16);
        ctx.fillStyle = stroke;
        ctx.fillRect(x0 * inv, y0 * inv - 26, 160 * msg.progress, 16);
      }
    }
  }, []);

  const sendFrame = useCallback(() => {
    const ws = wsRef.current;
    const video = videoRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN || !video || awaitingRef.current || !scanningRef.current) return;
    const dataUrl = captureFrame(video, FRAME_MAX_WIDTH, FRAME_QUALITY);
    if (!dataUrl) return;
    awaitingRef.current = true;
    ws.send(JSON.stringify({ type: "frame", image: dataUrl }));
  }, []);

  const connect = useCallback(() => {
    if (wsRef.current) wsRef.current.close();
    setWsError(null);
    const ws = new WebSocket(wsUrl(`/ws/sessions/${sessionId}/scan`));
    wsRef.current = ws;

    ws.onopen = () => setConnected(true);
    ws.onclose = () => {
      setConnected(false);
      setScanning(false);
      scanningRef.current = false;
    };
    ws.onerror = () => setWsError("Соединение с сервером прервано.");

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      switch (msg.type) {
        case "ready":
          setCalibrated(Boolean(msg.calibrated));
          setCounters(msg.counters ?? {});
          break;
        case "state": {
          awaitingRef.current = false;
          const state = msg as ScanStateMessage;
          setPrompt(state.prompt);
          setPromptColor(state.color);
          setHints(state.hints ?? []);
          setCounters(state.counters ?? {});
          setSpeed(state.speed ?? 0);
          setAnalysisMs(state.overlay?.analysisMs ?? 0);
          drawOverlay(state);
          break;
        }
        case "scan_result": {
          const res = msg as ScanResultMessage;
          setCounters(res.counters ?? {});
          setSpeed(res.speed ?? 0);
          setPrompt(res.prompt);
          setPromptColor(res.result.success ? "green" : "red");
          setRecent((old) =>
            [
              {
                id: res.result.sheetId,
                ok: res.result.success,
                label: res.result.studentLabel || res.result.sheetUid || "Без QR",
                sub: res.result.success
                  ? `качество ${(res.result.quality * 100).toFixed(0)}%`
                  : reasonRu(res.result.reason) + (res.result.warnings.length ? ` · ${res.result.warnings[0]}` : ""),
                thumbnail: res.result.thumbnail,
                at: Date.now(),
              },
              ...old,
            ].slice(0, 30),
          );
          break;
        }
        case "busy":
          // Frame dropped while persisting — release the send lock.
          awaitingRef.current = false;
          break;
        case "error":
          awaitingRef.current = false;
          setWsError(msg.message);
          break;
        default:
          break;
      }
    };
  }, [sessionId, drawOverlay]);

  useEffect(() => {
    connect();
    return () => {
      wsRef.current?.close();
      if (timerRef.current) window.clearInterval(timerRef.current);
    };
  }, [connect]);

  // Frame pump.
  useEffect(() => {
    timerRef.current = window.setInterval(sendFrame, FRAME_INTERVAL_MS);
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current);
    };
  }, [sendFrame]);

  async function startScanning() {
    try {
      if (session.data?.status === "draft" || session.data?.status === "paused" || session.data?.status === "completed") {
        await api.post(`/sessions/${sessionId}/start`);
        session.refresh();
      }
    } catch {
      /* session may already be scanning */
    }
    wsRef.current?.send(JSON.stringify({ type: "resume" }));
    scanningRef.current = true;
    setScanning(true);
  }

  function pauseScanning() {
    wsRef.current?.send(JSON.stringify({ type: "pause" }));
    scanningRef.current = false;
    setScanning(false);
    setPrompt("ПАУЗА");
    setPromptColor("neutral");
  }

  async function completeSession() {
    pauseScanning();
    try {
      await api.post(`/sessions/${sessionId}/complete`);
      session.refresh();
    } catch (e) {
      setWsError((e as Error).message);
    }
  }

  const total = counters.accepted ?? 0;
  const expected = session.data?.expected_sheet_count ?? 0;

  return (
    <>
      <h1 className="page-title">
        Сканирование: {session.data?.title ?? `сессия ${sessionId}`}
        {session.data && <Badge map={SESSION_STATUS_RU} value={session.data.status} />}
        <span className="spacer" />
        <Link className="btn small" to={`/sessions/${sessionId}/review`}>
          К проверке →
        </Link>
      </h1>

      {camera.error && <div className="error-box">{camera.error}</div>}
      {wsError && <div className="error-box">{wsError}</div>}
      {calibrated === false && (
        <div className="error-box">
          Камера не откалибрована — детекция будет менее надёжной. <Link to="/calibration">Пройти калибровку</Link>.
        </div>
      )}

      <div className="scan-layout">
        <div>
          <div className="video-stage">
            <video ref={videoRef} muted playsInline />
            <canvas className="overlay" ref={overlayRef} />
            <div className="scan-hints">
              {hints.map((h) => (
                <div key={h} className="hint">
                  {hintRu(h)}
                </div>
              ))}
            </div>
            <div className={`scan-prompt ${promptColor}`}>{prompt}</div>
          </div>

          <div className="row mt">
            {!scanning ? (
              <button className="btn primary" onClick={startScanning} disabled={!connected || !camera.stream}>
                ▶ Начать сканирование
              </button>
            ) : (
              <button className="btn" onClick={pauseScanning}>
                ⏸ Пауза
              </button>
            )}
            <button className="btn success" onClick={completeSession}>
              Завершить сессию
            </button>
            <select
              value={camera.deviceId ?? ""}
              onChange={(e) => camera.start(e.target.value)}
              style={{ maxWidth: 260 }}
              title="Выбор камеры"
            >
              {camera.devices.map((d) => (
                <option key={d.deviceId} value={d.deviceId}>
                  {d.label || `Камера ${d.deviceId.slice(0, 6)}`}
                </option>
              ))}
            </select>
            <span className="muted">
              {camera.resolution ? `${camera.resolution[0]}×${camera.resolution[1]}` : ""}
              {analysisMs ? ` · анализ ${analysisMs} мс` : ""}
              {connected ? "" : " · нет связи с сервером"}
            </span>
          </div>

          <div className="grid cols-4 mt">
            <div className="stat-card">
              <div className="value">
                {total}
                {expected ? ` / ${expected}` : ""}
              </div>
              <div className="label">Принято листов</div>
            </div>
            <div className="stat-card">
              <div className="value">{counters.duplicates ?? 0}</div>
              <div className="label">Дубликаты</div>
            </div>
            <div className="stat-card">
              <div className="value">{counters.unidentified ?? 0}</div>
              <div className="label">Без QR</div>
            </div>
            <div className="stat-card">
              <div className="value">{speed || "—"}</div>
              <div className="label">Листов/мин</div>
            </div>
          </div>
        </div>

        <div className="panel">
          <h3 style={{ marginTop: 0 }}>Последние листы</h3>
          <div className="recent-sheets">
            {recent.length === 0 && <span className="muted">Отсканированные листы появятся здесь.</span>}
            {recent.map((r) => (
              <div key={`${r.at}-${r.id}`} className={`recent-sheet${r.ok ? "" : " warn"}`}>
                {r.thumbnail ? <img src={r.thumbnail} alt="" /> : <div style={{ width: 46, height: 62 }} />}
                <div className="meta">
                  <div className="name">{r.label}</div>
                  <div className="sub">{r.sub}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}

function reasonRu(reason: string): string {
  const map: Record<string, string> = {
    duplicate: "дубликат",
    unidentified: "QR не прочитан",
    low_quality: "низкое качество",
    no_candidates: "нет пригодных кадров",
  };
  return map[reason] ?? reason ?? "предупреждение";
}

function hintRu(hint: string): string {
  const map: Record<string, string> = {
    remove_hand: "Уберите руку с листа",
    hold_still: "Не двигайте лист",
    glare: "Блики на листе — измените освещение",
    blur: "Изображение размыто",
    touches_border: "Лист выходит за границы кадра",
    perspective: "Слишком большой угол камеры",
  };
  return map[hint] ?? hint;
}
