import { useParams, Link } from "react-router-dom";
import { api } from "../api/client";
import type { ScanSession } from "../api/types";
import { Badge, SCAN_STATUS_RU, SESSION_STATUS_RU, useApi } from "../lib";

interface SummarySheet {
  sheetId: number;
  number: number;
  student: string | null;
  externalId: string | null;
  scanStatus: string;
  quality: number;
  answer: string;
  verdict: string;
  confidence: number | null;
  reviewed: boolean;
}

interface Summary {
  session: ScanSession;
  sheets: SummarySheet[];
  verdicts: Record<string, number>;
  blank: number;
  reviewed: number;
  corrected: number;
  averageQuality: number;
  durationMinutes: number | null;
  disclaimer: string;
}

interface Roster {
  classLinked: boolean;
  students: { studentId: number; externalId: string; name: string; status: string }[];
  submitted: number;
  missing: number;
  totalStudents: number;
}

const VERDICT_RU: Record<string, { label: string; color: string; icon: string }> = {
  match: { label: "Совпало", color: "green", icon: "✓" },
  likely: { label: "Похоже", color: "amber", icon: "≈" },
  mismatch: { label: "Отличается", color: "red", icon: "✗" },
  unknown: { label: "Без сверки", color: "gray", icon: "—" },
};

export default function SummaryPage() {
  const { id } = useParams();
  const sessionId = Number(id);
  const summary = useApi<Summary>(() => api.get(`/sessions/${sessionId}/summary`), [sessionId]);
  const roster = useApi<Roster>(() => api.get(`/sessions/${sessionId}/roster`), [sessionId]);

  const data = summary.data;
  const missing = roster.data?.classLinked ? roster.data.students.filter((s) => s.status === "missing") : [];

  return (
    <>
      <h1 className="page-title no-print-margin">
        Итоги: {data?.session.title ?? `сессия ${sessionId}`}
        {data && <Badge map={SESSION_STATUS_RU} value={data.session.status} />}
        <span className="spacer" />
        <button className="btn small no-print" onClick={() => window.print()}>
          🖨 Печать
        </button>
        <Link className="btn small no-print" to={`/sessions/${sessionId}/review`}>
          К проверке
        </Link>
        <Link className="btn small no-print" to={`/sessions/${sessionId}/scan`}>
          К сканированию
        </Link>
      </h1>

      {summary.error && <div className="error-box">{summary.error}</div>}
      {summary.loading && <p className="muted">Загрузка…</p>}

      {data && (
        <>
          <div className="grid cols-4">
            <div className="stat-card">
              <div className="value">
                {data.sheets.length}
                {data.session.expected_sheet_count ? ` / ${data.session.expected_sheet_count}` : ""}
              </div>
              <div className="label">Листов отсканировано{roster.data?.classLinked ? ` · сдали ${roster.data.submitted} из ${roster.data.totalStudents}` : ""}</div>
            </div>
            <div className="stat-card">
              <div className="value" style={{ color: "var(--green)" }}>
                {data.verdicts.match ?? 0}
                <span className="muted" style={{ fontSize: 16 }}> ✓</span>
              </div>
              <div className="label">Совпали с эталоном · похоже: {data.verdicts.likely ?? 0} · отличается: {data.verdicts.mismatch ?? 0}</div>
            </div>
            <div className="stat-card">
              <div className="value">{data.reviewed}</div>
              <div className="label">Проверено учителем · исправлено: {data.corrected} · пустых: {data.blank}</div>
            </div>
            <div className="stat-card">
              <div className="value">{(data.averageQuality * 100).toFixed(0)}%</div>
              <div className="label">Среднее качество{data.durationMinutes ? ` · ${data.durationMinutes} мин` : ""}</div>
            </div>
          </div>

          {missing.length > 0 && (
            <div className="error-box mt">
              Не сдали ({missing.length}): {missing.map((s) => s.name || s.externalId).join(", ")}
            </div>
          )}

          <div className="panel mt">
            <table className="data">
              <thead>
                <tr>
                  <th>№</th>
                  <th>Ученик</th>
                  <th>Ответ</th>
                  <th>Сверка</th>
                  <th>OCR</th>
                  <th>Скан</th>
                  <th>Проверен</th>
                </tr>
              </thead>
              <tbody>
                {data.sheets.map((s) => {
                  const v = VERDICT_RU[s.verdict] ?? VERDICT_RU.unknown;
                  return (
                    <tr key={s.sheetId}>
                      <td>{s.number}</td>
                      <td>
                        {s.student ?? <span className="muted">не определён</span>}
                        {s.externalId && <span className="muted" style={{ fontSize: 12 }}> · {s.externalId}</span>}
                      </td>
                      <td style={{ maxWidth: 340, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {s.answer || <span className="muted">—</span>}
                      </td>
                      <td>
                        <span className={`badge ${v.color}`}>
                          {v.icon} {v.label}
                        </span>
                      </td>
                      <td className="muted">{s.confidence !== null ? `${Math.round(s.confidence * 100)}%` : "—"}</td>
                      <td>
                        <Badge map={SCAN_STATUS_RU} value={s.scanStatus} />
                      </td>
                      <td>{s.reviewed ? "✓" : <span className="muted">—</span>}</td>
                    </tr>
                  );
                })}
                {data.sheets.length === 0 && (
                  <tr>
                    <td colSpan={7} className="muted">
                      В сессии нет листов.
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
