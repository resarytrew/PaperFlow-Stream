import { useEffect, useState } from "react";
import { api } from "../api/client";
import { fmtBytes, useApi } from "../lib";

interface SettingsResponse {
  config: Record<string, Record<string, unknown>>;
  paths: { dataDir: string; storageDir: string; database: string };
  version: string;
}

interface OcrStatus {
  queue: Record<string, number>;
  providers: Record<string, unknown>;
  active: string;
  thresholds: { high: number; low: number; criticalToken: number };
  concurrency: number;
}

interface Health {
  status: string;
  product: string;
  version: string;
  protocolVersion: number;
  deploymentMode: "personal" | "school";
  qrBackends: Record<string, boolean>;
  ocr: { queue: Record<string, number>; local: Record<string, unknown> };
}

interface HubClient {
  id: string;
  name: string;
  origin: string;
  workspace_id: string;
  role: string;
  last_seen_at: string;
  expires_at: string;
}

interface HubClientsResponse {
  clients: HubClient[];
}

type EditableField = {
  key: string;
  label: string;
  step?: number;
  type?: "number" | "checkbox" | "text" | "password";
};

/** Editable subset of the runtime config: section -> field -> label + step. */
const EDITABLE: { section: string; title: string; fields: EditableField[] }[] = [
  {
    section: "stability",
    title: "Стабильность и качество",
    fields: [
      { key: "motion_threshold", label: "Порог движения (0–1)", step: 0.01 },
      { key: "stable_frames_required", label: "Стабильных кадров до захвата" },
      { key: "min_quality_score", label: "Минимальное качество листа (0–1)", step: 0.01 },
      { key: "min_sharpness", label: "Минимальная резкость (0–1)", step: 0.01 },
      { key: "max_glare", label: "Максимум бликов (0–1)", step: 0.01 },
    ],
  },
  {
    section: "detection",
    title: "Детекция листа",
    fields: [
      { key: "min_area_ratio", label: "Мин. доля кадра под лист (0–1)", step: 0.01 },
      { key: "entering_diff_ratio", label: "Порог появления листа", step: 0.005 },
      { key: "empty_diff_ratio", label: "Порог пустого стола", step: 0.005 },
    ],
  },
  {
    section: "ocr",
    title: "Распознавание рукописного текста",
    fields: [
      { key: "high_confidence", label: "Порог «уверенно» (0–1)", step: 0.01 },
      { key: "low_confidence", label: "Порог «сомнительно» (0–1)", step: 0.01 },
      { key: "concurrency", label: "Параллельных задач OCR" },
    ],
  },
  {
    section: "privacy",
    title: "Приватность и хранение",
    fields: [
      { key: "file_retention_days", label: "Хранить изображения, дней" },
      { key: "allow_cloud_providers", label: "Разрешить облачные провайдеры", type: "checkbox" },
      { key: "vision_ocr_enabled", label: "Разрешить Yandex Vision OCR", type: "checkbox" },
      { key: "vision_send_full_sheet", label: "Отправлять весь лист (иначе только область ответа)", type: "checkbox" },
    ],
  },
  {
    section: "vision_ocr",
    title: "Yandex Vision OCR",
    fields: [
      { key: "endpoint", label: "Endpoint", type: "text" },
      { key: "api_key", label: "API key", type: "password" },
      { key: "folder_id", label: "Folder ID", type: "text" },
      { key: "model", label: "Модель", type: "text" },
      { key: "mock_mode", label: "Тестовый mock-режим без облака", type: "checkbox" },
    ],
  },
];

