import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Dashboard } from "../api/types";
import { Badge, SESSION_STATUS_RU, fmtBytes, fmtDate, useApi } from "../lib";

export default function DashboardPage() {
  const { data, loading, error, refresh } = useApi<Dashboard>(() => api.get("/dashboard"), []);

  return (
    <>
      <h1 className="page-title">
        Главная
        <span className="spacer" />
        <button className="btn small" onClick={refresh}>
          Обновить
        </button>
      </h1>

      {error && <div className="error-box">Backend недоступен: {error}. Запустите `uvicorn app.main:app` в каталоге backend.</div>}
      {loading && <p className="muted">Загрузка…</p>}

      {data && (
        <>
          <div className="grid cols-4">
            <div className="stat-card">
              <div className="value">{data.sheets_today}</div>
              <div className="label">Листов сегодня</div>
            </div>
            <div className="stat-card">
              <div className="value">{data.needs_review}</div>
              <div className="label">Требуют внимания</div>
            </div>
            <div className="stat-card">
              <div className="value">{data.average_speed || "—"}</div>
              <div className="label">Листов/мин (сегодня)</div>
            </div>
            <div className="stat-card">
              <div className="value">{fmtBytes(data.storage_bytes)}</div>
              <div className="label">Занято на диске · всего листов: {data.total_sheets}</div>
            </div>
          </div>

          <h3 className="section">Последняя сессия</h3>
          {data.last_session ? (
            <div className="panel">
              <div className="row">
                <strong>{data.last_session.title}</strong>
                <Badge map={SESSION_STATUS_RU} value={data.last_session.status} />
                <span className="muted">{fmtDate(data.last_session.created_at)}</span>
                <span className="spacer" style={{ flex: 1 }} />
                <Link className="btn small" to={`/sessions/${data.last_session.id}/scan`}>
                  Продолжить сканирование
                </Link>
                <Link className="btn small" to={`/sessions/${data.last_session.id}/review`}>
                  Проверка
                </Link>
              </div>
              <div className="row mt muted">
                <span>Всего: {data.last_session.stats.total}</span>
                <span>OK: {data.last_session.stats.ok}</span>
                <span>Дубликаты: {data.last_session.stats.duplicates}</span>
                <span>Без QR: {data.last_session.stats.unidentified}</span>
                <span>Ожидают OCR: {data.last_session.stats.pending_ocr}</span>
              </div>
            </div>
          ) : (
            <div className="panel muted">
              Сессий пока нет. <Link to="/sessions">Создайте первую сессию</Link>, распечатайте бланки и начните сканирование.
            </div>
          )}

          <h3 className="section">События оборудования</h3>
          <div className="panel">
            {data.hardware_events.length === 0 && <span className="muted">Событий нет — всё в порядке.</span>}
            {data.hardware_events.map((e) => (
              <div key={e.id} className="row" style={{ padding: "4px 0" }}>
                <span className={`badge ${e.level === "error" ? "red" : "amber"}`}>{e.level}</span>
                <span>{e.message || e.code}</span>
                <span className="muted">{fmtDate(e.created_at)}</span>
              </div>
            ))}
          </div>
        </>
      )}
    </>
  );
}
