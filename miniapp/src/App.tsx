import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, InboxItem, deleteTask, getInbox, scheduleTask } from "./api";
import { alertUser, confirm, haptic } from "./telegram";

type Load =
  | { kind: "loading" }
  | { kind: "ready"; items: InboxItem[] }
  | { kind: "error"; message: string; expired: boolean };

function shortDate(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
}

export default function App() {
  const [state, setState] = useState<Load>({ kind: "loading" });
  // Ключи, по которым уже идёт запрос: второй тап по той же карточке
  // игнорируется, иначе задача переносилась бы дважды.
  const busy = useRef(new Set<string>());
  const [, rerender] = useState(0);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const data = await getInbox();
      setState({ kind: "ready", items: data.items });
    } catch (e) {
      const expired = e instanceof ApiError && e.status === 401;
      setState({ kind: "error", message: (e as Error).message, expired });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(item: InboxItem, run: () => Promise<unknown>) {
    if (busy.current.has(item.key) || state.kind !== "ready") return;
    busy.current.add(item.key);
    rerender((n) => n + 1);
    const before = state.items;
    // Убираем сразу: Graph отвечает секунду-другую, ждать его незачем.
    setState({ kind: "ready", items: before.filter((i) => i.key !== item.key) });
    try {
      await run();
      haptic("success");
    } catch (e) {
      haptic("error");
      setState({ kind: "ready", items: before });
      alertUser((e as Error).message);
    } finally {
      busy.current.delete(item.key);
      rerender((n) => n + 1);
    }
  }

  async function remove(item: InboxItem) {
    if (busy.current.has(item.key)) return;
    // showConfirm в Telegram принимает не больше 256 символов.
    const title = item.title.length > 200 ? `${item.title.slice(0, 200)}…` : item.title;
    if (!(await confirm(`Удалить «${title}»?`))) return;
    await act(item, () => deleteTask(item.key));
  }

  if (state.kind === "loading") {
    return <main className="center">Загружаю…</main>;
  }
  if (state.kind === "error") {
    return (
      <main className="center">
        <p>{state.expired ? "Сессия устарела — закрой и открой мини-апп заново." : state.message}</p>
        {!state.expired && <button onClick={() => void load()}>Повторить</button>}
      </main>
    );
  }

  const { items } = state;
  return (
    <main>
      <header>
        <h1>Входящие · {items.length}</h1>
        <button className="ghost" onClick={() => void load()} aria-label="Обновить">↻</button>
      </header>

      {items.length === 0 && <p className="center">Пусто, всё разобрано 🎉</p>}

      <ul>
        {items.map((item) => {
          const disabled = busy.current.has(item.key);
          return (
            <li key={item.key} className="card">
              <div className="title">{item.title}</div>
              {item.note && <div className="note">{item.note}</div>}
              <div className="meta">{shortDate(item.created)}</div>
              <div className="actions">
                <button disabled={disabled} onClick={() => void act(item, () => scheduleTask(item.key, "today"))}>
                  Сегодня
                </button>
                <button disabled={disabled} onClick={() => void act(item, () => scheduleTask(item.key, "tomorrow"))}>
                  Завтра
                </button>
                <label className={`date ${disabled ? "disabled" : ""}`}>
                  Дата
                  <input
                    type="date"
                    disabled={disabled}
                    aria-label="Выбрать дату"
                    onChange={(e) => {
                      const value = e.target.value;
                      if (value) void act(item, () => scheduleTask(item.key, value));
                    }}
                  />
                </label>
                <button className="danger" disabled={disabled} onClick={() => void remove(item)}>
                  Удалить
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </main>
  );
}
