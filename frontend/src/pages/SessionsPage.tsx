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
  const [showCreate, setShowCreate] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function createSession(startScan: boolean) {
    try {
      setError(null);
      const session = await api.post<ScanSession>("/sessions", {
        class_id: classId || null,
        task_id: taskId || null,
        title: title.trim(),
        expected_sheet_count: expected,
      });
      setTitle("");
      setExpected(0);
      setClassId("");
      setTaskId("");
      setShowCreate(false);
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

  async function removeSession(session: ScanSession) {
    if (!confirm(`Удалить сессию «${session.title}» вместе с листами?`)) return;
    try {
      setError(null);
      await api.delete(`/sessions/${session.id}`);
      sessions.refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const items = sessions.data ?? [];
  const activeCount = items.filter((session) => session.status === "scanning" || session.status === "paused").length;
  const attentionCount = items.reduce(
    (sum, session) =>
      sum + session.stats.duplicates + session.stats.unidentified + session.stats.low_quality + session.stats.rescan_required,
    0,
  );

  return (
    <>
      <section className="page-heading sessions-heading">
        <div className="page-heading-copy">
          <div className="eyebrow">Архив работы</div>
          <h1>Сессии</h1>
          <p>Каждая сессия — отдельный поток листов: от первого скана до итоговой ведомости.</p>
        </div>
        <div className="page-heading-actions">
          <button className="btn primary" onClick={() => setShowCreate((value) => !value)}>
            {showCreate ? "Закрыть создание" : "Новая сессия"}
          </button>
        </div>
      </section>

      {error && <div className="error-box">{error}</div>}

      <section className="session-overview" aria-label="Сводка по сессиям">
        <div>
          <strong>{items.length}</strong>
          <span>всего сессий</span>
        </div>
        <div>
          <strong>{activeCount}</strong>
          <span>в работе сейчас</span>
        </div>
        <div className={attentionCount ? "attention" : ""}>
          <strong>{attentionCount}</strong>
          <span>листов требуют внимания</span>
        </div>
      </section>

      {showCreate && (
        <section className="session-create-sheet" aria-label="Создание сессии">
          <div className="session-create-intro">
            <div className="eyebrow">Новый поток</div>
            <h2>Подготовить сессию</h2>
            <p>Класс и задание можно не указывать. Название сформируется автоматически, если оставить поле пустым.</p>
          </div>

          <div className="session-create-form">
            <label className="field">
              <span>Класс</span>
              <select value={classId} onChange={(event) => setClassId(event.target.value ? Number(event.target.value) : "")}>
                <option value="">Без привязки к классу</option>
                {(classes.data ?? []).map((classGroup) => (
                  <option key={classGroup.id} value={classGroup.id}>
                    {classGroup.name}
                  </option>
                ))}
              </select>
            </label>

            <label className="field">
              <span>Задание</span>
              <select value={taskId} onChange={(event) => setTaskId(event.target.value ? Number(event.target.value) : "")}>
                <option value="">Без привязки к заданию</option>
                {(tasks.data ?? []).map((task) => (
                  <option key={task.id} value={task.id}>
                    {task.external_id} — {task.title}
                  </option>
                ))}
              </select>
            </label>

            <label className="field session-title-field">
              <span>Название</span>
              <input
                type="text"
                placeholder="Например: 8Б · Контрольная по истории"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
              />
            </label>

            <label className="field">
              <span>Ожидается листов</span>
              <input
                type="number"
                min={0}
                placeholder="0"
                value={expected || ""}
                onChange={(event) => setExpected(Number(event.target.value) || 0)}
              />
            </label>
          </div>

          <div className="session-create-actions">
            <button className="btn" onClick={() => createSession(false)}>
              Сохранить черновик
            </button>
            <button className="btn primary" onClick={() => createSession(true)}>
              Создать и открыть камеру
            </button>
          </div>
        </section>
      )}

      <section className="section-block">
        <div className="section-heading">
          <h2>Последние сессии</h2>
          <span className="section-kicker">{sessions.loading ? "Обновляю список…" : `${items.length} записей`}</span>
        </div>

        <div className="sessions-library">
          {items.map((session) => {
            const problems = session.stats.duplicates + session.stats.unidentified + session.stats.low_quality + session.stats.rescan_required;
            const progress = session.expected_sheet_count
              ? Math.min(100, Math.round((session.stats.total / session.expected_sheet_count) * 100))
              : null;

            return (
              <article className="session-library-card" key={session.id}>
                <div className="session-library-main">
                  <div className="session-card-index">#{String(session.id).padStart(3, "0")}</div>
                  <div className="session-library-copy">
                    <div className="session-library-topline">
                      <Badge map={SESSION_STATUS_RU} value={session.status} />
                      <span>{fmtDate(session.created_at)}</span>
                    </div>
                    <h3>{session.title}</h3>
                    <p>
                      {session.class_name || "Без класса"}
                      {session.task_title ? ` · ${session.task_title}` : ""}
                    </p>
                  </div>
                </div>

                <div className="session-progress-block">
                  <div className="session-progress-copy">
                    <span>Обработано</span>
                    <strong>
                      {session.stats.total}
                      {session.expected_sheet_count ? ` / ${session.expected_sheet_count}` : ""}
                    </strong>
                  </div>
                  {progress !== null && (
                    <div className="session-progress-track" aria-label={`Прогресс ${progress}%`}>
                      <span style={{ width: `${progress}%` }} />
                    </div>
                  )}
                  <div className={`session-problem-line${problems ? " has-problems" : ""}`}>
                    {problems ? `${problems} требуют внимания` : "Замечаний нет"}
                  </div>
                </div>

                <div className="session-library-actions">
                  <Link className="btn primary" to={`/sessions/${session.id}/scan`}>
                    Открыть
                  </Link>
                  <Link className="btn teacher" to={`/sessions/${session.id}/review`}>
                    Проверить
                  </Link>
                  <details className="session-more">
                    <summary aria-label="Дополнительные действия">•••</summary>
                    <div className="session-more-menu">
                      <Link to={`/sessions/${session.id}/summary`}>Итоги сессии</Link>
                      <button onClick={() => exportAs(session.id, "xlsx")}>Экспорт XLSX</button>
                      <button onClick={() => exportAs(session.id, "csv")}>Экспорт CSV</button>
                      <button onClick={() => exportAs(session.id, "json")}>Экспорт JSON</button>
                      <button onClick={() => exportAs(session.id, "zip")}>Архив изображений</button>
                      <button className="danger-text" onClick={() => removeSession(session)}>
                        Удалить сессию
                      </button>
                    </div>
                  </details>
                </div>
              </article>
            );
          })}

          {!sessions.loading && items.length === 0 && (
            <div className="panel empty-state sessions-empty">
              <div className="empty-state-mark">✓</div>
              <h3>Пока нет ни одной сессии</h3>
              <p>Создай первый поток, подключи камеру и начни принимать работы учеников.</p>
              <button className="btn primary" onClick={() => setShowCreate(true)}>
                Создать первую сессию
              </button>
            </div>
          )}
        </div>
      </section>
    </>
  );
}
