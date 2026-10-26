import { useEffect, useState } from "react";
import { Evening, RitualTask, Settings, Times, completeTask, getEvening, getStats, moveKeys, ritualDone } from "../api";
import { ErrorView, errorText } from "../components/Status";
import { Title } from "../components/Title";
import { hasTime, useTimeAsk, warningText } from "../components/TimeSheet";
import { badges, dayLabel, localToday } from "../format";
import { plural } from "../plural";
import { alertUser, haptic } from "../telegram";

// Вечернее закрытие: «Что успел?» → «Остальное — на завтра?» → «День закрыт».
// Отмеченное закрывается пачкой по «Дальше», а не по тапу: можно передумать.

type Step = "done" | "move" | "summary";

function addDays(iso: string, days: number): string {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + days);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

type Result = { done: number; moved: number; weekDone: number | null };

export function RitualEvening({ settings, onClose }: { settings: Settings | null; onClose: () => void }) {
  const [state, setState] = useState<{ kind: "loading" } | { kind: "error"; message: string; expired: boolean } | { kind: "ready"; data: Evening }>({ kind: "loading" });
  const [step, setStep] = useState<Step>("done");
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [days, setDays] = useState<Record<string, string>>({});
  const [left, setLeft] = useState<RitualTask[]>([]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result>({ done: 0, moved: 0, weekDone: null });
  const { ask, sheet } = useTimeAsk();
  const today = localToday();
  const tomorrow = addDays(today, 1);
  const choices = Array.from({ length: 7 }, (_, i) => addDays(today, i + 1));

  const summarize = async (done: number, moved: number) => {
    let weekDone: number | null = null;
    try {
      weekDone = (await getStats()).week.reduce((n, d) => n + d.completed, 0);
    } catch {
      // Итог недели — украшение; без него экран всё равно нужен.
    }
    setResult({ done, moved, weekDone });
    setStep("summary");
  };

  const load = async () => {
    setState({ kind: "loading" });
    try {
      const data = await getEvening();
      setState({ kind: "ready", data });
      if (!data.today_open.length) await summarize(data.done_today, 0);
    } catch (e) {
      setState({ kind: "error", ...errorText(e) });
    }
  };

  useEffect(() => {
    void load();
  }, []);

  if (state.kind === "loading") return <div className="center">Загружаю…</div>;
  if (state.kind === "error") return <ErrorView {...state} onRetry={() => void load()} />;
  const data = state.data;

  const toggle = (key: string) =>
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  // Шаг 1: закрываем отмеченное по одной; не закрылось — остаётся в списке.
  const completeChecked = async () => {
    setBusy(true);
    const failed = new Set<string>();
    let done = 0;
    for (const task of data.today_open.filter((t) => checked.has(t.key))) {
      try {
        await completeTask(task.key);
        done += 1;
      } catch {
        failed.add(task.key);
      }
    }
    setBusy(false);
    const failedTitles = data.today_open.filter((t) => failed.has(t.key)).map((t) => t.title);
    if (failedTitles.length) alertUser(`Не удалось закрыть: ${failedTitles.join(", ")}`);
    haptic(failed.size ? "error" : "success");
    const rest = data.today_open.filter((t) => !checked.has(t.key) || failed.has(t.key));
    setState({ kind: "ready", data: { ...data, done_today: data.done_today + done } });
    setLeft(rest);
    if (rest.length) setStep("move");
    else await summarize(data.done_today + done, 0);
  };

  // Шаг 2: переносим группами по дню; у задач с 🔔/🗓 — окно времени.
  const closeDay = async () => {
    const groups = new Map<string, RitualTask[]>();
    for (const task of left) {
      const day = days[task.key] ?? tomorrow;
      groups.set(day, [...(groups.get(day) ?? []), task]);
    }
    const plans: { day: string; tasks: RitualTask[]; times?: Times }[] = [];
    for (const day of [...groups.keys()].sort()) {
      const tasks = groups.get(day)!;
      const timed = tasks.filter(hasTime);
      let times: Times | undefined;
      if (timed.length) {
        const answer = await ask(timed, dayLabel(day, today));
        if (!answer) return;
        times = answer;
      }
      plans.push({ day, tasks, times });
    }
    setBusy(true);
    let moved = 0;
    const messages: string[] = [];
    for (const { day, tasks, times } of plans) {
      const keys = tasks.map((t) => t.key);
      try {
        const r = times ? await moveKeys(keys, day, times) : await moveKeys(keys, day);
        moved += r.moved;
        if (r.failed.length) messages.push(`Не удалось перенести: ${r.failed.join(", ")}`);
        const w = warningText(r.warnings);
        if (w) messages.push(w);
      } catch (e) {
        messages.push((e as Error).message);
      }
    }
    setBusy(false);
    if (messages.length) alertUser(messages.join("\n\n"));
    haptic(messages.length ? "error" : "success");
    await summarize(data.done_today, moved);
  };

  const finish = async () => {
    try {
      await ritualDone("evening");
    } catch {
      // Отметка лишь гасит повтор сводки; день всё равно закрыт.
    }
    onClose();
  };

  const total = data.done_today + data.today_open.length;
  const stepNo = step === "done" ? 1 : 2;

  return (
    <div className="ritual">
      <header>
        <h1>Закрытие дня</h1>
        <button className="ghost-text" onClick={onClose}>
          Закрыть
        </button>
      </header>

      {step !== "summary" && (
        <>
          <div className="progress" aria-hidden="true">
            <span className="on" />
            <span className={stepNo === 2 ? "on" : undefined} />
          </div>
          <p className="step-count">Шаг {stepNo} из 2</p>
        </>
      )}

      {step === "done" && (
        <>
          <h2 className="step-title">Что успел?</h2>
          <p className="hint">Сделано {data.done_today} из {total}</p>
          <p className="hint">Отметь сделанное — даже если забыл отметить днём.</p>
          <ul className="check-list">
            {data.today_open.map((task) => (
              <li key={task.key} className="card">
                <label>
                  <input type="checkbox" aria-label={task.title} checked={checked.has(task.key)} onChange={() => toggle(task.key)} />
                  <span>
                    <Title text={task.title} /> {badges(task) && <small>{badges(task)}</small>}
                  </span>
                </label>
              </li>
            ))}
          </ul>
          <button className="wide primary" disabled={busy} onClick={() => void completeChecked()}>
            Дальше
          </button>
        </>
      )}

      {step === "move" && (
        <>
          <h2 className="step-title">Остальное — на завтра?</h2>
          <p className="hint">Можно поменять день у любой.</p>
          <ul>
            {left.map((task) => (
              <li key={task.key} className="card move-row">
                <span className="title">
                  <Title text={task.title} />
                </span>
                <select
                  aria-label={`День: ${task.title}`}
                  value={days[task.key] ?? tomorrow}
                  onChange={(e) => setDays((d) => ({ ...d, [task.key]: e.target.value }))}
                >
                  {choices.map((d) => (
                    <option key={d} value={d}>
                      {dayLabel(d, today)}
                    </option>
                  ))}
                </select>
              </li>
            ))}
          </ul>
          <section className="card">
            <h2>Завтра, {new Date(`${tomorrow}T12:00:00`).toLocaleDateString("ru-RU", { weekday: "long" })}</h2>
            <ul className="plan-list">
              {(data.tomorrow_events ?? []).map((e, i) => (
                <li key={`e${i}`}>
                  <span className="time">{e.time ?? "весь день"}</span> {e.title}
                </li>
              ))}
              {data.tomorrow.map((t) => (
                <li key={t.key}>
                  <Title text={t.title} />
                </li>
              ))}
              {!data.tomorrow.length && !data.tomorrow_events?.length && <li className="hint">Пока пусто</li>}
            </ul>
          </section>
          {data.inbox_count > 0 && <p className="hint">Во входящих {data.inbox_count} — разберём утром.</p>}
          <button className="wide primary" disabled={busy} onClick={() => void closeDay()}>
            Закрыть день
          </button>
        </>
      )}

      {step === "summary" && (
        <div className="summary">
          <div className="big">🌙</div>
          <h2 className="step-title">День закрыт</h2>
          <p>
            Сделано {result.done}
            {result.moved > 0 && ` · перенесено ${result.moved}`}
          </p>
          {result.weekDone !== null && (
            <p className="hint">
              За неделю {plural(result.weekDone, "закрыта", "закрыто", "закрыто")} {result.weekDone}{" "}
              {plural(result.weekDone, "задача", "задачи", "задач")}
            </p>
          )}
          {settings?.morning_enabled && <p className="hint">Утренний разбор в {settings.morning_time}</p>}
          <button className="wide primary" onClick={() => void finish()}>
            Готово
          </button>
        </div>
      )}
      {sheet}
    </div>
  );
}
