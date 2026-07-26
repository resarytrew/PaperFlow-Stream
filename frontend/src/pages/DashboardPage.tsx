import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Dashboard } from "../api/types";
import { Badge, SESSION_STATUS_RU, fmtBytes, fmtDate, useApi } from "../lib";

export default function DashboardPage() {
  const { data, loading, error, refresh } = useApi<Dashboard>(() => api.get("/dashboard"), []);

  return (
    <>
      <section className="page-heading">
        <div className="page-heading-copy">
          <div className="eyebrow">Обзор проверки</div>
          <h1>Рабочий стол</h1>
          <p>Здесь собран текущий поток: сколько листов принято, что требует внимания и где продолжить работу.</p>
        </div>
        <div className="page-heading-actions">
          <button className="btn" onClick={refresh}>
            Обновить данные
          </button>
          <Link className="btn primary" to="/sessions">
            Новая сессия
          </Link>
        </div>
      </section>

      {error && <div className="error-box">Локальный модуль недоступен: {error}</div>}
      {loading && <p className="muted">Собираю актуальные данные…</p>}

      {data && (
        <>
          <div className="grid dashboard-stats">
            <div className="stat-card" data-index="01">
              <span className="stat-accent" />
              <div className="value">{data.sheets_today}</div>
              <div className="label">Листов обработано сегодня</div>
            </div>
            <div className="stat-card attention" data-index="02">
              <span className="stat-accent" />
              <div className="value">{data.needs_review}</div>
              <div className="label">Работ требуют внимания учителя</div>
            </div>
            <div className="stat-card speed" data-index="03">
              <span className="stat-accent" />
              <div className="value">{data.average_speed || "—"}</div>
              <div className="label">Средняя скорость, листов в минуту</div>
            </div>
            <div className="stat-card storage" data-index="04">
              <span className="stat-accent" />
              <div className="value">{fmtBytes(data.storage_bytes)}</div>
              <div className="label">Локальный архив · всего {data.total_sheets} листов</div>
            </div>
          </div>

          <section className="section-block">
            <div className="section-heading">
              <h2>Последняя сессия</h2>
              <span className="section-kicker">Продолжи с того места, где остановился</span>
            </div>

            {data.last_session ? (
              <div className="panel session-card">
                <div className="session-card-head">
                  <div>
                    <h3 className="session-title">{data.last_session.title}</h3>
                    <div className="session-meta">
                      <Badge map={SESSION_STATUS_RU} value={data.last_session.status} />
                      <span>{fmtDate(data.last_session.created_at)}</span>
                    </div>
                  </div>
                  <div className="session-actions">
                    <Link className="btn" to={`/sessions/${data.last_session.id}/scan`}>
                      Продолжить сканирование
                    </Link>
                    <Link className="btn teacher" to={`/sessions/${data.last_session.id}/review`}>
                      Перейти к проверке
                    </Link>
                  </div>
                </div>

                <div className="session-stats">
                  <div className="session-stat">
                    <strong>{data.last_session.stats.total}</strong>
                    <span>Всего листов</span>
                  </div>
                  <div className="session-stat">
                    <strong>{data.last_session.stats.ok}</strong>
                    <span>Принято без замечаний</span>
                  </div>
                  <div className="session-stat">
                    <strong>{data.last_session.stats.duplicates}</strong>
                    <span>Найдено дубликатов</span>
                  </div>
                  <div className="session-stat">
                    <strong>{data.last_session.stats.unidentified}</strong>
                    <span>Листов без QR</span>
                  </div>
                  <div className="session-stat">
                    <strong>{data.last_session.stats.pending_ocr}</strong>
                    <span>Ожидают распознавания</span>
                  </div>
                </div>
              </div>
            ) : (
              <div className="panel empty-state muted">
                Сессий пока нет. <Link to="/sessions">Создай первую сессию</Link>, подготовь бланки и запусти потоковую проверку.
              </div>
            )}
          </section>

          <section className="section-block">
            <div className="section-heading">
              <h2>Состояние оборудования</h2>
              <span className="section-kicker">Камера и локальные сервисы</span>
            </div>
            <div className="panel hardware-list">
              {data.hardware_events.length === 0 && <div className="all-clear">Событий нет — система готова к работе</div>}
              {data.hardware_events.map((event) => (
                <div key={event.id} className="hardware-row">
                  <span className={`badge ${event.level === "error" ? "red" : "amber"}`}>{event.level}</span>
                  <span>{event.message || event.code}</span>
                  <span className="muted">{fmtDate(event.created_at)}</span>
                </div>
              ))}
            </div>
          </section>
        </>
      )}
    </>
  );
}
