import { useRef, useState } from "react";
import { alertUser, haptic } from "./telegram";

// Действие над карточкой: убираем её из списка сразу (Graph отвечает
// секунду-другую), при ошибке возвращаем и показываем причину. Ключи, по
// которым идёт запрос, заблокированы — двойной тап не шлёт второй запрос.
export function useCardActions(onChanged?: () => void) {
  const busy = useRef(new Set<string>());
  const [, tick] = useState(0);

  async function run(key: string, removeOptimistically: () => () => void, call: () => Promise<unknown>) {
    if (busy.current.has(key)) return;
    busy.current.add(key);
    tick((n) => n + 1);
    const undo = removeOptimistically();
    try {
      await call();
      haptic("success");
      onChanged?.();
    } catch (e) {
      haptic("error");
      undo();
      alertUser((e as Error).message);
    } finally {
      busy.current.delete(key);
      tick((n) => n + 1);
    }
  }

  return { run, isBusy: (key: string) => busy.current.has(key) };
}

export function shortTitle(title: string): string {
  // showConfirm в Telegram принимает не больше 256 символов.
  return title.length > 200 ? `${title.slice(0, 200)}…` : title;
}
