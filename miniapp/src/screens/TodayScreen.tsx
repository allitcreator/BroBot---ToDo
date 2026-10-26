import { useCallback, useEffect, useState } from "react";
import { Task, completeTask, deleteTask, getToday, moveTasks, scheduleTask } from "../api";
import { TaskCard } from "../components/TaskCard";
import { ErrorView, errorText } from "../components/Status";
import { badges, dayLabel, localToday } from "../format";
import { shortTitle, useCardActions } from "../hooks";
import { alertUser, confirm, haptic } from "../telegram";

type Data = { overdue: Task[]; today: Task[] };
type Load = { kind: "loading" } | { kind: "ready"; data: Data } | { kind: "error"; message: string; expired: boolean };

export function TodayScreen({ onChanged }: { onChanged?: () => void }) {
  const [state, setState] = useState<Load>({ kind: "loading" });
  const [moving, setMoving] = useState(false);
  const { run, isBusy } = useCardActions(onChanged);

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

  const moveAll = async (scope: "overdue" | "today", to: "today" | "tomorrow", count: number) => {
    if (moving) return;
    const question = scope === "overdue" ? `Перенести просроченные (${count}) на сегодня?` : `Перенести сегодняшние (${count}) на завтра?`;
    if (!(await confirm(question))) return;
    setMoving(true);
    try {
      const r = await moveTasks(scope, to);
      haptic(r.failed.length ? "error" : "success");
      if (r.failed.length) alertUser(`Перенесено ${r.moved} из ${r.total}. Не удалось: ${r.failed.join(", ")}`);
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
  const card = (task: Task, overdue: boolean) => (
    <TaskCard
      key={task.key}
      title={task.title}
      note={task.note}
      meta={[overdue && task.date ? dayLabel(task.date, today) : "", badges(task)].filter(Boolean).join(" · ")}
      disabled={isBusy(task.key)}
      actions={[
        { label: "Готово", onClick: () => void act(task, () => completeTask(task.key)) },
        overdue
          ? { label: "Сегодня", onClick: () => void act(task, () => scheduleTask(task.key, "today")) }
          : { label: "Завтра", onClick: () => void act(task, () => scheduleTask(task.key, "tomorrow")) },
        { label: "Удалить", danger: true, onClick: () => void remove(task) },
      ]}
      onPickDate={(date) => void act(task, () => scheduleTask(task.key, date))}
    />
  );

  return (
    <>
      <header>
        <h1>Сегодня · {data.today.length}</h1>
        <button className="ghost" onClick={() => void load()} aria-label="Обновить">↻</button>
      </header>

      {data.overdue.length > 0 && (
        <section>
          <div className="section-head">
            <h2>Просрочено · {data.overdue.length}</h2>
            <button className="ghost-text" disabled={moving} onClick={() => void moveAll("overdue", "today", data.overdue.length)}>
              Все на сегодня
            </button>
          </div>
          <ul>{data.overdue.map((t) => card(t, true))}</ul>
        </section>
      )}

      <section>
        {data.overdue.length > 0 && (
          <div className="section-head">
            <h2>На сегодня · {data.today.length}</h2>
          </div>
        )}
        {data.today.length === 0 ? (
          <p className="center">На сегодня ничего 🎉</p>
        ) : (
          <>
            <ul>{data.today.map((t) => card(t, false))}</ul>
            <button className="ghost-text wide" disabled={moving} onClick={() => void moveAll("today", "tomorrow", data.today.length)}>
              Перенести все на завтра
            </button>
          </>
        )}
      </section>
    </>
  );
}