export default function SettingsPage() {
  const settings = useApi<SettingsResponse>(() => api.get("/settings"), []);
  const ocr = useApi<OcrStatus>(() => api.get("/ocr/status"), []);
  const health = useApi<Health>(() => api.get("/health"), []);
  const dashboard = useApi<{ storage_bytes: number }>(() => api.get("/dashboard"), []);
  const hubClients = useApi<HubClientsResponse>(() => api.get("/hub/clients"), []);

  const [draft, setDraft] = useState<Record<string, Record<string, unknown>>>({});
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (settings.data) setDraft(JSON.parse(JSON.stringify(settings.data.config)));
  }, [settings.data]);

  function setValue(section: string, key: string, value: unknown) {
    setDraft((d) => ({ ...d, [section]: { ...d[section], [key]: value } }));
  }

  async function save() {
    setError(null);
    setSaved(false);
    try {
      const patch: Record<string, Record<string, unknown>> = {};
      for (const group of EDITABLE) {
        patch[group.section] = {};
        for (const f of group.fields) {
          patch[group.section][f.key] = draft[group.section]?.[f.key];
        }
      }
      await api.patch("/settings", patch);
      setSaved(true);
      settings.refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function reset() {
    if (!confirm("Сбросить все настройки к значениям по умолчанию?")) return;
    try {
      await api.post("/settings/reset");
      settings.refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function retention() {
    try {
      const r = await api.post<{ retentionDays: number; filesRemoved: number }>("/maintenance/retention");
      alert(`Удалено файлов: ${r.filesRemoved} (срок хранения: ${r.retentionDays} дн.)`);
      dashboard.refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function downloadBackup() {
    try {
      setError(null);
      await api.download("/maintenance/backup", "paperflow_backup.zip");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function revokeClient(client: HubClient) {
    if (!confirm(`Отключить браузер «${client.name}» (${client.origin})?`)) return;
    try {
      await api.delete(`/hub/clients/${client.id}`);
      hubClients.refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <>
      <h1 className="page-title">
        Настройки
        <span className="spacer" />
        <button className="btn" onClick={reset}>
          Сбросить
        </button>
        <button className="btn primary" onClick={save}>
          Сохранить
        </button>
      </h1>

      {error && <div className="error-box">{error}</div>}
      {saved && <div className="ok-box">Настройки сохранены и применены к активным сессиям.</div>}

      <div className="grid cols-2">
        {EDITABLE.map((group) => (
          <div className="panel" key={group.section}>
            <h3 style={{ marginTop: 0 }}>{group.title}</h3>
            {group.fields.map((f) => (
              <label className={f.type === "checkbox" ? "row" : "field"} key={f.key} style={f.type === "checkbox" ? { marginBottom: 10, cursor: "pointer" } : undefined}>
                {f.type === "checkbox" ? (
                  <>
                    <input
                      type="checkbox"
                      checked={Boolean(draft[group.section]?.[f.key])}
                      onChange={(e) => setValue(group.section, f.key, e.target.checked)}
                    />
                    <span>{f.label}</span>
                  </>
                ) : (
                  <>
                    <span>{f.label}</span>
                    <input
                      type={f.type ?? "number"}
                      step={f.step ?? 1}
                      value={(draft[group.section]?.[f.key] as string | number | undefined) ?? ""}
                      onChange={(e) =>
                        setValue(
                          group.section,
                          f.key,
                          f.type === "text" || f.type === "password" ? e.target.value : Number(e.target.value),
                        )
                      }
                    />
                  </>
                )}
              </label>
            ))}
            {group.section === "vision_ocr" && (
              <p className="muted" style={{ fontSize: 13, lineHeight: 1.5 }}>
                По умолчанию облако выключено. Для безопасного теста включите mock-режим: изображения не отправляются наружу,
                но кнопка «Распознать Vision» в проверке работает как настоящая интеграция.
              </p>
            )}
          </div>
        ))}
      </div>

      <h3 className="section">Состояние системы</h3>
      <div className="grid cols-2">
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>Локальный Hub</h3>
          {settings.data && (
            <dl className="kv">
              <dt>Версия</dt>
              <dd>{settings.data.version}</dd>
              <dt>Режим</dt>
              <dd>{health.data?.deploymentMode === "school" ? "Школьный Hub" : "Персональный Hub"}</dd>
              <dt>Каталог данных</dt>
              <dd style={{ wordBreak: "break-all" }}>{settings.data.paths.dataDir}</dd>
              <dt>База данных</dt>
              <dd style={{ wordBreak: "break-all" }}>{settings.data.paths.database}</dd>
              <dt>Занято на диске</dt>
              <dd>{dashboard.data ? fmtBytes(dashboard.data.storage_bytes) : "…"}</dd>
            </dl>
          )}
          <div className="row mt">
            <button className="btn" onClick={() => void downloadBackup()}>
              Скачать резервную копию
            </button>
            <button className="btn" onClick={() => void retention()}>
              Очистить старые изображения
            </button>
          </div>
          <p className="muted mt" style={{ fontSize: 13 }}>
            Резервная копия создаётся локально. Ключи OCR и доверенные браузерные токены в неё не включаются.
          </p>
        </div>

        <div className="panel">
          <h3 style={{ marginTop: 0 }}>QR и OCR</h3>
          {health.data && (
            <dl className="kv">
              <dt>QR-бэкенды</dt>
              <dd>
                {Object.entries(health.data.qrBackends).map(([name, ok]) => (
                  <span key={name} className={`badge ${ok ? "green" : "gray"}`} style={{ marginRight: 6 }}>
                    {name}
                  </span>
                ))}
              </dd>
            </dl>
          )}
          {ocr.data && (
            <dl className="kv mt">
              <dt>Активный провайдер OCR</dt>
              <dd>{ocr.data.active}</dd>
              <dt>Очередь OCR</dt>
              <dd>
                в работе: {ocr.data.queue.active ?? 0}, ожидает: {ocr.data.queue.pending ?? ocr.data.queue.queued ?? 0}
              </dd>
              <dt>Пороги уверенности</dt>
              <dd>
                ≥{ocr.data.thresholds.high} уверенно · &lt;{ocr.data.thresholds.low} сомнительно
              </dd>
            </dl>
          )}
          <p className="muted mt" style={{ fontSize: 13 }}>
            Локальная модель OCR обучена в основном на печатном тексте — русский рукописный текст распознаётся слабо.
            Слабые результаты автоматически попадают во вкладку «Нужна проверка».
          </p>
        </div>
      </div>

      <h3 className="section">Подключённые браузеры</h3>
      <div className="panel">
        {hubClients.loading && <span className="muted">Загрузка подключений…</span>}
        {hubClients.data?.clients.length === 0 && (
          <p className="muted" style={{ margin: 0 }}>
            Сопряжённых внешних браузеров нет. Локальный интерфейс может работать без отдельного токена.
          </p>
        )}
        {(hubClients.data?.clients ?? []).map((client) => (
          <div className="row" key={client.id} style={{ padding: "10px 0", borderBottom: "1px solid var(--border)" }}>
            <div style={{ flex: 1 }}>
              <strong>{client.name}</strong>
              <div className="muted" style={{ fontSize: 13, wordBreak: "break-all" }}>
                {client.origin} · workspace: {client.workspace_id} · роль: {client.role}
              </div>
              <div className="muted" style={{ fontSize: 12 }}>
                Последняя активность: {new Date(client.last_seen_at).toLocaleString("ru-RU")}
              </div>
            </div>
            <button className="btn small danger" onClick={() => void revokeClient(client)}>
              Отключить
            </button>
          </div>
        ))}
        <p className="muted mt" style={{ fontSize: 13 }}>
          Токен каждого браузера привязан к точному Origin и рабочему пространству. Его можно отозвать в любой момент.
        </p>
      </div>
    </>
  );
}
