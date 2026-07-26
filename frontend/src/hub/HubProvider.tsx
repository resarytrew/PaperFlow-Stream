import { createContext, type ReactNode, useContext, useEffect, useMemo, useState } from "react";
import {
  beginPairing,
  clearHubConnection,
  discoverHub,
  finishPairing,
  probeHub,
  type HubConnection,
  type PairingChallenge,
} from "./runtime";

type HubStatus = "discovering" | "ready" | "unavailable" | "pairing-required" | "pairing";

interface HubContextValue {
  status: HubStatus;
  connection: HubConnection | null;
  error: string | null;
  challenge: PairingChallenge | null;
  reconnect: (url?: string) => Promise<void>;
  startPairing: () => Promise<void>;
  confirmPairing: (code: string) => Promise<void>;
  disconnect: () => void;
}

const HubContext = createContext<HubContextValue | null>(null);

export function HubProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<HubStatus>("discovering");
  const [connection, setConnection] = useState<HubConnection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [challenge, setChallenge] = useState<PairingChallenge | null>(null);

  async function reconnect(url?: string) {
    setStatus("discovering");
    setError(null);
    setChallenge(null);
    try {
      const next = url ? await probeHub(url) : await discoverHub();
      setConnection(next);
      setStatus(next.info.authorization.authorized ? "ready" : "pairing-required");
    } catch (e) {
      setConnection(null);
      setError((e as Error).message);
      setStatus("unavailable");
    }
  }

  async function startPairing() {
    if (!connection) return;
    setError(null);
    try {
      const nextChallenge = await beginPairing(connection);
      setChallenge(nextChallenge);
      setStatus("pairing");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function confirmPairing(code: string) {
    if (!connection || !challenge) return;
    setError(null);
    try {
      const next = await finishPairing(connection, challenge, code);
      setConnection(next);
      setChallenge(null);
      setStatus("ready");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function disconnect() {
    clearHubConnection();
    setConnection(null);
    setChallenge(null);
    setStatus("unavailable");
  }

  useEffect(() => {
    void reconnect();
  }, []);

  const value = useMemo<HubContextValue>(
    () => ({
      status,
      connection,
      error,
      challenge,
      reconnect,
      startPairing,
      confirmPairing,
      disconnect,
    }),
    [status, connection, error, challenge],
  );

  return <HubContext.Provider value={value}>{children}</HubContext.Provider>;
}

export function useHub(): HubContextValue {
  const value = useContext(HubContext);
  if (!value) throw new Error("useHub must be used inside HubProvider");
  return value;
}

export function HubConnectionGate({ children }: { children: ReactNode }) {
  const hub = useHub();
  const [url, setUrl] = useState("");
  const [code, setCode] = useState("");

  if (hub.status === "ready") return <>{children}</>;

  const cardStyle = {
    width: "min(560px, calc(100% - 40px))",
    padding: 32,
    borderRadius: 20,
    background: "white",
    boxShadow: "0 24px 70px rgba(18, 32, 46, 0.14)",
  } as const;

  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", background: "#f3f6f8", color: "#15202b" }}>
      <div style={cardStyle}>
        <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", color: "#687783" }}>
          PaperFlow Web
        </div>
        <h1 style={{ margin: "10px 0 8px" }}>Локальный PaperFlow Hub</h1>

        {hub.status === "discovering" && <p>Ищу защищённый Hub на этом компьютере или в школьной сети…</p>}

        {hub.status === "unavailable" && (
          <>
            <p style={{ lineHeight: 1.55 }}>
              Hub не найден. Убедись, что локальный модуль запущен. Ученические работы не будут отправлены на облачный сервер.
            </p>
            <label style={{ display: "grid", gap: 8 }}>
              <span>Адрес локального Hub</span>
              <input
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                placeholder="https://127.0.0.1:17841"
                style={{ padding: 12, border: "1px solid #cbd4da", borderRadius: 10 }}
              />
            </label>
            <button
              onClick={() => void hub.reconnect(url || undefined)}
              style={{ marginTop: 16, padding: "12px 18px", border: 0, borderRadius: 10, cursor: "pointer" }}
            >
              Повторить подключение
            </button>
          </>
        )}

        {hub.status === "pairing-required" && (
          <>
            <p style={{ lineHeight: 1.55 }}>
              Hub найден, но этот браузер ещё не сопряжён с ним. Подключение будет привязано к адресу интерфейса и локальному рабочему пространству.
            </p>
            <button
              onClick={() => void hub.startPairing()}
              style={{ padding: "12px 18px", border: 0, borderRadius: 10, cursor: "pointer" }}
            >
              Начать безопасное подключение
            </button>
          </>
        )}

        {hub.status === "pairing" && hub.challenge && (
          <>
            <p style={{ lineHeight: 1.55 }}>
              Открой локальную страницу подтверждения, затем введи показанный шестизначный код.
            </p>
            <a
              href={hub.challenge.displayUrl}
              target="_blank"
              rel="noreferrer"
              style={{ display: "inline-block", marginBottom: 16 }}
            >
              Открыть код на этом компьютере
            </a>
            {hub.challenge.devCode && (
              <div style={{ marginBottom: 12, fontFamily: "monospace" }}>DEV-код: {hub.challenge.devCode}</div>
            )}
            <div style={{ display: "flex", gap: 10 }}>
              <input
                value={code}
                onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="000000"
                style={{ flex: 1, padding: 12, border: "1px solid #cbd4da", borderRadius: 10, fontSize: 22, letterSpacing: ".16em" }}
              />
              <button
                disabled={code.length !== 6}
                onClick={() => void hub.confirmPairing(code)}
                style={{ padding: "12px 18px", border: 0, borderRadius: 10, cursor: "pointer" }}
              >
                Подключить
              </button>
            </div>
          </>
        )}

        {hub.error && <div style={{ marginTop: 16, padding: 12, borderRadius: 10, background: "#fff0f0", color: "#9b2525" }}>{hub.error}</div>}

        <p style={{ marginTop: 24, fontSize: 13, color: "#687783", lineHeight: 1.5 }}>
          ФИО учеников, изображения листов, ответы, OCR и оценки остаются внутри локального Hub.
        </p>
      </div>
    </div>
  );
}
