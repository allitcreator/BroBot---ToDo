import { useCallback, useEffect, useState } from "react";
import { Settings, Summary, getSettings, getSummary } from "./api";
import { InboxScreen } from "./screens/InboxScreen";
import { SettingsScreen } from "./screens/SettingsScreen";
import { TodayScreen } from "./screens/TodayScreen";
import { RitualEvening } from "./screens/RitualEvening";
import { RitualMorning } from "./screens/RitualMorning";
import { WeekScreen } from "./screens/WeekScreen";

type Tab = "inbox" | "today" | "week" | "settings";

// Параметр из кнопки сводки бота (?ritual=morning|evening) открывает разбор
// поверх вкладок; test — только метка: проверка, что параметр доходит.
const ritualParam = () => new URLSearchParams(window.location.search).get("ritual");

export default function App() {
  const [tab, setTab] = useState<Tab>("today");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [ritual, setRitual] = useState(ritualParam);

  // Счётчики на вкладках обновляются после каждого действия. Ошибка здесь
  // не важна: экран сам покажет, что не так, а счётчик — подсказка.
  const refreshSummary = useCallback(() => {
    getSummary().then(setSummary).catch(() => undefined);
  }, []);

  useEffect(() => {
    refreshSummary();
    // Настройки нужны экрану «Ещё» и итогу вечера (время утренней сводки).
    getSettings().then(setSettings).catch(() => undefined);
  }, [refreshSummary]);

  const tabs: { id: Tab; icon: string; label: string; count?: number; alert?: boolean }[] = [
    { id: "today", icon: "★", label: "Сегодня", count: summary ? summary.today + summary.overdue : undefined, alert: !!summary?.overdue },
    { id: "inbox", icon: "📥", label: "Входящие", count: summary?.inbox },
    { id: "week", icon: "📅", label: "Неделя" },
    { id: "settings", icon: "⚙️", label: "Ещё" },
  ];

  const closeRitual = () => {
    setRitual(null);
    setTab("today");
    refreshSummary();
  };

  if (ritual === "morning") {
    return (
      <main>
        <RitualMorning onClose={closeRitual} />
      </main>
    );
  }
  if (ritual === "evening") {
    return (
      <main>
        <RitualEvening settings={settings} onClose={closeRitual} />
      </main>
    );
  }

  return (
    <>
      <main>
        {ritual === "test" && <p className="ritual-note">Получено из кнопки: ritual={ritual}</p>}
        {tab === "inbox" && <InboxScreen onChanged={refreshSummary} />}
        {tab === "today" && <TodayScreen onChanged={refreshSummary} inbox={summary?.inbox ?? 0} onOpenInbox={() => setTab("inbox")} onRitual={setRitual} />}
        {tab === "week" && <WeekScreen onChanged={refreshSummary} />}
        {tab === "settings" && <SettingsScreen settings={settings} onSettings={setSettings} />}
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
            <span className="tab-icon" aria-hidden="true">
              {t.icon}
            </span>
            {t.label}
            {t.count ? <span className={t.alert ? "count alert" : "count"}>{t.count}</span> : null}
          </button>
        ))}
      </nav>
    </>
  );
}
