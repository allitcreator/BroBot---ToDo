import { useCallback, useEffect, useState } from "react";
import { Summary, getSummary } from "./api";
import { InboxScreen } from "./screens/InboxScreen";
import { TodayScreen } from "./screens/TodayScreen";
import { WeekScreen } from "./screens/WeekScreen";

type Tab = "inbox" | "today" | "week";

export default function App() {
  const [tab, setTab] = useState<Tab>("inbox");
  const [summary, setSummary] = useState<Summary | null>(null);

  // Счётчики на вкладках обновляются после каждого действия. Ошибка здесь
  // не важна: экран сам покажет, что не так, а счётчик — подсказка.
  const refreshSummary = useCallback(() => {
    getSummary().then(setSummary).catch(() => undefined);
  }, []);

  useEffect(() => {
    refreshSummary();
  }, [refreshSummary]);

  const tabs: { id: Tab; label: string; count?: number; alert?: boolean }[] = [
    { id: "inbox", label: "Входящие", count: summary?.inbox },
    { id: "today", label: "Сегодня", count: summary ? summary.today + summary.overdue : undefined, alert: !!summary?.overdue },
    { id: "week", label: "Неделя" },
  ];

  return (
    <>
      <main>
        {tab === "inbox" && <InboxScreen onChanged={refreshSummary} />}
        {tab === "today" && <TodayScreen onChanged={refreshSummary} />}
        {tab === "week" && <WeekScreen onChanged={refreshSummary} />}
      </main>
      <nav className="tabbar" role="tablist">
        {tabs.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            className={tab === t.id ? "active" : undefined}
            onClick={() => setTab(t.id)}
          >
            {t.label}
            {t.count ? <span className={t.alert ? "count alert" : "count"}>{t.count}</span> : null}
          </button>
        ))}
      </nav>
    </>
  );
}
