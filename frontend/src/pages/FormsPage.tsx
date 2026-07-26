import { useMemo, useState } from "react";
import { api } from "../api/client";
import type { ClassGroup, Task } from "../api/types";
import { useApi } from "../lib";

type BlockType = "lines" | "choice" | "short" | "grid";
type LayoutKind = "lines" | "choice" | "short" | "grid" | "mixed";
type VariantMode = "rotate" | "all" | "fixed";

interface FormBlock {
  type: BlockType;
  title: string;
  rows: number;
  columns: number;
}

const BLOCK_LABEL: Record<BlockType, string> = {
  lines: "Развёрнутый ответ",
  choice: "Выбор ответа",
  short: "Краткий ответ",
  grid: "Сетка / таблица",
};

const PRESETS: Record<LayoutKind, FormBlock[]> = {
  lines: [{ type: "lines", title: "Ответ", rows: 8, columns: 4 }],
  choice: [{ type: "choice", title: "Тестовая часть", rows: 20, columns: 4 }],
  short: [{ type: "short", title: "Краткие ответы", rows: 12, columns: 8 }],
  grid: [{ type: "grid", title: "Сетка / таблица", rows: 14, columns: 8 }],
  mixed: [
    { type: "choice", title: "Часть A — выбор ответа", rows: 10, columns: 4 },
    { type: "short", title: "Часть B — краткий ответ", rows: 6, columns: 8 },
    { type: "lines", title: "Часть C — развёрнутый ответ", rows: 6, columns: 4 },
  ],
};

