import { KeyboardEvent, useState } from "react";
import { Executor, PlanBody, Task } from "../api";
import { AdviceSheet } from "./AdviceSheet";
import { Title } from "./Title";

// Порядок дня и исполнители: тап по задаче — следующий номер, повторный —
// снять номер (следующие сдвигаются). Строки на местах не прыгают: новый
// порядок список примет после сохранения. Значок справа — исполнитель по
// кругу 👤 → 🤖 → без отметки; у 🤖 можно написать рекомендации.

type Row = { executor: Executor; advice: string | null };
export type PlanEdit = { order: string[]; meta: Record<string, Row> };

const NEXT: Record<string, Executor> = { none: "self", self: "agent", agent: null };
const ICON: Record<string, string> = { none: "·", self: "👤", agent: "🤖" };
const WORD: Record<string, string> = { none: "не выбран", self: "сам", agent: "агент" };

export function executorLabel(executor: Executor | undefined): string {
  return executor ? `${ICON[executor]} ${WORD[executor]}` : "";
}

export function initialPlan(tasks: Task[]): PlanEdit {
  const numbered = tasks.filter((t) => t.order != null).sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  return {
    order: numbered.map((t) => t.key),
    meta: Object.fromEntries(tasks.map((t) => [t.key, { executor: t.executor ?? null, advice: t.advice ?? null }])),
  };
}

// Строка правки задачи; задачи, которой не было при входе, — как с сервера.
const rowOf = (edit: PlanEdit, t: Task): Row => edit.meta[t.key] ?? { executor: t.executor ?? null, advice: t.advice ?? null };

// Только изменённое; null — менять нечего. Ключи задач, которых уже нет в
// списке, не уходят: сервер отклонил бы весь запрос.
export function planChanges(tasks: Task[], edit: PlanEdit): PlanBody | null {
  const before = initialPlan(tasks);
  const keys = new Set(tasks.map((t) => t.key));
  const order = edit.order.filter((k) => keys.has(k));
  const body: PlanBody = {};
  if (before.order.join("\n") !== order.join("\n")) body.order = order;
  const meta: NonNullable<PlanBody["meta"]> = {};
  for (const t of tasks) {
    const a = before.meta[t.key];
    const b = rowOf(edit, t);
    // Рекомендации действуют только у агента: у другого исполнителя уходят null.
    const changed = a.executor !== b.executor || (b.executor === "agent" && (a.advice ?? "") !== (b.advice ?? ""));
    if (changed) meta[t.key] = { executor: b.executor, advice: b.executor === "agent" ? b.advice : null };
  }
  if (Object.keys(meta).length) body.meta = meta;
  return Object.keys(body).length ? body : null;
}

type Props = {
  tasks: (Task & { time?: string | null })[];
  edit: PlanEdit;
  onChange: (edit: PlanEdit) => void;
  disabled?: boolean;
};

export function DayPlanner({ tasks, edit, onChange, disabled = false }: Props) {
  const [advising, setAdvising] = useState<{ key: string; title: string; text: string } | null>(null);

  const toggle = (key: string) =>
    onChange({ ...edit, order: edit.order.includes(key) ? edit.order.filter((k) => k !== key) : [...edit.order, key] });

  const cycle = (key: string) => {
    const row = rowOf(edit, tasks.find((t) => t.key === key)!);
    // Текст рекомендаций держим до сохранения: случайный тап по значку его не
    // стирает, круг обратно к 🤖 возвращает. Сервер снимет его, только если
    // при сохранении исполнитель не агент (planChanges).
    onChange({ ...edit, meta: { ...edit.meta, [key]: { ...row, executor: NEXT[row.executor ?? "none"] } } });
  };

  const saveAdvice = (advice: string | null) => {
    if (!advising) return;
    const task = tasks.find((t) => t.key === advising.key)!;
    onChange({ ...edit, meta: { ...edit.meta, [advising.key]: { ...rowOf(edit, task), advice } } });
    setAdvising(null);
  };

  return (
    <>
      <ul className="plan-list day-plan">
        {tasks.map((t) => {
          const n = edit.order.indexOf(t.key) + 1;
          const row = rowOf(edit, t);
          const who = row.executor ?? "none";
          return (
            <li key={t.key}>
              {/* Не <button>: внутри бывают ссылки, Title открывает их сам и номер не ставит. */}
              <div
                className={n ? "pick on" : "pick"}
                role="button"
                tabIndex={disabled ? -1 : 0}
                aria-pressed={n > 0}
                aria-disabled={disabled}
                aria-label={`Порядок: ${n ? `${n}, ` : ""}${t.title}`}
                onClick={() => !disabled && toggle(t.key)}
                onKeyDown={(e: KeyboardEvent) => {
                  if (disabled || e.target !== e.currentTarget || (e.key !== "Enter" && e.key !== " ")) return;
                  e.preventDefault();
                  toggle(t.key);
                }}
              >
                <span className="num" data-testid="num">
                  {n || ""}
                </span>
                <span className="pick-title">
                  {t.time && <span className="time">{t.time}</span>} <Title text={t.title} />
                </span>
              </div>
              <button
                className={`who ${who}`}
                aria-label={`Исполнитель (${WORD[who]}) ${t.title}`}
                disabled={disabled}
                onClick={() => cycle(t.key)}
              >
                {ICON[who]}
              </button>
              {row.executor === "agent" && (
                <div className="advice">
                  {row.advice && <span className="advice-text">{row.advice}</span>}
                  <button
                    className="ghost-text"
                    aria-label={`Рекомендации: ${t.title}`}
                    disabled={disabled}
                    onClick={() => setAdvising({ key: t.key, title: t.title, text: row.advice ?? "" })}
                  >
                    ✎ {row.advice ? "Изменить" : "Рекомендации"}
                  </button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {edit.order.length > 0 && (
        <button className="ghost-text" disabled={disabled} onClick={() => onChange({ ...edit, order: [] })}>
          Сбросить
        </button>
      )}
      {advising && (
        <AdviceSheet title={advising.title} initial={advising.text} onSave={saveAdvice} onCancel={() => setAdvising(null)} />
      )}
    </>
  );
}
