import { useEffect, useRef, useState } from "react";
import { Morning, Plan, RitualTask, Times, deleteTask, getMorning, getPlan, moveKeys, ritualDone, savePlan, scheduleTask } from "../api";
import { DayPlanner, PlanEdit, initialPlan, planChanges } from "../components/DayPlanner";
import { ErrorView, errorText } from "../components/Status";
import { TaskCard } from "../components/TaskCard";
import { hasTime, reportWarnings, useTimeAsk, warningText } from "../components/TimeSheet";
import { badges, dayLabel, localToday, shortDate } from "../format";
import { shortTitle } from "../hooks";
import { plural } from "../plural";
import { alertUser, confirm, haptic } from "../telegram";

// Утренний разбор: Просрочено → Застряло → Входящие → План дня. Пустые шаги
// пропускаются. Застрявшие и входящие — по одной карточке, просроченные —
// списком: их мало, а решение обычно одно — «остальное на сегодня».

type Step = "overdue" | "stuck" | "inbox" | "plan";
const TITLES: Record<Step, string> = { overdue: "Просрочено", stuck: "Застряло", inbox: "Входящие", plan: "План дня" };

function addDays(iso: string, days: number): string {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + days);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function RitualMorning({ onClose }: { onClose: () => void }) {
  const [state, setState] = useState<{ kind: "loading" } | { kind: "error"; message: string; expired: boolean } | { kind: "ready"; data: Morning }>({ kind: "loading" });
  const [steps, setSteps] = useState<Step[]>([]);
  const [index, setIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const started = useRef(Date.now());
  const { ask, sheet } = useTimeAsk();
  const today = localToday();

  const load = async () => {
    setState({ kind: "loading" });
    try {
      const data = await getMorning();
      setSteps([...(["overdue", "stuck", "inbox"] as const).filter((s) => data[s].length > 0), "plan"]);
      setIndex(0);
      setState({ kind: "ready", data });
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
  const step = steps[index];
  const nextStep = () => setIndex((i) => Math.min(i + 1, steps.length - 1));
  const drop = (s: "overdue" | "stuck" | "inbox", key: string) =>
    setState({ kind: "ready", data: { ...data, [s]: data[s].filter((t) => t.key !== key) } });

  // Действие над задачей: успех — убрать её из шага, ошибка — оставить и сказать.
  const act = async (s: "overdue" | "stuck" | "inbox", task: RitualTask, call: () => Promise<unknown>): Promise<boolean> => {
    if (busy) return false;
    setBusy(true);
    try {
      const r = (await call()) as { warnings?: ("calendar" | "reminder")[] } | undefined;
      reportWarnings(r?.warnings);
      haptic("success");
      drop(s, task.key);
      return true;
    } catch (e) {
      haptic("error");
      alertUser((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  // Перенос со временем: задача с 🔔/🗓 — сначала окно времени.
  const schedule = async (s: "overdue" | "stuck" | "inbox", task: RitualTask, date: string, label: string) => {
    let time: string | null | undefined;
    if (hasTime(task)) {
      const times = await ask([task], label);
      if (!times) return false;
      time = times[task.key];
    }
    return act(s, task, () => (time === undefined ? scheduleTask(task.key, date) : scheduleTask(task.key, date, time)));
  };

  const remove = async (s: "overdue" | "stuck" | "inbox", task: RitualTask) => {
    if (!(await confirm(`Удалить «${shortTitle(task.title)}»?`))) return false;
    return act(s, task, () => deleteTask(task.key));
  };

  const restToday = async () => {
    const rest = data.overdue;
    if (!rest.length) return nextStep();
    const timed = rest.filter(hasTime);
    let times: Times | undefined;
    if (timed.length) {
      const answer = await ask(timed, "Сегодня");
      if (!answer) return;
      times = answer;
    }
    setBusy(true);
    try {
      const keys = rest.map((t) => t.key);
      const r = times ? await moveKeys(keys, "today", times) : await moveKeys(keys, "today");
      const failed = r.failed.length ? `Перенесено ${r.moved} из ${r.total}. Не удалось: ${r.failed.join(", ")}` : "";
      const text = [failed, warningText(r.warnings)].filter(Boolean).join("\n\n");
      if (text) alertUser(text);
      haptic(r.failed.length ? "error" : "success");
      // Неперенесённые остаются в шаге: их можно перенести или удалить по одной.
      const notMoved = new Set(r.failed);
      const kept = rest.filter((t) => notMoved.has(t.title));
      setState({ kind: "ready", data: { ...data, overdue: kept } });
      if (!kept.length) nextStep();
    } catch (e) {
      haptic("error");
      alertUser((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  // Карточки по одной: шаг закончился — следующий.
  const oneByOne = (s: "stuck" | "inbox") => {
    const task = data[s][0];
    if (!task) {
      // Обычно сюда не попадаем: после последней карточки шаг сменяется сам.
      return (
        <button className="wide primary" onClick={nextStep}>
          Дальше
        </button>
      );
    }
    // Последняя карточка шага — после действия сразу следующий шаг.
    const last = data[s].length === 1;
    const then = (p: Promise<boolean>) => void p.then((ok) => ok && last && nextStep());
    const skip = { label: "Пропустить", quiet: true, onClick: () => (last ? nextStep() : drop(s, task.key)) };
    const actions =
      s === "stuck"
        ? [
            { label: "Сегодня", main: true, onClick: () => then(schedule(s, task, "today", "Сегодня")) },
            { label: "Через 2 недели", onClick: () => then(schedule(s, task, addDays(today, 14), dayLabel(addDays(today, 14), today))) },
            { label: "Удалить", danger: true, onClick: () => then(remove(s, task)) },
            skip,
          ]
        : [
            { label: "Сегодня", main: true, onClick: () => then(schedule(s, task, "today", "Сегодня")) },
            { label: "Удалить", danger: true, onClick: () => then(remove(s, task)) },
            skip,
          ];
    return (
      <>
        <p className="hint">
          {s === "stuck" ? "Висят давно — реши, что с ними делать." : "Пришли без даты — назначь день или удали."} Осталось {data[s].length}.
        </p>
        <ul>
          <TaskCard
            key={task.key}
            big
            title={task.title}
            note={task.note}
            meta={[task.created ? `создана ${shortDate(task.created)}` : "", badges(task)].filter(Boolean).join(" · ")}
            disabled={busy}
            actions={actions}
            onPickDate={(date) => then(schedule(s, task, date, dayLabel(date, today)))}
          />
        </ul>
      </>
    );
  };

  const finish = async () => {
    try {
      await ritualDone("morning");
    } catch {
      // Отметка нужна, чтобы «Позже» не напомнило; без неё разбор всё равно сделан.
    }
    haptic("success");
    onClose();
  };

  return (
    <div className="ritual">
      <header>
        <h1>Утренний разбор</h1>
        <button className="ghost-text" onClick={onClose}>
          Закрыть
        </button>
      </header>
      <div className="progress" aria-hidden="true">
        {steps.map((s, i) => (
          <span key={s} className={i <= index ? "on" : undefined} />
        ))}
      </div>
      <p className="step-count">
        Шаг {index + 1} из {steps.length}
      </p>
      <h2 className="step-title">{TITLES[step]}</h2>

      {step === "overdue" && (
        <>
          <ul>
            {data.overdue.map((task) => (
              <TaskCard
                key={task.key}
                title={task.title}
                note={task.note}
                meta={[task.date ? dayLabel(task.date, today) : "", badges(task)].filter(Boolean).join(" · ")}
                disabled={busy}
                open
                actions={[
                  { label: "Сегодня", main: true, onClick: () => void schedule("overdue", task, "today", "Сегодня") },
                  { label: "Удалить", danger: true, onClick: () => void remove("overdue", task) },
                ]}
                onPickDate={(date) => void schedule("overdue", task, date, dayLabel(date, today))}
              />
            ))}
          </ul>
          <button className="wide primary" disabled={busy} onClick={() => void restToday()}>
            {data.overdue.length ? `Остальное на сегодня · ${data.overdue.length}` : "Дальше"}
          </button>
        </>
      )}
      {(step === "stuck" || step === "inbox") && oneByOne(step)}
      {step === "plan" && <PlanStep startedAt={started.current} onFinish={() => void finish()} />}
      {sheet}
    </div>
  );
}

function PlanStep({ startedAt, onFinish }: { startedAt: number; onFinish: () => void }) {
  const [state, setState] = useState<{ kind: "loading" } | { kind: "error"; message: string; expired: boolean } | { kind: "ready"; plan: Plan }>({ kind: "loading" });
  const [elapsed] = useState(() => Math.round((Date.now() - startedAt) / 1000));
  const [edit, setEdit] = useState<PlanEdit>({ order: [], meta: {} });
  const [saving, setSaving] = useState(false);

  const load = async () => {
    setState({ kind: "loading" });
    try {
      const plan = await getPlan();
      setEdit(initialPlan(plan.today));
      setState({ kind: "ready", plan });
    } catch (e) {
      setState({ kind: "error", ...errorText(e) });
    }
  };

  useEffect(() => {
    void load();
  }, []);

  if (state.kind === "loading") return <div className="center small">Собираю план…</div>;
  if (state.kind === "error") return <ErrorView {...state} onRetry={() => void load()} />;

  const { events, today, norm } = state.plan;

  // Порядок и исполнители — одним запросом; не сохранилось — остаёмся в плане.
  const start = async () => {
    const body = planChanges(today, edit);
    if (body) {
      setSaving(true);
      try {
        const r = await savePlan(body);
        if (r.failed.length) alertUser(`Не сохранилось для: ${r.failed.join(", ")}`);
      } catch (e) {
        haptic("error");
        alertUser((e as Error).message);
        setSaving(false);
        return;
      }
    }
    onFinish();
  };
  const n = today.length;
  const m = events?.length ?? 0;
  const parts = [`${n} ${plural(n, "задача", "задачи", "задач")}`];
  if (m) parts.push(`${m} ${plural(m, "встреча", "встречи", "встреч")}`);
  const verdict = norm ? ` Обычно закрываешь ${norm} в день — ${n > norm * 1.5 ? "многовато, что-то можно перенести" : "реально"}.` : "";

  return (
    <>
      <p className="hint">
        Сегодня {parts.join(" и ")}.{verdict}
        {elapsed >= 5 && ` Разобрано за ${elapsed >= 60 ? `${Math.floor(elapsed / 60)} мин ` : ""}${elapsed % 60} с.`}
      </p>
      {events === null && <p className="hint">Календарь недоступен.</p>}
      {events && events.length > 0 && (
        <section className="card">
          <h2>Встречи</h2>
          <ul className="plan-list">
            {events.map((e, i) => (
              <li key={i}>
                <span className="time">{e.time ?? "весь день"}</span> {e.title}
              </li>
            ))}
          </ul>
        </section>
      )}
      <section className="card">
        <h2>Задачи</h2>
        {n === 0 ? (
          <p className="hint">На сегодня задач нет.</p>
        ) : (
          <>
            <p className="hint">Тапай задачи в том порядке, в каком будешь делать. Справа — кто делает.</p>
            <DayPlanner tasks={today} edit={edit} onChange={setEdit} disabled={saving} />
          </>
        )}
      </section>
      <button className="wide primary" disabled={saving} onClick={() => void start()}>
        Начать день
      </button>
    </>
  );
}
