import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, sheetImageUrl, wsUrl } from "../api/client";
import type { AnswerCheck, ScanSession, ScannedSheet, Student } from "../api/types";
import { Badge, RECOG_STATUS_RU, SCAN_STATUS_RU, useApi } from "../lib";

const ANSWER_VERDICT_RU: Record<string, { label: string; color: string; icon: string }> = {
  match: { label: "Совпадает с эталоном", color: "green", icon: "✓" },
  likely: { label: "Похоже на эталон", color: "amber", icon: "≈" },
  mismatch: { label: "Отличается от эталона", color: "red", icon: "✗" },
  unknown: { label: "Эталон не задан", color: "gray", icon: "?" },
};

const TABS: { id: string; label: string }[] = [
  { id: "all", label: "Все" },
  { id: "high_confidence", label: "Уверенные" },
  { id: "needs_review", label: "Нужна проверка" },
  { id: "low_confidence", label: "Низкая уверенность" },
  { id: "failed", label: "Ошибки OCR" },
  { id: "blank", label: "Пустые" },
  { id: "unidentified", label: "Без ученика" },
  { id: "rescan", label: "Пересканировать" },
];

export default function ReviewPage() {
  const { id } = useParams();
  const sessionId = Number(id);
  const session = useApi<ScanSession>(() => api.get(`/sessions/${sessionId}`), [sessionId]);

  const [tab, setTab] = useState("all");
  const counts = useApi<Record<string, number>>(() => api.get(`/sessions/${sessionId}/review/counts`), [sessionId]);
  const sheets = useApi<ScannedSheet[]>(() => api.get(`/sessions/${sessionId}/review?tab=${tab}`), [sessionId, tab]);

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const selected = useMemo(() => sheets.data?.find((s) => s.id === selectedId) ?? null, [sheets.data, selectedId]);

  const [teacherText, setTeacherText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [imageKind, setImageKind] = useState<"enhanced" | "normalized" | "answer" | "source">("enhanced");

  const students = useApi<Student[]>(
    () => (session.data?.class_id ? api.get(`/students?class_id=${session.data.class_id}`) : api.get("/students")),
    [session.data?.class_id],
  );

  // Live OCR progress: refresh the current lists when the queue reports done.
  useEffect(() => {
    const ws = new WebSocket(wsUrl("/ws/ocr"));
    let timer: number | null = null;
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type === "ocr" || msg.type === "recognition" || msg.sheetId) {
        if (timer) window.clearTimeout(timer);
        timer = window.setTimeout(() => {
          sheets.refresh();
          counts.refresh();
        }, 400);
      }
    };
    return () => {
      if (timer) window.clearTimeout(timer);
      ws.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, tab]);

  useEffect(() => {
    setTeacherText(selected?.review?.teacher_text || selected?.recognition?.recognized_text || "");
  }, [selectedId, selected?.recognition?.recognized_text, selected?.review?.teacher_text]);

  async function act(fn: () => Promise<unknown>) {
    try {
      setError(null);
      await fn();
      sheets.refresh();
      counts.refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const submitDecision = (decision: string) =>
    act(() => api.post(`/sheets/${selectedId}/review`, { decision, teacher_text: teacherText, comment: "" }));

  const confidence = selected?.recognition?.overall_confidence ?? 0;
  const confColor = confidence >= 0.85 ? "var(--green)" : confidence >= 0.6 ? "var(--amber)" : "var(--red)";

  return (
    <>
      <h1 className="page-title">
        Проверка: {session.data?.title ?? `сессия ${sessionId}`}
        <span className="spacer" />
        <button
          className="btn small"
          onClick={() => act(() => api.post(`/sessions/${sessionId}/recognize-all?only_missing=true`))}
          title="Поставить в очередь OCR все листы без результата"
        >
          Распознать все
        </button>
        <button className="btn small" onClick={() => api.download(`/sessions/${sessionId}/export/xlsx`, `session_${sessionId}.xlsx`).catch((e) => setError(e.message))}>
          Экспорт XLSX
        </button>
        <Link className="btn small" to={`/sessions/${sessionId}/scan`}>
          ← К сканированию
        </Link>
      </h1>

      {error && <div className="error-box">{error}</div>}

      <div className="tabs">
        {TABS.map((t) => (
          <button key={t.id} className={`tab${tab === t.id ? " active" : ""}`} onClick={() => setTab(t.id)}>
            {t.label}
            {counts.data ? ` (${counts.data[t.id] ?? 0})` : ""}
          </button>
        ))}
      </div>

      <div className="review-layout">
        <div className="sheet-list">
          {sheets.loading && <span className="muted">Загрузка…</span>}
          {sheets.data?.length === 0 && <span className="muted">В этой вкладке листов нет.</span>}
          {(sheets.data ?? []).map((s) => (
            <div key={s.id} className={`sheet-item${s.id === selectedId ? " selected" : ""}`} onClick={() => setSelectedId(s.id)}>
              <img src={sheetImageUrl(s.id, "thumbnail")} alt="" loading="lazy" />
              <div className="meta">
                <div className="name">
                  #{s.sequence_number} {s.student_name || s.student_external_id || "Неизвестный ученик"}
                </div>
                <div className="sub">
                  <Badge map={SCAN_STATUS_RU} value={s.scan_status} />{" "}
                  {s.recognition && <Badge map={RECOG_STATUS_RU} value={s.recognition.status} />}{" "}
                  {s.recognition?.analysis_json?.answerCheck && s.recognition.analysis_json.answerCheck.verdict !== "unknown" && (
                    <span
                      className={`badge ${ANSWER_VERDICT_RU[s.recognition.analysis_json.answerCheck.verdict]?.color ?? "gray"}`}
                      title={ANSWER_VERDICT_RU[s.recognition.analysis_json.answerCheck.verdict]?.label}
                    >
                      {ANSWER_VERDICT_RU[s.recognition.analysis_json.answerCheck.verdict]?.icon}
                    </span>
                  )}
                </div>
                {s.recognition?.recognized_text && (
                  <div className="sub" style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 200 }}>
                    «{s.recognition.recognized_text}»
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>

        <div>
          {!selected && <div className="panel muted">Выберите лист слева.</div>}
          {selected && (
            <div className="panel">
              <div className="row mb">
                <strong>
                  Лист #{selected.sequence_number} ·{" "}
                  {selected.student_id ? (
                    <Link to={`/students/${selected.student_id}`}>{selected.student_name}</Link>
                  ) : (
                    "ученик не определён"
                  )}
                </strong>
                <Badge map={SCAN_STATUS_RU} value={selected.scan_status} />
                {selected.recognition && <Badge map={RECOG_STATUS_RU} value={selected.recognition.status} />}
                <span className="muted">качество {(selected.quality_score * 100).toFixed(0)}%</span>
              </div>

              <div className="row mb">
                {(["enhanced", "normalized", "answer", "source"] as const).map((k) => (
                  <button key={k} className={`btn small${imageKind === k ? " primary" : ""}`} onClick={() => setImageKind(k)}>
                    {{ enhanced: "Улучшенное", normalized: "Выровненное", answer: "Зона ответа", source: "Оригинал" }[k]}
                  </button>
                ))}
              </div>

              <div className="sheet-preview mb">
                <img src={sheetImageUrl(selected.id, imageKind)} alt="Лист" />
              </div>

              {selected.recognition && (
                <>
                  <div className="row mb">
                    <span className="muted">Уверенность распознавания:</span>
                    <div className="confidence-bar" style={{ flex: 1, maxWidth: 260 }}>
                      <div style={{ width: `${Math.round(confidence * 100)}%`, background: confColor }} />
                    </div>
                    <span>{(confidence * 100).toFixed(0)}%</span>
                    <span className="muted">
                      {selected.recognition.provider}
                      {selected.recognition.model_name ? ` · ${selected.recognition.model_name}` : ""}
                    </span>
                  </div>
                  {selected.recognition.error_message && <div className="error-box">{selected.recognition.error_message}</div>}
                  <AnswerHint check={selected.recognition.analysis_json?.answerCheck} />
                </>
              )}

              <label className="field">
                <span>Текст ответа (распознанный / исправленный учителем)</span>
                <textarea value={teacherText} onChange={(e) => setTeacherText(e.target.value)} rows={3} />
              </label>

              <div className="row">
                <button className="btn success" onClick={() => submitDecision(teacherText === (selected.recognition?.recognized_text ?? "") ? "accepted" : "corrected")}>
                  ✓ Принять
                </button>
                <button className="btn" onClick={() => act(() => api.post(`/sheets/${selected.id}/recognize`))}>
                  ↻ Распознать заново
                </button>
                <button className="btn" onClick={() => act(() => api.post(`/sheets/${selected.id}/blank-override?is_blank=true`))}>
                  Пустой ответ
                </button>
                <button className="btn" onClick={() => submitDecision("rescan_required")}>
                  Пересканировать
                </button>
                <button className="btn danger" onClick={() => submitDecision("unreadable")}>
                  Нечитаемо
                </button>
              </div>

              <h3 className="section">Привязка к ученику</h3>
              <div className="row">
                <select
                  value={selected.student_id ?? ""}
                  onChange={(e) =>
                    act(() =>
                      api.patch(`/sheets/${selected.id}/assign`, {
                        student_id: e.target.value ? Number(e.target.value) : null,
                      }),
                    )
                  }
                  style={{ maxWidth: 320 }}
                >
                  <option value="">— не привязан —</option>
                  {(students.data ?? []).map((st) => (
                    <option key={st.id} value={st.id}>
                      {st.external_id} · {st.display_name}
                    </option>
                  ))}
                </select>
                {selected.duplicate_of_id && (
                  <button className="btn small" onClick={() => act(() => api.patch(`/sheets/${selected.id}/assign`, { clear_duplicate: true }))}>
                    Снять метку «дубликат» (№{selected.duplicate_of_id})
                  </button>
                )}
                <button
                  className="btn small danger"
                  onClick={() => {
                    if (!confirm("Удалить лист?")) return;
                    act(async () => {
                      await api.delete(`/sheets/${selected.id}`);
                      setSelectedId(null);
                    });
                  }}
                >
                  Удалить лист
                </button>
              </div>

              {selected.warnings && selected.warnings.length > 0 && (
                <>
                  <h3 className="section">Предупреждения</h3>
                  <ul className="muted" style={{ margin: 0, paddingLeft: 18 }}>
                    {selected.warnings.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </>
  );
}

/** Hint block: recognized/corrected answer vs the task's expected answer.
 *  Never a grade — always rendered with the disclaimer. */
function AnswerHint({ check }: { check?: AnswerCheck }) {
  if (!check || check.verdict === "unknown") return null;
  const info = ANSWER_VERDICT_RU[check.verdict] ?? ANSWER_VERDICT_RU.unknown;
  return (
    <div className="row mb" title={check.disclaimer}>
      <span className="muted">Сверка с эталоном:</span>
      <span className={`badge ${info.color}`}>
        {info.icon} {info.label}
      </span>
      {check.editDistance !== null && check.editDistance > 0 && (
        <span className="muted" style={{ fontSize: 12 }}>
          расхождение: {check.editDistance} симв.
        </span>
      )}
      {check.source === "teacher_text" && (
        <span className="muted" style={{ fontSize: 12 }}>
          по исправленному тексту
        </span>
      )}
      <span className="muted" style={{ fontSize: 12 }}>
        {check.disclaimer}
      </span>
    </div>
  );
}
