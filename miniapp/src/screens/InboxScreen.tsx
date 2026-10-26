import { useCallback, useEffect, useState } from "react";
import { InboxItem, deleteTask, getInbox, scheduleTask } from "../api";
import { TaskCard } from "../components/TaskCard";
import { ErrorView, errorText } from "../components/Status";
import { shortDate } from "../format";
import { shortTitle, useCardActions } from "../hooks";
import { confirm } from "../telegram";

type Load =
  | { kind: "loading" }
  | { kind: "ready"; items: InboxItem[] }
  | { kind: "error"; message: string; expired: boolean };

export function InboxScreen({ onChanged }: { onChanged?: () => void }) {
  const [state, setState] = useState<Load>({ kind: "loading" });
  const { run, isBusy } = useCardActions(onChanged);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      setState({ kind: "ready", items: (await getInbox()).items });
    } catch (e) {
      setState({ kind: "error", ...errorText(e) });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (state.kind === "loading") return <div className="center">Загружаю…</div>;
  if (state.kind === "error") return <ErrorView {...state} onRetry={() => void load()} />;

  const items = state.items;
  const act = (item: InboxItem, call: () => Promise<unknown>) =>
    run(
      item.key,
      () => {
        setState({ kind: "ready", items: items.filter((i) => i.key !== item.key) });
        return () => setState({ kind: "ready", items });
      },
      call,
    );

  const remove = async (item: InboxItem) => {
    if (isBusy(item.key)) return;
    if (!(await confirm(`Удалить «${shortTitle(item.title)}»?`))) return;
    await act(item, () => deleteTask(item.key));
  };

  return (
    <>
      <header>
        <h1>
          <span className="ico">📥</span>Входящие
        </h1>
        <button className="ghost" onClick={() => void load()} aria-label="Обновить">↻</button>
      </header>
      <p className="sub">{items.length} без даты</p>
      {items.length === 0 && (
        <div className="empty">
          <div className="big">📥</div>
          Пусто, всё разобрано 🎉
          <small>Всё, что пришлёте боту без даты, ляжет сюда.</small>
        </div>
      )}
      <ul>
        {items.map((item) => (
          <TaskCard
            key={item.key}
            title={item.title}
            note={item.note}
            meta={shortDate(item.created)}
            disabled={isBusy(item.key)}
            open
            actions={[
              { label: "Сегодня", main: true, onClick: () => void act(item, () => scheduleTask(item.key, "today")) },
              { label: "Завтра", onClick: () => void act(item, () => scheduleTask(item.key, "tomorrow")) },
              { label: "Удалить", danger: true, onClick: () => void remove(item) },
            ]}
            onPickDate={(date) => void act(item, () => scheduleTask(item.key, date))}
          />
        ))}
      </ul>
    </>
  );
}
