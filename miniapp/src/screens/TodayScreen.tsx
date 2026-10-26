import { useCallback, useEffect, useState } from "react";
import { Task, Times, completeTask, deleteTask, getToday, moveTasks, scheduleTask } from "../api";
import { TaskCard } from "../components/TaskCard";
import { hasTime, reportWarnings, useTimeAsk, warningText } from "../components/TimeSheet";
import { ErrorView, errorText } from "../components/Status";
import { badges, dayLabel, localToday, longDate } from "../format";
import { plural } from "../plural";
import { shortTitle, useCardActions } from "../hooks";
import { alertUser, confirm, haptic } from "../telegram";

type Data = { overdue: Task[]; today: Task[] };
type Load = { kind: "loading" } | { kind: "ready"; data: Data } | { kind: "error"; message: string; expired: boolean };

type Props = {
  onChanged?: () => void;
  inbox?: number;
  onOpenInbox?: () => void;
  onRitual?: (kind: "morning" | "evening") => void;
};

export function TodayScreen({ onChanged, inbox = 0, onOpenInbox, onRitual }: Props) {
  const [state, setState] = useState<Load>({ kind: "loading" });
  const [moving, setMoving] = useState(false);
  const { run, isBusy } = useCardActions(onChanged);
  const { ask, sheet } = useTimeAsk();

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setState({ kind: "loading" });
    try {
      setState({ kind: "ready", data: await getToday() });
    } catch (e) {
      setState({ kind: "error", ...errorText(e) });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (state.kind === "loading") return <div className="center">Загружаю…</div>;
  if (state.kind === "error") return <ErrorView {...state} onRetry={() => void load()} />;

  const data = state.data;
  const act = (task: Task, call: () => Promise<unknown>) =>
    run(
      task.key,
      () => {
        setState({
          kind: "ready",
          data: { overdue: data.overdue.filter((t) => t.key !== task.key), today: data.today.filter((t) => t.key !== task.key) },
        });
        return () => setState({ kind: "ready", data });
      },
      call,
    );

  const remove = async (task: Task) => {
    if (isBusy(task.key)) return;
    if (!(await confirm(`Удалить «${shortTitle(task.title)}»?`))) return;
    await act(task, () => deleteTask(task.key));
  };

  // Задача со временем — сначала спрашиваем время на новый день.
  const schedule = async (task: Task, date: string, label: string) => {
    if (isBusy(task.key)) return;
    let time: string | null | undefined;
    if (hasTime(task)) {
      const times = await ask([task], label);
      if (!times) return;
      time = times[task.key];
    }
    await act(task, async () => {
      const r = time === undefined ? await scheduleTask(task.key, date) : await scheduleTask(task.key, date, time);
      reportWarnings(r?.warnings);
    });
  };

  const moveAll = async (scope: "overdue" | "today", to: "today" | "tomorrow", tasks: Task[]) => {
    if (moving) return;
    // Окно времени само служит подтверждением; без задач со временем — вопрос.
    const timed = tasks.filter(hasTime);
    let times: Times | undefined;
    if (timed.length) {
      const answer = await ask(timed, to === "today" ? "Сегодня" : "Завтра");
      if (!answer) return;
      times = answer;
    } else {
      const question = scope === "overdue" ? `Перенести просроченные (${tasks.length}) на сегодня?` : `Перенести сегодняшние (${tasks.length}) на завтра?`;
      if (!(await confirm(question))) return;
    }
    setMoving(true);
    try {
      const r = times ? await moveTasks(scope, to, times) : await moveTasks(scope, to);
      haptic(r.failed.length ? "error" : "success");
      // Одно окно: второй showAlert, пока открыт первый, Telegram отклоняет.
      const failed = r.failed.length ? `Перенесено ${r.moved} из ${r.total}. Не удалось: ${r.failed.join(", ")}` : "";
      const text = [failed, warningText(r.warnings)].filter(Boolean).join("\n\n");
      if (text) alertUser(text);
      onChanged?.();
      await load(true);
    } catch (e) {
      haptic("error");
      alertUser((e as Error).message);
    } finally {
      setMoving(false);
    }
  };

  const today = localToday();
  const total = data.overdue.length + data.today.length;
  const card = (task: Task, overdue: boolean) => (
    <TaskCard
      key={task.key}
      title={task.title}
      note={task.note}
      meta={[overdue && task.date ? dayLabel(task.date, today) : "", badges(task)].filter(Boolean).join(" · ")}
      disabled={isBusy(task.key)}
      onDone={() => void act(task, () => completeTask(task.key))}
      actions={[
        overdue
          ? { label: "Сегодня", main: true, onClick: () => void schedule(task, "today", "Сегодня") }
          : { label: "Завтра", main: true, onClick: () => void schedule(task, "tomorrow", "Завтра") },
        { label: "Удалить", danger: true, onClick: () => void remove(task) },
      ]}
      onPickDate={(date) => void schedule(task, date, dayLabel(date, today))}
    />
  );

  return (
    <>
      <header>
        <h1>
          <span className="star">★</span>Сегодня
        </h1>
        <button className="ghost" onClick={() => void load()} aria-label="Обновить">↻</button>
      </header>
      <p className="sub">
        {longDate(today)}
        {total > 0 && ` · ${total} ${plural(total, "задача", "задачи", "задач")}`}
      </p>

      {onRitual && (
        <div className="pills">
          <button onClick={() => onRitual("morning")}>☀️ Утренний разбор</button>
          <button onClick={() => onRitual("evening")}>🌙 Закрыть день</button>
        </div>
      )}
      {inbox > 0 && onOpenInbox && (
        <button className="inbox-line" onClick={onOpenInbox}>
          <span>📥 {inbox} во входящих</span>
          <span>Разобрать ›</span>
        </button>
      )}

      {data.overdue.length > 0 && (
        <section>
          <div className="group danger">
            <h2>Просрочено · {data.overdue.length}</h2>
            <button className="ghost-text" disabled={moving} onClick={() => void moveAll("overdue", "today", data.overdue)}>
              Все на сегодня
            </button>
          </div>
          <ul>{data.overdue.map((t) => card(t, true))}</ul>
        </section>
      )}

      <section>
        {data.today.length > 0 && (
          <div className="group">
            <h2>{data.overdue.length > 0 ? "На сегодня" : "Задачи"} · {data.today.length}</h2>
          </div>
        )}
        {data.today.length === 0 ? (
          <div className="empty">
            <div className="big">★</div>
            На сегодня ничего 🎉
          </div>
        ) : (
          <>
            <ul>{data.today.map((t) => card(t, false))}</ul>
            <button className="ghost-text wide" disabled={moving} onClick={() => void moveAll("today", "tomorrow", data.today)}>
              Перенести все на завтра
            </button>
          </>
        )}
      </section>
      {sheet}
    </>
  );
}
