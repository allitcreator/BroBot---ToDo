import { useCallback, useEffect, useState } from "react";
import { Task, completeTask, deleteTask, getWeek, scheduleTask } from "../api";
import { TaskCard } from "../components/TaskCard";
import { ErrorView, errorText } from "../components/Status";
import { badges, dayLabel, localToday } from "../format";
import { shortTitle, useCardActions } from "../hooks";
import { confirm } from "../telegram";

type Day = { date: string; items: Task[] };
type Load = { kind: "loading" } | { kind: "ready"; days: Day[] } | { kind: "error"; message: string; expired: boolean };

export function WeekScreen({ onChanged }: { onChanged?: () => void }) {
  const [state, setState] = useState<Load>({ kind: "loading" });
  const { run, isBusy } = useCardActions(onChanged);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      setState({ kind: "ready", days: (await getWeek()).days });
    } catch (e) {
      setState({ kind: "error", ...errorText(e) });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (state.kind === "loading") return <div className="center">Загружаю…</div>;
  if (state.kind === "error") return <ErrorView {...state} onRetry={() => void load()} />;

  const days = state.days;
  // Перенос внутри недели перекладывает карточку в нужный день, а не убирает её.
  const act = (task: Task, call: () => Promise<unknown>, moveTo?: string) =>
    run(
      task.key,
      () => {
        setState({
          kind: "ready",
          days: days.map((d) => {
            const items = d.items.filter((t) => t.key !== task.key);
            return d.date === moveTo ? { ...d, items: [...items, { ...task, date: moveTo }] } : { ...d, items };
          }),
        });
        return () => setState({ kind: "ready", days });
      },
      call,
    );

  const remove = async (task: Task) => {
    if (isBusy(task.key)) return;
    if (!(await confirm(`Удалить «${shortTitle(task.title)}»?`))) return;
    await act(task, () => deleteTask(task.key));
  };

  const today = localToday();
  const total = days.reduce((n, d) => n + d.items.length, 0);

  return (
    <>
      <header>
        <h1>Неделя · {total}</h1>
        <button className="ghost" onClick={() => void load()} aria-label="Обновить">↻</button>
      </header>
      {days.map((d) => (
        <section key={d.date}>
          <div className="section-head">
            <h2>{dayLabel(d.date, today)}</h2>
          </div>
          {d.items.length === 0 ? (
            <p className="empty-day">—</p>
          ) : (
            <ul>
              {d.items.map((t) => (
                <TaskCard
                  key={t.key}
                  title={t.title}
                  note={t.note}
                  meta={badges(t)}
                  disabled={isBusy(t.key)}
                  actions={[
                    { label: "Готово", onClick: () => void act(t, () => completeTask(t.key)) },
                    { label: "Удалить", danger: true, onClick: () => void remove(t) },
                  ]}
                  onPickDate={(date) => void act(t, () => scheduleTask(t.key, date), date)}
                />
              ))}
            </ul>
          )}
        </section>
      ))}
    </>
  );
}
