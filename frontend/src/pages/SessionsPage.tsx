import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { ClassGroup, ScanSession, Task } from "../api/types";
import { Badge, SESSION_STATUS_RU, fmtDate, useApi } from "../lib";

export default function SessionsPage() {
  const navigate = useNavigate();
  const sessions = useApi<ScanSession[]>(() => api.get("/sessions"), []);
  const classes = useApi<ClassGroup[]>(() => api.get("/classes"), []);
  const tasks = useApi<Task[]>(() => api.get("/tasks"), []);

  const [classId, setClassId] = useState<number | "">("");
  const [taskId, setTaskId] = useState<number | "">("");
  const [title, setTitle] = useState("");
  const [expected, setExpected] = useState(0);
  const [error, setError] = useState<string | null>(null);

  async function createSession(startScan: boolean) {
    try {
      const session = await api.post<ScanSession>("/sessions", {
        class_id: classId || null,
        task_id: taskId || null,
        title: title.trim(),
        expected_sheet_count: expected,
      });
      setTitle("");
      setExpected(0);
      sessions.refresh();
      if (startScan) navigate(`/sessions/${session.id}/scan`);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function exportAs(id: number, kind: "csv" | "json" | "xlsx" | "zip") {
    try {
      await api.download(`/sessions/${id}/export/${kind}`, `session_${id}.${kind}`);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <>
      <h1 className="page-title">Сессии сканирования</h1>
      {error && <div className="error-box">{error}</div>}

      <div className="panel mb">
        <h3 style={{ marginTop: 0 }}>Новая сессия</h3>
        <div className="row">
          <select value={classId} onChange={(e) => setClassId(e.target.value ? Number(e.target.value) : "")} style={{ maxWidth: 200 }}>
            <option value="">Класс (необязательно)</option>
            {(classes.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          <select value={taskId} onChange={(e) => setTaskId(e.target.value ? Number(e.target.value) : "")} style={{ maxWidth: 260 }}>
            <option value="">Задание (необязательно)</option>
            {(tasks.data ?? []).map((t) => (
              <option key={t.id} value={t.id}>
                {t.external_id} — {t.title}
              </option>
            ))}
          </select>
          <input type="text" placeholder="Название (автоматически, если пусто)" value={title} onChange={(e) => setTitle(e.target.value)} style={{ maxWidth: 280 }} />
          <input
            type="number"
            min={0}
            placeholder="Ожидается листов"
            value={expected || ""}
            onChange={(e) => setExpected(Number(e.target.value) || 0)}
            style={{ maxWidth: 150 }}
            title="Ожидаемое количество листов"
          />
          <button className="btn primary" onClick={() => createSession(true)}>
            Создать и сканировать
          </button>
          <button className="btn" onClick={() => createSession(false)}>
            Только создать
          </button>
        </div>
      </div>

      <div className="panel">
        <table className="data">
          <thead>
            <tr>
              <th>Сессия</th>
              <th>Статус</th>
              <th>Листов</th>
              <th>Проблем</th>
              <th>Создана</th>
              <th>Действия</th>
            </tr>
          </thead>
          <tbody>
            {(sessions.data ?? []).map((s) => {
              const problems = s.stats.duplicates + s.stats.unidentified + s.stats.low_quality + s.stats.rescan_required;
              return (
                <tr key={s.id}>
                  <td>
                    <strong>{s.title}</strong>
                    {s.class_name && <div className="muted" style={{ fontSize: 12 }}>{s.class_name}{s.task_title ? ` · ${s.task_title}` : ""}</div>}
                  </td>
                  <td>
                    <Badge map={SESSION_STATUS_RU} value={s.status} />
                  </td>
                  <td>
                    {s.stats.total}
                    {s.expected_sheet_count ? ` / ${s.expected_sheet_count}` : ""}
                  </td>
                  <td>{problems ? <span className="badge amber">{problems}</span> : <span className="muted">0</span>}</td>
                  <td className="muted">{fmtDate(s.created_at)}</td>
                  <td>
                    <div className="row" style={{ gap: 6 }}>
                      <Link className="btn small primary" to={`/sessions/${s.id}/scan`}>
                        Сканировать
                      </Link>
                      <Link className="btn small" to={`/sessions/${s.id}/review`}>
                        Проверка
                      </Link>
                      <Link className="btn small" to={`/sessions/${s.id}/summary`}>
                        Итоги
                      </Link>
                      <button className="btn small" onClick={() => exportAs(s.id, "xlsx")} title="Экспорт XLSX">
                        XLSX
                      </button>
                      <button className="btn small" onClick={() => exportAs(s.id, "zip")} title="ZIP-архив изображений">
                        ZIP
                      </button>
                      <button
                        className="btn small danger"
                        onClick={async () => {
                          if (!confirm(`Удалить сессию «${s.title}» вместе с листами?`)) return;
                          try {
                            await api.delete(`/sessions/${s.id}`);
                            sessions.refresh();
                          } catch (e) {
                            setError((e as Error).message);
                          }
                        }}
                      >
                        ×
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
            {sessions.data?.length === 0 && (
              <tr>
                <td colSpan={6} className="muted">
                  Сессий пока нет.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
