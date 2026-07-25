import { useState } from "react";
import { api } from "../api/client";
import type { ClassGroup, Student, Task } from "../api/types";
import { useApi } from "../lib";

export default function CatalogPage() {
  const classes = useApi<ClassGroup[]>(() => api.get("/classes"), []);
  const tasks = useApi<Task[]>(() => api.get("/tasks"), []);
  const [selectedClass, setSelectedClass] = useState<number | null>(null);
  const students = useApi<Student[]>(
    () => (selectedClass ? api.get(`/students?class_id=${selectedClass}`) : Promise.resolve([])),
    [selectedClass],
  );

  const [error, setError] = useState<string | null>(null);

  // --- create class -----------------------------------------------------
  const [className, setClassName] = useState("");
  const [classYear, setClassYear] = useState("");

  async function createClass() {
    if (!className.trim()) return;
    try {
      await api.post("/classes", { name: className.trim(), school_year: classYear.trim() });
      setClassName("");
      setClassYear("");
      classes.refresh();
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  // --- bulk students -----------------------------------------------------
  const [bulkText, setBulkText] = useState("");

  async function addStudents() {
    if (!selectedClass || !bulkText.trim()) return;
    const rows = bulkText
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean)
      .map((line) => {
        // "ID;Фамилия;Имя" or "ID Фамилия Имя"
        const parts = line.includes(";") ? line.split(";").map((p) => p.trim()) : line.split(/\s+/);
        return {
          external_id: parts[0] ?? "",
          last_name: parts[1] ?? "",
          first_name: parts.slice(2).join(" "),
          class_id: selectedClass,
        };
      })
      .filter((s) => s.external_id);
    if (!rows.length) return;
    try {
      await api.post("/students/bulk", { class_id: selectedClass, students: rows });
      setBulkText("");
      students.refresh();
      classes.refresh();
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  // --- create task ---------------------------------------------------------
  const [taskId, setTaskId] = useState("");
  const [taskTitle, setTaskTitle] = useState("");
  const [taskSubject, setTaskSubject] = useState("");
  const [taskAnswer, setTaskAnswer] = useState("");

  async function createTask() {
    if (!taskId.trim() || !taskTitle.trim()) return;
    try {
      await api.post("/tasks", {
        external_id: taskId.trim(),
        title: taskTitle.trim(),
        subject: taskSubject.trim(),
        expected_answer: taskAnswer.trim(),
      });
      setTaskId("");
      setTaskTitle("");
      setTaskSubject("");
      setTaskAnswer("");
      tasks.refresh();
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <>
      <h1 className="page-title">Классы, ученики и задания</h1>
      {error && <div className="error-box">{error}</div>}

      <div className="grid cols-2">
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>Классы</h3>
          <table className="data">
            <thead>
              <tr>
                <th>Название</th>
                <th>Учебный год</th>
                <th>Учеников</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(classes.data ?? []).map((c) => (
                <tr key={c.id} style={{ cursor: "pointer" }} onClick={() => setSelectedClass(c.id)}>
                  <td>{selectedClass === c.id ? <strong>{c.name}</strong> : c.name}</td>
                  <td>{c.school_year || "—"}</td>
                  <td>{c.student_count}</td>
                  <td>
                    <button
                      className="btn small danger"
                      onClick={async (e) => {
                        e.stopPropagation();
                        if (!confirm(`Удалить класс «${c.name}»?`)) return;
                        try {
                          await api.delete(`/classes/${c.id}`);
                          if (selectedClass === c.id) setSelectedClass(null);
                          classes.refresh();
                        } catch (err) {
                          setError((err as Error).message);
                        }
                      }}
                    >
                      ×
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="row mt">
            <input type="text" placeholder="Название (например, 7Б)" value={className} onChange={(e) => setClassName(e.target.value)} style={{ maxWidth: 180 }} />
            <input type="text" placeholder="2026/2027" value={classYear} onChange={(e) => setClassYear(e.target.value)} style={{ maxWidth: 130 }} />
            <button className="btn primary" onClick={createClass}>
              Добавить класс
            </button>
          </div>
        </div>

        <div className="panel">
          <h3 style={{ marginTop: 0 }}>
            Ученики {selectedClass ? `— ${classes.data?.find((c) => c.id === selectedClass)?.name ?? ""}` : ""}
          </h3>
          {!selectedClass && <p className="muted">Выберите класс слева, чтобы посмотреть или добавить учеников.</p>}
          {selectedClass && (
            <>
              <table className="data">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Фамилия Имя</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {(students.data ?? []).map((s) => (
                    <tr key={s.id}>
                      <td>{s.external_id}</td>
                      <td>{s.display_name}</td>
                      <td>
                        <button
                          className="btn small danger"
                          onClick={async () => {
                            try {
                              await api.delete(`/students/${s.id}`);
                              students.refresh();
                              classes.refresh();
                            } catch (err) {
                              setError((err as Error).message);
                            }
                          }}
                        >
                          ×
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <label className="field mt">
                <span>Массовое добавление — по одному ученику в строке: «ID;Фамилия;Имя»</span>
                <textarea value={bulkText} onChange={(e) => setBulkText(e.target.value)} placeholder={"S-101;Иванов;Пётр\nS-102;Смирнова;Анна"} />
              </label>
              <button className="btn primary" onClick={addStudents}>
                Добавить учеников
              </button>
            </>
          )}
        </div>
      </div>

      <h3 className="section">Задания</h3>
      <div className="panel">
        <table className="data">
          <thead>
            <tr>
              <th>Код</th>
              <th>Название</th>
              <th>Предмет</th>
              <th>Ожидаемый ответ</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(tasks.data ?? []).map((t) => (
              <tr key={t.id}>
                <td>{t.external_id}</td>
                <td>{t.title}</td>
                <td>{t.subject || "—"}</td>
                <td className="muted">{t.expected_answer || "—"}</td>
                <td>
                  <button
                    className="btn small danger"
                    onClick={async () => {
                      if (!confirm(`Удалить задание «${t.title}»?`)) return;
                      try {
                        await api.delete(`/tasks/${t.id}`);
                        tasks.refresh();
                      } catch (err) {
                        setError((err as Error).message);
                      }
                    }}
                  >
                    ×
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="row mt">
          <input type="text" placeholder="Код (T-042)" value={taskId} onChange={(e) => setTaskId(e.target.value)} style={{ maxWidth: 130 }} />
          <input type="text" placeholder="Название задания" value={taskTitle} onChange={(e) => setTaskTitle(e.target.value)} style={{ maxWidth: 280 }} />
          <input type="text" placeholder="Предмет" value={taskSubject} onChange={(e) => setTaskSubject(e.target.value)} style={{ maxWidth: 160 }} />
          <input type="text" placeholder="Ожидаемый ответ (необязательно)" value={taskAnswer} onChange={(e) => setTaskAnswer(e.target.value)} style={{ maxWidth: 240 }} />
          <button className="btn primary" onClick={createTask}>
            Добавить задание
          </button>
        </div>
      </div>
    </>
  );
}
