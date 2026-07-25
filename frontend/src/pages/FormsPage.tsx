import { useState } from "react";
import { api } from "../api/client";
import type { ClassGroup, Task } from "../api/types";
import { useApi } from "../lib";

export default function FormsPage() {
  const classes = useApi<ClassGroup[]>(() => api.get("/classes"), []);
  const tasks = useApi<Task[]>(() => api.get("/tasks"), []);

  const [classId, setClassId] = useState<number | "">("");
  const [taskId, setTaskId] = useState<number | "">("");
  const [sheetsPerStudent, setSheetsPerStudent] = useState(1);
  const [formsPerPage, setFormsPerPage] = useState(3);
  const [cutLines, setCutLines] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  async function generate() {
    if (!classId || !taskId) {
      setError("Выберите класс и задание.");
      return;
    }
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const response = await fetch("/api/forms/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          class_id: classId,
          task_id: taskId,
          sheets_per_student: sheetsPerStudent,
          forms_per_page: formsPerPage,
          include_cut_lines: cutLines,
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.detail ?? response.statusText);
      }
      const count = response.headers.get("X-Form-Count") ?? "?";
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const disposition = response.headers.get("Content-Disposition") ?? "";
      const match = /filename="?([^";]+)"?/.exec(disposition);
      a.download = match ? match[1] : "forms.pdf";
      a.click();
      URL.revokeObjectURL(url);
      setDone(`PDF сформирован: ${count} бланков. Распечатайте на A4 без масштабирования (100%).`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1 className="page-title">Генерация бланков</h1>
      {error && <div className="error-box">{error}</div>}
      {done && <div className="ok-box">{done}</div>}

      <div className="panel" style={{ maxWidth: 640 }}>
        <label className="field">
          <span>Класс</span>
          <select value={classId} onChange={(e) => setClassId(e.target.value ? Number(e.target.value) : "")}>
            <option value="">— выберите класс —</option>
            {(classes.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} ({c.student_count} уч.)
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span>Задание</span>
          <select value={taskId} onChange={(e) => setTaskId(e.target.value ? Number(e.target.value) : "")}>
            <option value="">— выберите задание —</option>
            {(tasks.data ?? []).map((t) => (
              <option key={t.id} value={t.id}>
                {t.external_id} — {t.title}
              </option>
            ))}
          </select>
        </label>

        <div className="row">
          <label className="field" style={{ flex: 1 }}>
            <span>Бланков на ученика</span>
            <input type="number" min={1} max={20} value={sheetsPerStudent} onChange={(e) => setSheetsPerStudent(Number(e.target.value) || 1)} />
          </label>
          <label className="field" style={{ flex: 1 }}>
            <span>Бланков на страницу A4</span>
            <input type="number" min={1} max={6} value={formsPerPage} onChange={(e) => setFormsPerPage(Number(e.target.value) || 3)} />
          </label>
        </div>

        <label className="row" style={{ marginBottom: 14, cursor: "pointer" }}>
          <input type="checkbox" checked={cutLines} onChange={(e) => setCutLines(e.target.checked)} />
          Линии отреза между бланками
        </label>

        <button className="btn primary" disabled={busy} onClick={generate}>
          {busy ? "Генерация…" : "Сформировать PDF"}
        </button>
      </div>

      <div className="panel mt" style={{ maxWidth: 640 }}>
        <h3 style={{ marginTop: 0 }}>Памятка</h3>
        <ul className="muted" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>
          <li>Каждый бланк содержит QR-код с ID ученика и задания — по нему лист опознаётся автоматически.</li>
          <li>Печатайте в масштабе 100%: изменение размера ухудшает чтение QR-кода.</li>
          <li>Просите учеников не заклеивать и не закрашивать зону QR-кода.</li>
        </ul>
      </div>
    </>
  );
}
