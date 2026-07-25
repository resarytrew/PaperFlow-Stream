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
  version: string;
  dataDir: string;
  qrBackends: Record<string, boolean>;
  ocr: { queue: Record<string, number>; local: Record<string, unknown> };
}

/** Editable subset of the runtime config: section -> field -> label + step. */
const EDITABLE: { section: string; title: string; fields: { key: string; label: string; step?: number }[] }[] = [
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
    fields: [{ key: "file_retention_days", label: "Хранить изображения, дней" }],
  },
];

export default function SettingsPage() {
  const settings = useApi<SettingsResponse>(() => api.get("/settings"), []);
  const ocr = useApi<OcrStatus>(() => api.get("/ocr/status"), []);
  const health = useApi<Health>(() => api.get("/health"), []);
  const dashboard = useApi<{ storage_bytes: number }>(() => api.get("/dashboard"), []);

  const [draft, setDraft] = useState<Record<string, Record<string, unknown>>>({});
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (settings.data) setDraft(JSON.parse(JSON.stringify(settings.data.config)));
  }, [settings.data]);

  function setValue(section: string, key: string, value: number) {
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
              <label className="field" key={f.key}>
                <span>{f.label}</span>
                <input
                  type="number"
                  step={f.step ?? 1}
                  value={(draft[group.section]?.[f.key] as number) ?? ""}
                  onChange={(e) => setValue(group.section, f.key, Number(e.target.value))}
                />
              </label>
            ))}
          </div>
        ))}
      </div>

      <h3 className="section">Состояние системы</h3>
      <div className="grid cols-2">
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>Сервер</h3>
          {settings.data && (
            <dl className="kv">
              <dt>Версия</dt>
              <dd>{settings.data.version}</dd>
              <dt>Каталог данных</dt>
              <dd style={{ wordBreak: "break-all" }}>{settings.data.paths.dataDir}</dd>
              <dt>База данных</dt>
              <dd style={{ wordBreak: "break-all" }}>{settings.data.paths.database}</dd>
              <dt>Занято на диске</dt>
              <dd>{dashboard.data ? fmtBytes(dashboard.data.storage_bytes) : "…"}</dd>
            </dl>
          )}
          <button className="btn mt" onClick={retention}>
            Очистить старые изображения сейчас
          </button>
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
            Слабые результаты автоматически попадают во вкладку «Нужна проверка». Улучшенную модель можно подключить
            через переменные окружения без изменения кода.
          </p>
        </div>
      </div>
    </>
  );
}