export default function FormsPage() {
  const classes = useApi<ClassGroup[]>(() => api.get("/classes"), []);
  const tasks = useApi<Task[]>(() => api.get("/tasks"), []);

  const [classId, setClassId] = useState<number | "">("");
  const [taskId, setTaskId] = useState<number | "">("");
  const [sheetsPerStudent, setSheetsPerStudent] = useState(1);
  const [formsPerPage, setFormsPerPage] = useState(1);
  const [cutLines, setCutLines] = useState(true);
  const [layoutKind, setLayoutKind] = useState<LayoutKind>("mixed");
  const [blocks, setBlocks] = useState<FormBlock[]>(PRESETS.mixed);
  const [variantCount, setVariantCount] = useState(2);
  const [variantMode, setVariantMode] = useState<VariantMode>("rotate");
  const [payloadFormat, setPayloadFormat] = useState<"json" | "compact">("json");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const selectedClass = useMemo(() => (classes.data ?? []).find((c) => c.id === classId), [classes.data, classId]);
  const selectedTask = useMemo(() => (tasks.data ?? []).find((t) => t.id === taskId), [tasks.data, taskId]);
  const estimatedForms = useMemo(() => {
    const students = selectedClass?.student_count ?? 0;
    const variantMultiplier = variantMode === "all" ? variantCount : 1;
    return students * sheetsPerStudent * variantMultiplier;
  }, [selectedClass?.student_count, sheetsPerStudent, variantCount, variantMode]);

  function applyPreset(kind: LayoutKind) {
    setLayoutKind(kind);
    setBlocks(PRESETS[kind].map((b) => ({ ...b })));
    setFormsPerPage(kind === "choice" ? 2 : 1);
  }

  function updateBlock(index: number, patch: Partial<FormBlock>) {
    setBlocks((old) => old.map((block, i) => (i === index ? { ...block, ...patch } : block)));
  }

  function addBlock(type: BlockType) {
    setLayoutKind("mixed");
    setBlocks((old) => [
      ...old,
      { type, title: BLOCK_LABEL[type], rows: type === "choice" ? 10 : 6, columns: type === "short" || type === "grid" ? 8 : 4 },
    ]);
  }

  function removeBlock(index: number) {
    setBlocks((old) => old.filter((_, i) => i !== index));
  }

  async function generate() {
    if (!classId || !taskId) {
      setError("Выберите класс и задание.");
      return;
    }
    if (!blocks.length) {
      setError("Добавьте хотя бы один блок ответа.");
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
          layout_kind: layoutKind,
          blocks,
          variant_count: variantCount,
          variant_mode: variantMode,
          payload_format: payloadFormat,
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
      setDone(`PDF сформирован: ${count} персональных бланков. Печатайте на A4 без масштабирования (100%).`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1 className="page-title">Конструктор бланков</h1>
      {error && <div className="error-box">{error}</div>}
      {done && <div className="ok-box">{done}</div>}

      <div className="forms-constructor">
        <div className="panel form-settings-panel">
          <h3 style={{ marginTop: 0 }}>1. Данные пакета</h3>
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
              <span>Листов на ученика</span>
              <input type="number" min={1} max={20} value={sheetsPerStudent} onChange={(e) => setSheetsPerStudent(Number(e.target.value) || 1)} />
            </label>
            <label className="field" style={{ flex: 1 }}>
              <span>Бланков на страницу A4</span>
              <input type="number" min={1} max={6} value={formsPerPage} onChange={(e) => setFormsPerPage(Number(e.target.value) || 1)} />
            </label>
          </div>

          <h3>2. Варианты и QR</h3>
          <div className="row">
            <label className="field" style={{ flex: 1 }}>
              <span>Количество вариантов</span>
              <input type="number" min={1} max={30} value={variantCount} onChange={(e) => setVariantCount(Number(e.target.value) || 1)} />
            </label>
            <label className="field" style={{ flex: 1 }}>
              <span>Распределение</span>
              <select value={variantMode} onChange={(e) => setVariantMode(e.target.value as VariantMode)}>
                <option value="rotate">по кругу между учениками</option>
                <option value="all">каждому все варианты</option>
                <option value="fixed">всем вариант 1</option>
              </select>
            </label>
          </div>
          <label className="field">
            <span>Формат QR</span>
            <select value={payloadFormat} onChange={(e) => setPayloadFormat(e.target.value as "json" | "compact")}>
              <option value="json">JSON: ученик + задание + вариант</option>
              <option value="compact">Компактный: меньше данных, вариант в sheetId</option>
            </select>
          </label>

          <h3>3. Тип бланка</h3>
          <div className="preset-grid">
            {(["lines", "choice", "short", "grid", "mixed"] as LayoutKind[]).map((kind) => (
              <button key={kind} className={`preset-card${layoutKind === kind ? " active" : ""}`} onClick={() => applyPreset(kind)}>
                {{ lines: "Развёрнутый", choice: "Тест", short: "Краткий", grid: "Сетка", mixed: "Комбинированный" }[kind]}
              </button>
            ))}
          </div>

          <h3>4. Блоки ответа</h3>
          <div className="form-block-editor">
            {blocks.map((block, index) => (
              <div className="form-block-row" key={`${block.type}-${index}`}>
                <select value={block.type} onChange={(e) => updateBlock(index, { type: e.target.value as BlockType })}>
                  {Object.entries(BLOCK_LABEL).map(([type, label]) => (
                    <option key={type} value={type}>{label}</option>
                  ))}
                </select>
                <input value={block.title} onChange={(e) => updateBlock(index, { title: e.target.value })} />
                <input title="Строки / вопросы" type="number" min={1} max={80} value={block.rows} onChange={(e) => updateBlock(index, { rows: Number(e.target.value) || 1 })} />
                <input title="Колонки / варианты / клетки" type="number" min={1} max={12} value={block.columns} onChange={(e) => updateBlock(index, { columns: Number(e.target.value) || 1 })} />
                <button className="btn small danger" onClick={() => removeBlock(index)}>×</button>
              </div>
            ))}
          </div>
          <div className="row mt">
            {(["choice", "short", "lines", "grid"] as BlockType[]).map((type) => (
              <button key={type} className="btn small" onClick={() => addBlock(type)}>+ {BLOCK_LABEL[type]}</button>
            ))}
          </div>

          <label className="row mt" style={{ marginBottom: 14, cursor: "pointer" }}>
            <input type="checkbox" checked={cutLines} onChange={(e) => setCutLines(e.target.checked)} />
            Линии отреза между бланками
          </label>

          <button className="btn primary" disabled={busy} onClick={generate}>
            {busy ? "Генерация…" : "Сформировать персональный PDF"}
          </button>
        </div>

        <div className="panel form-preview-panel">
          <h3 style={{ marginTop: 0 }}>Предпросмотр</h3>
          <div className="paper-preview">
            <div className="paper-header">
              <div className="fake-qr">QR</div>
              <div>
                <strong>{selectedClass?.name ?? "Класс"} • {selectedTask?.title ?? "Задание"}</strong>
                <span>ID ученика · Вариант {variantCount > 1 ? "1…" + variantCount : "1"}</span>
                <span className="muted">QR привяжет лист к ученику, заданию и варианту.</span>
              </div>
            </div>
            <div className="paper-body">
              {blocks.map((block, index) => (
                <PreviewBlock key={`${block.type}-${index}`} block={block} />
              ))}
            </div>
          </div>

          <div className="form-summary mt">
            <div><strong>{estimatedForms || "—"}</strong><span>бланков в пакете</span></div>
            <div><strong>{variantCount}</strong><span>вариант(ов)</span></div>
            <div><strong>{formsPerPage}</strong><span>на странице A4</span></div>
          </div>

          <div className="panel mt" style={{ background: "var(--bg-panel-2)" }}>
            <h3 style={{ marginTop: 0 }}>Что будет в QR</h3>
            <ul className="muted" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>
              <li>ID ученика и класс;</li>
              <li>ID задания;</li>
              <li>уникальный sheetId;</li>
              <li>номер варианта и номер листа.</li>
            </ul>
          </div>
        </div>
      </div>
    </>
  );
}

function PreviewBlock({ block }: { block: FormBlock }) {
  if (block.type === "choice") {
    return (
      <div className="preview-block choice">
        <strong>{block.title}</strong>
        {Array.from({ length: Math.min(block.rows, 12) }).map((_, row) => (
          <div className="choice-line" key={row}>
            <span>{row + 1}</span>
            {Array.from({ length: Math.min(block.columns, 6) }).map((__, col) => <i key={col} />)}
          </div>
        ))}
      </div>
    );
  }
  if (block.type === "short") {
    return (
      <div className="preview-block short">
        <strong>{block.title}</strong>
        {Array.from({ length: Math.min(block.rows, 8) }).map((_, row) => (
          <div className="short-line" key={row}>
            <span>{row + 1}</span>
            {Array.from({ length: Math.min(block.columns, 10) }).map((__, col) => <i key={col} />)}
          </div>
        ))}
      </div>
    );
  }
  if (block.type === "grid") {
    return (
      <div className="preview-block grid-block">
        <strong>{block.title}</strong>
        <div className="mini-grid" style={{ gridTemplateColumns: `repeat(${Math.min(block.columns, 10)}, 1fr)` }}>
          {Array.from({ length: Math.min(block.rows, 10) * Math.min(block.columns, 10) }).map((_, i) => <i key={i} />)}
        </div>
      </div>
    );
  }
  return (
    <div className="preview-block lines">
      <strong>{block.title}</strong>
      {Array.from({ length: Math.min(block.rows, 10) }).map((_, i) => <i key={i} />)}
    </div>
  );
}
