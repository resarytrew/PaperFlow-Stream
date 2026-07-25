import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Student } from "../api/types";
import { Badge, SCAN_STATUS_RU, fmtDate, useApi } from "../lib";

interface HistorySheet {
  sheetId: number;
  sessionId: number;
  sessionTitle: string;
  taskTitle: string | null;
  taskExternalId: string | null;
  scannedAt: string | null;
  scanStatus: string;
  quality: number;
  answer: string;
  verdict: string;
  reviewed: boolean;
  reviewDecision: string | null;
}

interface History {
  student: Student;
  sheets: HistorySheet[];
  verdicts: Record<string, number>;
  totalSheets: number;
  matchRate: number | null;
  disclaimer: string;
}

const VERDICT_RU: Record<string, { label: string; color: string; icon: string }> = {
  match: { label: "Совпало", color: "green", icon: "✓" },
  likely: { label: "Похоже", color: "amber", icon: "≈" },
  mismatch: { label: "Отличается", color: "red", icon: "✗" },
  unknown: { label: "Без сверки", color: "gray", icon: "—" },
};

export default function StudentPage() {
  const { id } = useParams();
  const studentId = Number(id);
  const history = useApi<History>(() => api.get(`/students/${studentId}/history`), [studentId]);
  const data = history.data;

  const checked = data ? data.verdicts.match + data.verdicts.likely + data.verdicts.mismatch : 0;

  return (
    <>
      <h1 className="page-title">
        {data ? data.student.display_name : "Ученик"}
        {data?.student.class_name && <span className="badge blue">{data.student.class_name}</span>}
        {data && <span className="muted" style={{ fontSize: 14 }}>ID: {data.student.external_id}</span>}
        <span className="spacer" />
        <Link className="btn small" to="/catalog">
          ← К классам
        </Link>
      </h1>

      {history.error && <div className="error-box">{history.error}</div>}
      {history.loading && <p className="muted">Загрузка…</p>}

      {data && (
        <>
          <div className="grid cols-4">
            <div className="stat-card">
              <div className="value">{data.totalSheets}</div>
              <div className="label">Всего листов</div>
            </div>
            <div className="stat-card">
              <div className="value" style={{ color: "var(--green)" }}>
                {data.matchRate !== null ? `${Math.round(data.matchRate * 100)}%` : "—"}
              </div>
              <div className="label">Совпадений с эталоном (из {checked} сверенных)</div>
            </div>
            <div className="stat-card">
              <div className="value" style={{ color: "var(--red)" }}>{data.verdicts.mismatch}</div>
              <div className="label">Отличались от эталона · похожих: {data.verdicts.likely}</div>
            </div>
            <div className="stat-card">
              <div className="value">{data.sheets.filter((s) => s.reviewed).length}</div>
              <div className="label">Проверено учителем</div>
            </div>
          </div>

          {/* mini timeline: newest on the right */}
          {checked > 0 && (
            <div className="panel mt">
              <div className="row" style={{ gap: 4, flexWrap: "wrap" }}>
                <span className="muted" style={{ marginRight: 8 }}>Хронология (старые → новые):</span>
                {[...data.sheets]
                  .reverse()
                  .filter((s) => s.verdict !== "unknown")
                  .map((s) => {
                    const v = VERDICT_RU[s.verdict];
                    return (
                      <span
                        key={s.sheetId}
                        className={`badge ${v.color}`}
                        title={`${s.taskTitle ?? s.sessionTitle} · ${fmtDate(s.scannedAt)}`}
                        style={{ cursor: "default" }}
                      >
                        {v.icon}
                      </span>
                    );
                  })}
              </div>
            </div>
          )}

          <div className="panel mt">
            <table className="data">
              <thead>
                <tr>
                  <th>Дата</th>
                  <th>Сессия / задание</th>
                  <th>Ответ</th>
                  <th>Сверка</th>
                  <th>Скан</th>
                  <th>Проверен</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.sheets.map((s) => {
                  const v = VERDICT_RU[s.verdict] ?? VERDICT_RU.unknown;
                  return (
                    <tr key={s.sheetId}>
                      <td className="muted">{fmtDate(s.scannedAt)}</td>
                      <td>
                        {s.taskTitle ?? s.sessionTitle}
                        {s.taskExternalId && <span className="muted" style={{ fontSize: 12 }}> · {s.taskExternalId}</span>}
                      </td>
                      <td style={{ maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {s.answer || <span className="muted">—</span>}
                      </td>
                      <td>
                        <span className={`badge ${v.color}`}>
                          {v.icon} {v.label}
                        </span>
                      </td>
                      <td>
                        <Badge map={SCAN_STATUS_RU} value={s.scanStatus} />
                      </td>
                      <td>{s.reviewed ? "✓" : <span className="muted">—</span>}</td>
                      <td>
                        <Link className="btn small" to={`/sessions/${s.sessionId}/review`}>
                          Открыть
                        </Link>
                      </td>
                    </tr>
                  );
                })}
                {data.sheets.length === 0 && (
                  <tr>
                    <td colSpan={7} className="muted">
                      У ученика пока нет отсканированных листов.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <p className="muted mt" style={{ fontSize: 13 }}>
            {data.disclaimer}
          </p>
        </>
      )}
    </>
  );
}
