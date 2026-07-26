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

const IMAGE_LABELS = {
  enhanced: "Улучшенное",
  normalized: "Выровненное",
  answer: "Зона ответа",
  source: "Оригинал",
} as const;

export default function ReviewPage() {
  const { id } = useParams();
  const sessionId = Number(id);
  const session = useApi<ScanSession>(() => api.get(`/sessions/${sessionId}`), [sessionId]);

  const [tab, setTab] = useState("all");
  const counts = useApi<Record<string, number>>(() => api.get(`/sessions/${sessionId}/review/counts`), [sessionId]);
  const sheets = useApi<ScannedSheet[]>(() => api.get(`/sessions/${sessionId}/review?tab=${tab}`), [sessionId, tab]);

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const selected = useMemo(() => sheets.data?.find((sheet) => sheet.id === selectedId) ?? null, [sheets.data, selectedId]);

  const [teacherText, setTeacherText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [imageKind, setImageKind] = useState<"enhanced" | "normalized" | "answer" | "source">("enhanced");

  const students = useApi<Student[]>(
    () => (session.data?.class_id ? api.get(`/students?class_id=${session.data.class_id}`) : api.get("/students")),
    [session.data?.class_id],
  );

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
    if (!selectedId && sheets.data?.length) setSelectedId(sheets.data[0].id);
    if (selectedId && sheets.data && !sheets.data.some((sheet) => sheet.id === selectedId)) {
      setSelectedId(sheets.data[0]?.id ?? null);
    }
  }, [selectedId, sheets.data]);

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
  const confidencePercent = Math.round(confidence * 100);
  const confidenceClass = confidence >= 0.85 ? "good" : confidence >= 0.6 ? "medium" : "low";
  const selectedPosition = selected ? (sheets.data ?? []).findIndex((sheet) => sheet.id === selected.id) + 1 : 0;

  function moveSelection(direction: -1 | 1) {
    const list = sheets.data ?? [];
    if (!list.length) return;
    const currentIndex = Math.max(0, list.findIndex((sheet) => sheet.id === selectedId));
    const nextIndex = Math.min(list.length - 1, Math.max(0, currentIndex + direction));
    setSelectedId(list[nextIndex].id);
  }

  return (
    <div className="review-page">
      <section className="review-heading">
        <div>
          <div className="eyebrow">Проверочный стол</div>
          <h1>{session.data?.title ?? `Сессия ${sessionId}`}</h1>
          <p>Слева — очередь работ, по центру — оригинал, справа — решение учителя.</p>
        </div>
        <div className="review-heading-actions">
          <button
            className="btn"
            onClick={() => act(() => api.post(`/sessions/${sessionId}/recognize-all?only_missing=true`))}
            title="Поставить в очередь OCR все листы без результата"
          >
            Распознать пропущенные
          </button>
          <button
            className="btn"
            onClick={() => api.download(`/sessions/${sessionId}/export/xlsx`, `session_${sessionId}.xlsx`).catch((nextError) => setError(nextError.message))}
          >
            Экспорт XLSX
          </button>
          <Link className="btn primary" to={`/sessions/${sessionId}/scan`}>
            Вернуться к камере
          </Link>
        </div>
      </section>

      {error && <div className="error-box">{error}</div>}

      <div className="review-filterbar">
        <div className="review-filter-scroll">
          {TABS.map((item) => (
            <button key={item.id} className={`review-filter${tab === item.id ? " active" : ""}`} onClick={() => setTab(item.id)}>
              <span>{item.label}</span>
              <strong>{counts.data?.[item.id] ?? 0}</strong>
            </button>
          ))}
        </div>
        <div className="review-counter">
          {selected ? `${selectedPosition} из ${sheets.data?.length ?? 0}` : `${sheets.data?.length ?? 0} работ`}
        </div>
      </div>

      <div className="review-desk">
        <aside className="review-queue" aria-label="Очередь работ">
          <div className="review-queue-head">
            <div>
              <span>Очередь</span>
              <strong>{sheets.data?.length ?? 0}</strong>
            </div>
            <div className="review-nav-buttons">
              <button onClick={() => moveSelection(-1)} disabled={!selected || selectedPosition <= 1} aria-label="Предыдущая работа">
                ↑
              </button>
              <button
                onClick={() => moveSelection(1)}
                disabled={!selected || selectedPosition >= (sheets.data?.length ?? 0)}
                aria-label="Следующая работа"
              >
                ↓
              </button>
            </div>
          </div>

          <div className="review-queue-list">
            {sheets.loading && <div className="review-empty-note">Загружаю работы…</div>}
            {!sheets.loading && sheets.data?.length === 0 && <div className="review-empty-note">В этой категории работ нет.</div>}
            {(sheets.data ?? []).map((sheet) => {
              const recognitionConfidence = Math.round((sheet.recognition?.overall_confidence ?? 0) * 100);
              const answerCheck = sheet.recognition?.analysis_json?.answerCheck;
              return (
                <button
                  type="button"
                  key={sheet.id}
                  className={`review-queue-item${sheet.id === selectedId ? " selected" : ""}`}
                  onClick={() => setSelectedId(sheet.id)}
                >
                  <img src={sheetImageUrl(sheet.id, "thumbnail")} alt="" loading="lazy" />
                  <span className="review-queue-copy">
                    <span className="review-queue-number">Лист {sheet.sequence_number}</span>
                    <strong>{sheet.student_name || sheet.student_external_id || "Ученик не определён"}</strong>
                    <span className="review-queue-status">
                      {sheet.recognition?.recognized_text ? `«${sheet.recognition.recognized_text}»` : "Текст ещё не распознан"}
                    </span>
                  </span>
                  <span className="review-queue-meta">
                    <span>{recognitionConfidence ? `${recognitionConfidence}%` : "—"}</span>
                    {answerCheck && answerCheck.verdict !== "unknown" && (
                      <span className={`queue-verdict ${ANSWER_VERDICT_RU[answerCheck.verdict]?.color ?? "gray"}`}>
                        {ANSWER_VERDICT_RU[answerCheck.verdict]?.icon}
                      </span>
                    )}
                  </span>
                </button>
              );
            })}
          </div>
        </aside>

        <main className="review-document-stage">
          {!selected && (
            <div className="review-stage-empty">
              <div className="empty-state-mark">✓</div>
              <h2>Выбери работу в очереди</h2>
              <p>Изображение листа и результат распознавания появятся здесь.</p>
            </div>
          )}

          {selected && (
            <>
              <div className="review-document-toolbar">
                <div>
                  <span className="review-document-label">Лист #{selected.sequence_number}</span>
                  <strong>{selected.student_name || selected.student_external_id || "Ученик не определён"}</strong>
                </div>
                <div className="review-image-switcher">
                  {(Object.keys(IMAGE_LABELS) as Array<keyof typeof IMAGE_LABELS>).map((kind) => (
                    <button key={kind} className={imageKind === kind ? "active" : ""} onClick={() => setImageKind(kind)}>
                      {IMAGE_LABELS[kind]}
                    </button>
                  ))}
                </div>
              </div>

              <div className="review-paper-frame">
                <img src={sheetImageUrl(selected.id, imageKind)} alt={`Лист ${selected.sequence_number}`} />
              </div>

              <div className="review-document-footer">
                <Badge map={SCAN_STATUS_RU} value={selected.scan_status} />
                {selected.recognition && <Badge map={RECOG_STATUS_RU} value={selected.recognition.status} />}
                <span>Качество изображения {Math.round(selected.quality_score * 100)}%</span>
                {selected.recognition?.provider && <span>{selected.recognition.provider}</span>}
              </div>
            </>
          )}
        </main>

        <aside className="review-decision-panel" aria-label="Решение учителя">
          {!selected && <div className="review-empty-note">Панель решения откроется после выбора листа.</div>}
          {selected && (
            <>
              <div className="decision-panel-head">
                <div>
                  <span>Решение учителя</span>
                  <h2>{selected.student_name || "Неизвестная работа"}</h2>
                </div>
                <span className={`confidence-seal ${confidenceClass}`}>{confidencePercent || "—"}%</span>
              </div>

              {selected.recognition && (
                <div className="confidence-section">
                  <div className="confidence-copy">
                    <span>Уверенность OCR</span>
                    <strong>{confidencePercent}%</strong>
                  </div>
                  <div className={`confidence-track ${confidenceClass}`}>
                    <span style={{ width: `${confidencePercent}%` }} />
                  </div>
                  {selected.recognition.error_message && <div className="error-box compact">{selected.recognition.error_message}</div>}
                  <AnswerHint check={selected.recognition.analysis_json?.answerCheck} />
                </div>
              )}

              <label className="field review-text-field">
                <span>Распознанный ответ</span>
                <textarea value={teacherText} onChange={(event) => setTeacherText(event.target.value)} rows={7} />
              </label>

              <div className="decision-primary-actions">
                <button
                  className="decision-accept"
                  onClick={() => submitDecision(teacherText === (selected.recognition?.recognized_text ?? "") ? "accepted" : "corrected")}
                >
                  <span>✓</span>
                  <strong>{teacherText === (selected.recognition?.recognized_text ?? "") ? "Принять ответ" : "Принять исправление"}</strong>
                  <small>Работа будет отмечена проверенной</small>
                </button>

                <button className="decision-rescan" onClick={() => submitDecision("rescan_required")}>
                  Пересканировать
                </button>
                <button className="decision-unreadable" onClick={() => submitDecision("unreadable")}>
                  Нечитаемо
                </button>
              </div>

              <details className="decision-tools">
                <summary>Инструменты распознавания</summary>
                <div>
                  <button className="btn" onClick={() => act(() => api.post(`/sheets/${selected.id}/recognize`))}>
                    Распознать заново
                  </button>
                  <button
                    className="btn primary"
                    title="Второй проход через Yandex Vision OCR. Требует явного разрешения в настройках приватности."
                    onClick={() => act(() => api.post(`/sheets/${selected.id}/recognize-vision`))}
                  >
                    Yandex Vision
                  </button>
                  <button className="btn" onClick={() => act(() => api.post(`/sheets/${selected.id}/blank-override?is_blank=true`))}>
                    Пустой ответ
                  </button>
                </div>
              </details>

              <details className="decision-tools">
                <summary>Ученик и служебные действия</summary>
                <div className="decision-service-stack">
                  <label className="field">
                    <span>Привязать к ученику</span>
                    <select
                      value={selected.student_id ?? ""}
                      onChange={(event) =>
                        act(() =>
                          api.patch(`/sheets/${selected.id}/assign`, {
                            student_id: event.target.value ? Number(event.target.value) : null,
                          }),
                        )
                      }
                    >
                      <option value="">Не привязан</option>
                      {(students.data ?? []).map((student) => (
                        <option key={student.id} value={student.id}>
                          {student.external_id} · {student.display_name}
                        </option>
                      ))}
                    </select>
                  </label>

                  {selected.student_id && <Link to={`/students/${selected.student_id}`}>Открыть карточку ученика</Link>}
                  {selected.duplicate_of_id && (
                    <button className="btn" onClick={() => act(() => api.patch(`/sheets/${selected.id}/assign`, { clear_duplicate: true }))}>
                      Снять метку дубликата №{selected.duplicate_of_id}
                    </button>
                  )}
                  <button
                    className="btn danger"
                    onClick={() => {
                      if (!confirm("Удалить лист?")) return;
                      void act(async () => {
                        await api.delete(`/sheets/${selected.id}`);
                        setSelectedId(null);
                      });
                    }}
                  >
                    Удалить лист
                  </button>
                </div>
              </details>

              {selected.warnings && selected.warnings.length > 0 && (
                <div className="decision-warnings">
                  <strong>Предупреждения</strong>
                  <ul>
                    {selected.warnings.map((warning, index) => (
                      <li key={index}>{warning}</li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </aside>
      </div>
    </div>
  );
}

function AnswerHint({ check }: { check?: AnswerCheck }) {
  if (!check || check.verdict === "unknown") return null;
  const info = ANSWER_VERDICT_RU[check.verdict] ?? ANSWER_VERDICT_RU.unknown;
  return (
    <div className={`answer-hint-card ${info.color}`} title={check.disclaimer}>
      <div className="answer-hint-icon">{info.icon}</div>
      <div>
        <span>Сверка с эталоном</span>
        <strong>{info.label}</strong>
        {check.editDistance !== null && check.editDistance > 0 && <small>Расхождение: {check.editDistance} символов</small>}
        {check.source === "teacher_text" && <small>Рассчитано по исправленному тексту</small>}
        <small>{check.disclaimer}</small>
      </div>
    </div>
  );
}
