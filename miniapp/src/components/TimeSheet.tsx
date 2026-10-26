import { ReactNode, useEffect, useState } from "react";
import { MoveWarning, Task, Times, getTaskTime } from "../api";
import { alertUser } from "../telegram";

// Время задачи живёт в напоминаниях и событии календаря (значки 🔔/🗓), у
// самой задачи в To Do только дата. При переносе такой задачи спрашиваем
// время: завтра оно может быть другим. Задачи без значков не спрашиваем.
export const hasTime = (t: Task) => t.reminder || t.calendar;

type Row = { value: string; none: boolean; loading: boolean };

function TimeSheet({ tasks, label, onDone }: { tasks: Task[]; label: string; onDone: (times: Times | null) => void }) {
  const [rows, setRows] = useState<Record<string, Row>>(() =>
    Object.fromEntries(tasks.map((t) => [t.key, { value: "", none: false, loading: true }])),
  );
  const set = (key: string, patch: Partial<Row>) => setRows((r) => ({ ...r, [key]: { ...r[key], ...patch } }));

  useEffect(() => {
    for (const t of tasks) {
      // Не загрузилось — поле пустое: оставленное пустым значит «то же время».
      getTaskTime(t.key)
        .then((r) => set(t.key, { value: r.time ?? "", loading: false }))
        .catch(() => set(t.key, { loading: false }));
    }
    // Задачи фиксируются при открытии окна.
  }, []);

  const submit = () => {
    const times: Times = {};
    for (const t of tasks) {
      const row = rows[t.key];
      if (row.none) times[t.key] = null;
      else if (row.value) times[t.key] = row.value;
    }
    onDone(times);
  };

  return (
    <div className="sheet-backdrop">
      <div className="sheet" role="dialog" aria-label={`Время на ${label}`}>
        <h2>Время на {label.toLowerCase()}</h2>
        <ul>
          {tasks.map((t) => {
            const row = rows[t.key];
            return (
              <li key={t.key} className="time-row">
                <div className="title">{t.title}</div>
                <div className="picker">
                  <input
                    type="time"
                    aria-label={`Время: ${t.title}`}
                    value={row.value}
                    disabled={row.none || row.loading}
                    onChange={(e) => set(t.key, { value: e.target.value })}
                  />
                  <label>
                    <input
                      type="checkbox"
                      aria-label={`Без времени: ${t.title}`}
                      checked={row.none}
                      onChange={(e) => set(t.key, { none: e.target.checked })}
                    />{" "}
                    без времени
                  </label>
                </div>
              </li>
            );
          })}
        </ul>
        <div className="actions">
          <button onClick={submit}>Перенести</button>
          <button className="ghost-text" onClick={() => onDone(null)}>
            Отмена
          </button>
        </div>
      </div>
    </div>
  );
}

// ask(задачи, «Завтра») открывает окно и ждёт ответа: время по ключам или
// null при отмене. sheet нужно отрисовать в экране.
export function useTimeAsk() {
  const [req, setReq] = useState<{ id: number; tasks: Task[]; label: string; resolve: (t: Times | null) => void } | null>(null);

  // Своё id у каждого окна: два вопроса подряд не делят состояние полей.
  const ask = (tasks: Task[], label: string) =>
    new Promise<Times | null>((resolve) => setReq({ id: Date.now() + Math.random(), tasks, label, resolve }));

  const sheet: ReactNode = req && (
    <TimeSheet
      key={req.id}
      tasks={req.tasks}
      label={req.label}
      onDone={(times) => {
        setReq(null);
        req.resolve(times);
      }}
    />
  );
  return { ask, sheet };
}

const WARNING_TEXT: Record<MoveWarning, string> = {
  calendar: "событие в календаре",
  reminder: "напоминание",
};

export function warningText(warnings?: MoveWarning[]): string {
  if (!warnings?.length) return "";
  return `Не сдвинулось вместе с задачей: ${warnings.map((w) => WARNING_TEXT[w]).join(", ")}. Проверь вручную.`;
}

export function reportWarnings(warnings?: MoveWarning[]): void {
  const text = warningText(warnings);
  if (text) alertUser(text);
}
