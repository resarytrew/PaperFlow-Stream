import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, sheetImageUrl, wsUrl } from "../api/client";
import type { ScanResultMessage, ScanSession, ScanStateMessage } from "../api/types";
import { captureFrameBlob, useCamera } from "../hooks/useCamera";
import { playDuplicate, playReconnected, playSuccess, playWarning, speakAccepted, unlockAudio } from "../hooks/sounds";
import { Badge, SESSION_STATUS_RU, useApi } from "../lib";

const FRAME_INTERVAL_MS = 120; // ~8 fps to the analyser; capped further by "busy" flow control
const FRAME_MAX_WIDTH = 1280;
const FRAME_QUALITY = 0.8;
const RECONNECT_BASE_MS = 800;
const RECONNECT_MAX_MS = 8000;

interface RecentSheet {
  id: number | null;
  ok: boolean;
  label: string;
  sub: string;
  thumbnail: string | null;
  at: number;
  status: string;
  quality: number;
  reason: string;
}

interface Roster {
  classLinked: boolean;
  students: { studentId: number; externalId: string; name: string; sheets: number; ok: number; problem: number; status: string }[];
  submitted: number;
  missing: number;
  totalStudents: number;
}

export default function ScanPage() {
  const { id } = useParams();
  const sessionId = Number(id);
  const navigate = useNavigate();
  const session = useApi<ScanSession>(() => api.get(`/sessions/${sessionId}`), [sessionId]);
  const roster = useApi<Roster>(() => api.get(`/sessions/${sessionId}/roster`), [sessionId]);
  const [sideTab, setSideTab] = useState<"recent" | "roster">("recent");
  const rosterRefreshRef = useRef<(() => void) | null>(null);
  rosterRefreshRef.current = roster.refresh;

  const camera = useCamera();
  const videoRef = useRef<HTMLVideoElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<number | null>(null);
  const awaitingRef = useRef(false);
  const reconnectRef = useRef<{ attempt: number; timer: number | null; closed: boolean }>({
    attempt: 0,
    timer: null,
    closed: false,
  });
  const soundRef = useRef(true);
  const voiceRef = useRef(false);
  const previewDuplicateRef = useRef<string | null>(null);

  const [connected, setConnected] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [soundOn, setSoundOn] = useState(true);
  const [voiceOn, setVoiceOn] = useState(false);
  const [diagOn, setDiagOn] = useState(false);
  const [diagBusy, setDiagBusy] = useState(false);
  const [debugOpen, setDebugOpen] = useState(false);
  const [undoBusy, setUndoBusy] = useState(false);
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
  const [lastState, setLastState] = useState<ScanStateMessage | null>(null);
  const [flashKey, setFlashKey] = useState(0);
  const [counterBurst, setCounterBurst] = useState(false);

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
    const phase = stagePhaseFor(msg.color, msg.state, undefined, msg.prompt);
    const stroke =
      phase === "align" || phase === "capture"
        ? colors.amber
        : phase === "search"
          ? colors.neutral
          : colors[msg.color] ?? colors.neutral;

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

    // Binary WebSocket frame: avoids base64 JSON overhead for every camera frame.
    awaitingRef.current = true;
    void captureFrameBlob(video, FRAME_MAX_WIDTH, FRAME_QUALITY)
      .then((blob) => {
        const current = wsRef.current;
        if (!blob || !current || current.readyState !== WebSocket.OPEN || !scanningRef.current) {
          awaitingRef.current = false;
          return;
        }
        try {
          current.send(blob);
        } catch {
          awaitingRef.current = false;
        }
      })
      .catch(() => {
        awaitingRef.current = false;
      });
  }, []);

  const connect = useCallback(() => {
    if (wsRef.current) {
      // deliberate replacement — don't let the old socket's onclose reconnect
      wsRef.current.onclose = null;
      wsRef.current.close();
    }
    setWsError(null);
    const ws = new WebSocket(wsUrl(`/ws/sessions/${sessionId}/scan`));
    wsRef.current = ws;

    ws.onopen = () => {
      const wasRetry = reconnectRef.current.attempt > 0;
      reconnectRef.current.attempt = 0;
      setConnected(true);
      setReconnecting(false);
      setWsError(null);
      awaitingRef.current = false;
      if (wasRetry) {
        if (soundRef.current) playReconnected();
        // scanning continues where it stopped: the runtime lives on the server
        if (scanningRef.current) ws.send(JSON.stringify({ type: "resume" }));
      }
    };
    ws.onclose = () => {
      setConnected(false);
      if (reconnectRef.current.closed) return;
      // exponential backoff auto-reconnect — a flaky cable must not end the session
      const attempt = ++reconnectRef.current.attempt;
      const delay = Math.min(RECONNECT_BASE_MS * 2 ** (attempt - 1), RECONNECT_MAX_MS);
      setReconnecting(true);
      reconnectRef.current.timer = window.setTimeout(connect, delay);
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
          const preview = state.overlay?.qrPreview;
          if (preview?.duplicate && preview.sheetUid !== previewDuplicateRef.current) {
            previewDuplicateRef.current = preview.sheetUid;
            if (soundRef.current) playDuplicate();
          } else if (!preview?.duplicate) {
            previewDuplicateRef.current = null;
          }
          setLastState(state);
          setPrompt(preview?.duplicate ? "ДУБЛИКАТ" : primaryPrompt(state));
          setPromptColor(
            preview?.duplicate
              ? "red"
              : state.state === "SELECTING_BEST_FRAME" || state.state === "PROCESSING_FRAME"
                ? "amber"
                : state.color,
          );
          setHints(state.hints ?? []);
          setCounters(state.counters ?? {});
          setSpeed(state.speed ?? 0);
          setAnalysisMs(state.overlay?.analysisMs ?? 0);
          drawOverlay(state);
          break;
        }
        case "scan_result": {
          const res = msg as ScanResultMessage;
          const duplicate = res.result.reason === "duplicate" || res.result.scanStatus === "duplicate";
          if (soundRef.current) {
            if (res.result.success) playSuccess();
            else if (duplicate) playDuplicate();
            else playWarning();
          }
          if (res.result.success && voiceRef.current) {
            speakAccepted(res.result.studentLabel || res.result.sheetUid);
          }
          setFlashKey((k) => k + 1);
          setCounterBurst(true);
          window.setTimeout(() => setCounterBurst(false), 850);
          setCounters(res.counters ?? {});
          setSpeed(res.speed ?? 0);
          setPrompt(duplicate ? "ДУБЛИКАТ" : res.prompt);
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
                status: res.result.scanStatus,
                quality: res.result.quality,
                reason: res.result.reason,
              },
              ...old,
            ].slice(0, 30),
          );
          rosterRefreshRef.current?.();
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
    reconnectRef.current.closed = false;
    connect();
    return () => {
      reconnectRef.current.closed = true;
      if (reconnectRef.current.timer) window.clearTimeout(reconnectRef.current.timer);
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
      }
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
    unlockAudio(); // browsers allow sound only after a user gesture
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

  async function undoLastScan() {
    if (undoBusy) return;
    setUndoBusy(true);
    try {
      const result = await api.post<{ sheetId: number; previousStatus: string }>(`/sessions/${sessionId}/undo-last`);
      setRecent((old) => old.filter((item) => item.id !== result.sheetId));
      setCounters((old) => ({
        ...old,
        scanned: Math.max(0, Number(old.scanned ?? 0) - 1),
        duplicates:
          result.previousStatus === "duplicate" ? Math.max(0, Number(old.duplicates ?? 0) - 1) : Number(old.duplicates ?? 0),
        unidentified:
          result.previousStatus === "unidentified" ? Math.max(0, Number(old.unidentified ?? 0) - 1) : Number(old.unidentified ?? 0),
      }));
      setPrompt("ПОСЛЕДНИЙ СКАН ОТМЕНЁН");
      setPromptColor("amber");
      rosterRefreshRef.current?.();
    } catch (e) {
      setWsError((e as Error).message);
      if (soundRef.current) playWarning();
    } finally {
      setUndoBusy(false);
    }
  }

  async function completeSession() {
    pauseScanning();
    try {
      await api.post(`/sessions/${sessionId}/complete`);
      navigate(`/sessions/${sessionId}/summary`);
    } catch (e) {
      setWsError((e as Error).message);
    }
  }

  const total = Number(counters.scanned ?? counters.accepted ?? 0);
  const expected = session.data?.expected_sheet_count ?? 0;
  const last = recent[0] ?? null;
  const qrPreview = lastState?.overlay?.qrPreview ?? null;
  const problemCount = Number(counters.duplicates ?? 0) + Number(counters.unidentified ?? 0) + Number(counters.errors ?? 0);
  const stagePhase = qrPreview?.duplicate ? "error" : stagePhaseFor(promptColor, lastState?.state, last?.reason, prompt);
  const readiness = readinessItems(lastState);

  // Space bar toggles start/pause; Backspace/Ctrl+Z undo the last accidental scan.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;

      if (event.code === "Space") {
        event.preventDefault();
        if (scanningRef.current) pauseScanning();
        else void startScanning();
      }
      if (event.code === "Backspace" || (event.ctrlKey && event.code === "KeyZ")) {
        event.preventDefault();
        void undoLastScan();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.data?.status, undoBusy]);

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
      {wsError && !reconnecting && <div className="error-box">{wsError}</div>}
      {reconnecting && <div className="error-box">Связь с сервером потеряна — переподключение… Сканирование продолжится автоматически.</div>}
      {calibrated === false && (
        <div className="error-box">
          Камера не откалибрована — детекция будет менее надёжной. <Link to="/calibration">Пройти калибровку</Link>.
        </div>
      )}

      <div className="scan-layout">
        <div>
          <div className={`video-stage phase-${stagePhase}`}>
            <video ref={videoRef} muted playsInline />
            <canvas className="overlay" ref={overlayRef} />
            <div className="alignment-guide" aria-hidden="true">
              <span className="corner tl" />
              <span className="corner tr" />
              <span className="corner bl" />
              <span className="corner br" />
              <span className="guide-label qr">QR</span>
              <span className="guide-label answer">ОБЛАСТЬ ОТВЕТА</span>
            </div>
            {flashKey > 0 && <div key={flashKey} className={`capture-flash ${promptColor}`} />}
            <div className={`counter-hud${counterBurst ? " bump" : ""}`}>
              <div className="count">
                {total}
                {expected ? <span>/{expected}</span> : null}
              </div>
              <div className="caption">принято</div>
              {counterBurst && <div className="plus-one">+1</div>}
            </div>
            <div className="auto-trigger-hud">
              <span className={`dot ${stagePhase}`} />
              {autoTriggerText(stagePhase)}
            </div>
            <div className={`qr-preview-hud ${qrPreview?.duplicate ? "duplicate" : qrPreview?.success ? "ok" : ""}`}>
              <div className="caption">Сейчас под камерой</div>
              <div className="value">
                {qrPreview?.success
                  ? qrPreview.studentLabel || qrPreview.studentId
                  : lastState?.overlay?.quad
                    ? "ищу QR-код"
                    : "—"}
              </div>
              {qrPreview?.success && (
                <div className="sub">
                  {qrPreview.duplicate
                    ? "уже сканировали"
                    : `${qrPreview.classId || "класс"} · ${qrPreview.taskId || "задание"}${qrPreview.variantNo ? ` · вариант ${qrPreview.variantNo}` : ""}`}
                </div>
              )}
            </div>
            <div className="scan-readiness">
              {readiness.map((item) => (
                <div key={item.key} className={`readiness-item ${item.state}`}>
                  <span>{item.state === "ok" ? "✓" : item.state === "warn" ? "!" : "…"}</span>
                  {item.label}
                </div>
              ))}
            </div>
            {hints.length > 0 && <div className="primary-hint">{hintRu(hints[0])}</div>}
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
            <button className="btn" onClick={undoLastScan} disabled={undoBusy || !recent.length} title="Backspace или Ctrl+Z">
              ↶ Отменить последний
            </button>
            <button className="btn success" onClick={completeSession}>
              Завершить сессию
            </button>
            <button
              className="btn"
              title={soundOn ? "Выключить звук" : "Включить звук"}
              onClick={() => {
                unlockAudio();
                soundRef.current = !soundRef.current;
                setSoundOn(soundRef.current);
              }}
            >
              {soundOn ? "🔊" : "🔇"}
            </button>
            <button
              className={`btn${voiceOn ? " primary" : ""}`}
              title="Озвучивать фамилию после успешного скана"
              onClick={() => {
                unlockAudio();
                voiceRef.current = !voiceRef.current;
                setVoiceOn(voiceRef.current);
              }}
            >
              {voiceOn ? "🗣 Фамилии" : "🗣"}
            </button>
            <button className="btn" onClick={() => setDebugOpen((v) => !v)}>
              {debugOpen ? "Скрыть диагностику" : "Диагностика"}
            </button>
            {debugOpen && (
              <button
                className={`btn${diagOn ? " danger" : ""}`}
                title="Запись кадров для техподдержки: включите, повторите проблемную подачу листа, скачайте архив"
                onClick={() => {
                  const next = !diagOn;
                  setDiagOn(next);
                  wsRef.current?.send(JSON.stringify({ type: "diagnostics", enabled: next }));
                }}
              >
                {diagOn ? "⏺ Запись…" : "⏺ Запись клипа"}
              </button>
            )}
            {debugOpen && diagOn && (
              <button
                className="btn"
                disabled={diagBusy}
                title="Скачать ZIP с кадрами и журналом для отправки в поддержку"
                onClick={async () => {
                  setDiagBusy(true);
                  try {
                    await api.download(`/sessions/${sessionId}/diagnostics/download`, `diagnostics_${sessionId}.zip`);
                  } catch (e) {
                    setWsError((e as Error).message);
                  } finally {
                    setDiagBusy(false);
                  }
                }}
              >
                {diagBusy ? "Сборка…" : "⬇ Скачать клип"}
              </button>
            )}
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
              {debugOpen && analysisMs ? ` · анализ ${analysisMs} мс` : ""}
              {connected ? "" : " · нет связи с сервером"}
              {" · Пробел — старт/пауза · Backspace — отмена"}
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
              <div className="value">{problemCount}</div>
              <div className="label">Разобрать потом</div>
            </div>
            <div className="stat-card">
              <div className="value">{counters.duplicates ?? 0}</div>
              <div className="label">Дубликаты</div>
            </div>
            <div className="stat-card">
              <div className="value">{speed || "—"}</div>
              <div className="label">Листов/мин</div>
            </div>
          </div>
        </div>

        <div className="panel">
          <div className="tabs" style={{ marginBottom: 10 }}>
            <button className={`tab${sideTab === "recent" ? " active" : ""}`} onClick={() => setSideTab("recent")}>
              Последние листы
            </button>
            <button className={`tab${sideTab === "roster" ? " active" : ""}`} onClick={() => { setSideTab("roster"); roster.refresh(); }}>
              Кто не сдал{roster.data?.classLinked ? ` (${roster.data.missing})` : ""}
            </button>
          </div>

          {sideTab === "recent" && (
            <div className="recent-sheets">
              {recent.length === 0 && <span className="muted">Отсканированные листы появятся здесь.</span>}
              {recent.map((r) => (
                <div key={`${r.at}-${r.id}`} className={`recent-sheet scan-card ${r.ok ? "" : " warn"}`}>
                  {r.id ? (
                    <img
                      src={sheetImageUrl(r.id, "qr")}
                      alt="QR"
                      onError={(e) => {
                        if (r.thumbnail) e.currentTarget.src = r.thumbnail;
                      }}
                    />
                  ) : r.thumbnail ? (
                    <img src={r.thumbnail} alt="" />
                  ) : (
                    <div className="thumb-placeholder">QR</div>
                  )}
                  <div className="meta">
                    <div className="name">{r.ok ? "✓" : "⚠"} {r.label}</div>
                    <div className="sub">{r.sub}</div>
                    <div className="mini-badges">
                      <span className={`badge ${r.ok ? "green" : "red"}`}>{statusRu(r.status)}</span>
                      <span className="badge gray">{Math.round(r.quality * 100)}%</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {sideTab === "roster" && (
            <div className="recent-sheets">
              {!roster.data?.classLinked && <span className="muted">Сессия не привязана к классу.</span>}
              {roster.data?.classLinked && (
                <>
                  <div className="muted" style={{ fontSize: 13 }}>
                    Сдали {roster.data.submitted} из {roster.data.totalStudents}
                  </div>
                  {roster.data.students
                    .filter((s) => s.status !== "ok")
                    .map((s) => (
                      <div key={s.studentId} className="recent-sheet warn" style={{ borderLeftColor: s.status === "missing" ? "var(--amber)" : "var(--red)" }}>
                        <div className="meta">
                          <div className="name">{s.name}</div>
                          <div className="sub">
                            {s.status === "missing" ? "лист не отсканирован" : `есть проблемные листы: ${s.problem}`}
                          </div>
                        </div>
                      </div>
                    ))}
                  {roster.data.students.every((s) => s.status === "ok") && (
                    <div className="ok-box" style={{ marginBottom: 0 }}>Все работы собраны ✓</div>
                  )}
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </>
  );
}

type StagePhase = "search" | "align" | "capture" | "success" | "error";

type ReadinessState = "wait" | "ok" | "warn";

function primaryPrompt(state: ScanStateMessage): string {
  const blockers = state.blockingReasons ?? [];
  if (blockers.includes("hand")) return "УБЕРИТЕ РУКУ";
  if (blockers.includes("motion")) return "НЕ ДВИГАЙТЕ";
  if (blockers.includes("sharpness")) return "ПОДОЖДИТЕ ФОКУС";
  if (blockers.includes("glare")) return "УБЕРИТЕ БЛИК";
  if (blockers.includes("corners") || blockers.includes("out_of_bounds")) return "ВЫРОВНЯЙТЕ ЛИСТ";
  if (state.state === "SELECTING_BEST_FRAME" || state.state === "PROCESSING_FRAME") return "СНИМАЮ";
  return state.prompt;
}

function stagePhaseFor(color: string, state?: string, reason?: string, prompt?: string): StagePhase {
  if (reason === "duplicate" || color === "red" || state === "SCAN_WARNING") return "error";
  if (state === "SELECTING_BEST_FRAME" || state === "PROCESSING_FRAME") return "capture";
  if (state === "SCAN_SUCCESS" || prompt?.includes("ПРИНЯТ") || color === "green") return "success";
  if (state === "PAPER_DETECTED" || state === "WAITING_STABILITY") return "align";
  return "search";
}

function autoTriggerText(phase: StagePhase): string {
  if (phase === "success") return "Скан зафиксирован";
  if (phase === "error") return "Нужна повторная подача";
  if (phase === "capture") return "Автозахват: снимаю";
  if (phase === "align") return "Автозахват: ищу QR и метки";
  return "Автозахват: жду лист";
}

function readinessItems(state: ScanStateMessage | null): { key: string; label: string; state: ReadinessState }[] {
  const blockers = state?.blockingReasons ?? [];
  const metrics = state?.overlay?.metrics ?? {};
  const qr = Number(metrics.qr_readability ?? metrics.qr ?? 0);
  const motion = Number(metrics.motion ?? 1);
  const quality = Number(metrics.quality ?? 0);
  const hasQuad = Boolean(state?.overlay?.quad);

  return [
    {
      key: "corners",
      label: "4 угла",
      state: !hasQuad ? "wait" : blockers.some((b) => ["corners", "out_of_bounds"].includes(b)) ? "warn" : "ok",
    },
    {
      key: "qr",
      label: qr >= 0.75 ? "QR читается" : "ищу QR",
      state: !hasQuad ? "wait" : qr >= 0.75 ? "ok" : qr >= 0.35 ? "wait" : "warn",
    },
    {
      key: "motion",
      label: blockers.includes("motion") ? "лист движется" : "неподвижно",
      state: !hasQuad ? "wait" : blockers.includes("motion") || motion > 0.2 ? "warn" : "ok",
    },
    {
      key: "quality",
      label: blockers.includes("sharpness") ? "смазано" : "резкость",
      state: !hasQuad ? "wait" : blockers.includes("sharpness") || quality < 0.25 ? "warn" : "ok",
    },
  ];
}

function statusRu(status: string): string {
  const map: Record<string, string> = {
    ok: "принят",
    duplicate: "дубликат",
    unidentified: "без QR",
    low_quality: "качество",
    rescan_required: "перескан",
  };
  return map[status] ?? status;
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
    hand: "Уберите руку с листа",
    hold_still: "Не двигайте лист",
    motion: "Не двигайте лист",
    glare: "Блики на листе — измените освещение",
    blur: "Изображение размыто",
    sharpness: "Подождите фокусировки — изображение смазано",
    touches_border: "Лист выходит за границы кадра",
    out_of_bounds: "Лист выходит за рамку",
    corners: "Покажите все четыре угла листа",
    perspective: "Слишком большой угол камеры",
  };
  return map[hint] ?? hint;
}
