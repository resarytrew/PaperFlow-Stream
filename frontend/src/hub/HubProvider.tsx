import {
  createContext,
  type ChangeEvent,
  type ReactNode,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
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

const DEFAULT_HUB_URL = "http://127.0.0.1:17841";
const REQUIRED_HUB_VERSION = "0.3.2";
const DEFAULT_DOWNLOAD_URL =
  "https://github.com/resarytrew/PaperFlow-Stream/releases/download/v0.3.2-pilot/ChistovikSetup-0.3.2.exe";
const DEFAULT_RELEASES_URL =
  "https://github.com/resarytrew/PaperFlow-Stream/releases/tag/v0.3.2-pilot";

function friendlyConnectionError(error: string | null): string | null {
  if (!error) return null;
  if (error.includes("Failed to fetch") || error.includes("NetworkError")) {
    return "Локальный Hub пока не отвечает. Обычно это означает, что он ещё не установлен, не запущен или браузер ожидает разрешение на доступ к локальной сети.";
  }
  if (error.includes("403")) {
    return "Локальный модуль найден, но этот адрес «Чистовика» ещё не подтверждён. Обнови Hub до актуальной версии и начни безопасное подключение заново.";
  }
  return error;
}

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
  const [url, setUrl] = useState(DEFAULT_HUB_URL);
  const [code, setCode] = useState("");
  const [waitingForInstall, setWaitingForInstall] = useState(false);

  const downloadUrl = import.meta.env.VITE_PAPERFLOW_HUB_DOWNLOAD_URL || DEFAULT_DOWNLOAD_URL;
  const releasesUrl = import.meta.env.VITE_PAPERFLOW_HUB_RELEASES_URL || DEFAULT_RELEASES_URL;
  const isWindows = typeof navigator !== "undefined" && /Windows/i.test(navigator.userAgent);
  const readableError = friendlyConnectionError(hub.error);

  useEffect(() => {
    if (!waitingForInstall || hub.status !== "unavailable") return;
    const timer = window.setTimeout(() => void hub.reconnect(), 3000);
    return () => window.clearTimeout(timer);
  }, [waitingForInstall, hub.status]);

  if (hub.status === "ready") return <>{children}</>;

  const cardStyle = {
    width: "min(680px, calc(100% - 32px))",
    padding: 32,
    borderRadius: 24,
    background: "white",
    boxShadow: "0 24px 70px rgba(18, 32, 46, 0.14)",
  } as const;
  const primaryButton = {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    minHeight: 46,
    padding: "0 20px",
    border: 0,
    borderRadius: 12,
    background: "#15202b",
    color: "white",
    fontWeight: 700,
    textDecoration: "none",
    cursor: "pointer",
  } as const;
  const secondaryButton = {
    ...primaryButton,
    background: "#edf2f4",
    color: "#15202b",
  } as const;

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        padding: "24px 0",
        background: "#f3f6f8",
        color: "#15202b",
      }}
    >
      <div style={cardStyle}>
        <div
          style={{
            fontSize: 13,
            fontWeight: 700,
            letterSpacing: ".08em",
            textTransform: "uppercase",
            color: "#687783",
          }}
        >
          Чистовик
        </div>
        <h1 style={{ margin: "10px 0 8px" }}>Локальный модуль «Чистовик»</h1>

        {hub.status === "discovering" && (
          <div style={{ padding: "22px 0" }}>
            <p style={{ margin: 0, lineHeight: 1.55 }}>
              Ищу защищённый Hub на этом компьютере или в школьной сети…
            </p>
            <div
              aria-hidden="true"
              style={{
                width: "100%",
                height: 4,
                marginTop: 18,
                overflow: "hidden",
                borderRadius: 999,
                background: "#e3eaed",
              }}
            >
              <div style={{ width: "42%", height: "100%", borderRadius: 999, background: "#31a88b" }} />
            </div>
          </div>
        )}

        {hub.status === "unavailable" && (
          <>
            <div
              style={{
                marginTop: 18,
                padding: 20,
                border: "1px solid #dce5e8",
                borderRadius: 16,
                background: "#f8fafb",
              }}
            >
              <div style={{ fontSize: 18, fontWeight: 750 }}>Для работы нужен Чистовик</div>
              <p style={{ margin: "8px 0 18px", color: "#53636e", lineHeight: 1.55 }}>
                Веб-интерфейс уже открыт на Vercel. Камера, OCR, база и ученические работы обрабатываются локальным модулем на этом компьютере.
              </p>

              <ol style={{ margin: "0 0 20px", paddingLeft: 22, lineHeight: 1.7 }}>
                <li>Скачай и установи Чистовик {REQUIRED_HUB_VERSION}.</li>
                <li>Запусти его и оставь работать в системном трее.</li>
                <li>Вернись сюда — подключение проверится автоматически.</li>
              </ol>

              {isWindows ? (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
                  <a
                    href={downloadUrl}
                    onClick={() => setWaitingForInstall(true)}
                    style={primaryButton}
                  >
                    Скачать Чистовик {REQUIRED_HUB_VERSION} для Windows
                  </a>
                  <button
                    type="button"
                    onClick={() => {
                      setWaitingForInstall(true);
                      void hub.reconnect();
                    }}
                    style={secondaryButton}
                  >
                    Чистовик уже установлен — проверить
                  </button>
                </div>
              ) : (
                <div style={{ padding: 14, borderRadius: 12, background: "#fff6df", lineHeight: 1.5 }}>
                  Готовый установщик пилотной версии сейчас предназначен для Windows 10/11. Для другой ОС Hub пока запускается из исходного кода.
                </div>
              )}

              <div style={{ marginTop: 14, fontSize: 13, color: "#687783", lineHeight: 1.5 }}>
                Загружаемый файл должен называться <strong>ChistovikSetup-0.3.1.exe</strong>. Если в папке загрузок уже есть старый ChistovikSetup.exe, удали его перед запуском. Открыть{" "}
                <a href={releasesUrl} target="_blank" rel="noreferrer">
                  релиз 0.3.1
                </a>
                .
              </div>
            </div>

            {waitingForInstall && (
              <div style={{ marginTop: 16, padding: 14, borderRadius: 12, background: "#edf8f5", color: "#1f6655" }}>
                Ожидаю запуск «Чистовика». После установки он будет проверяться каждые несколько секунд.
              </div>
            )}

            {readableError && (
              <div style={{ marginTop: 16, padding: 14, borderRadius: 12, background: "#fff4e5", color: "#76501d", lineHeight: 1.5 }}>
                {readableError}
              </div>
            )}

            <details style={{ marginTop: 18 }}>
              <summary style={{ cursor: "pointer", fontWeight: 650 }}>Диагностика и ручной адрес Hub</summary>
              <div style={{ marginTop: 14, display: "grid", gap: 12 }}>
                <label style={{ display: "grid", gap: 8 }}>
                  <span>Адрес локального модуля</span>
                  <input
                    value={url}
                    onChange={(event: ChangeEvent<HTMLInputElement>) => setUrl(event.target.value)}
                    placeholder={DEFAULT_HUB_URL}
                    style={{ padding: 12, border: "1px solid #cbd4da", borderRadius: 10 }}
                  />
                </label>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
                  <button type="button" onClick={() => void hub.reconnect(url || undefined)} style={secondaryButton}>
                    Проверить этот адрес
                  </button>
                  <a
                    href={`${DEFAULT_HUB_URL}/api/health`}
                    target="_blank"
                    rel="noreferrer"
                    style={{ ...secondaryButton, fontSize: 14 }}
                  >
                    Открыть локальную диагностику
                  </a>
                </div>
                {hub.error && (
                  <code style={{ padding: 12, borderRadius: 10, background: "#eef2f4", overflowWrap: "anywhere" }}>
                    {hub.error}
                  </code>
                )}
              </div>
            </details>
          </>
        )}

        {hub.status === "pairing-required" && (
          <>
            <div style={{ marginTop: 18, padding: 18, borderRadius: 14, background: "#edf8f5" }}>
              <strong>Чистовик найден.</strong>
              <p style={{ margin: "8px 0 0", lineHeight: 1.55 }}>
                Осталось подтвердить этот адрес Чистовик локальным кодом. Без подтверждения сайт не получает доступ к работам и настройкам.
              </p>
            </div>
            <button onClick={() => void hub.startPairing()} style={{ ...primaryButton, marginTop: 16 }}>
              Начать безопасное подключение
            </button>
          </>
        )}

        {hub.status === "pairing" && hub.challenge && (
          <>
            <p style={{ lineHeight: 1.55 }}>
              Открой локальную страницу подтверждения. Проверь, что на ней указан текущий адрес Чистовик, затем введи шестизначный код.
            </p>
            <a
              href={hub.challenge.displayUrl}
              target="_blank"
              rel="noreferrer"
              style={{ ...secondaryButton, marginBottom: 16 }}
            >
              Открыть код на этом компьютере
            </a>
            {hub.challenge.devCode && (
              <div style={{ marginBottom: 12, fontFamily: "monospace" }}>DEV-код: {hub.challenge.devCode}</div>
            )}
            <div style={{ display: "flex", gap: 10 }}>
              <input
                value={code}
                onChange={(event: ChangeEvent<HTMLInputElement>) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="000000"
                style={{
                  flex: 1,
                  minWidth: 0,
                  padding: 12,
                  border: "1px solid #cbd4da",
                  borderRadius: 10,
                  fontSize: 22,
                  letterSpacing: ".16em",
                }}
              />
              <button
                disabled={code.length !== 6}
                onClick={() => void hub.confirmPairing(code)}
                style={{ ...primaryButton, opacity: code.length === 6 ? 1 : 0.5 }}
              >
                Подключить
              </button>
            </div>
          </>
        )}

        {hub.status !== "unavailable" && hub.error && (
          <div style={{ marginTop: 16, padding: 12, borderRadius: 10, background: "#fff0f0", color: "#9b2525" }}>
            {friendlyConnectionError(hub.error)}
          </div>
        )}

        <p style={{ marginTop: 24, fontSize: 13, color: "#687783", lineHeight: 1.5 }}>
          ФИО учеников, изображения листов, ответы, OCR и оценки остаются внутри локального Hub.
        </p>
      </div>
    </div>
  );
}
