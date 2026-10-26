import { useCallback, useEffect, useState } from "react";
import { Settings, Summary, getSettings, getSummary } from "./api";
import { InboxScreen } from "./screens/InboxScreen";
import { ProjectsScreen } from "./screens/ProjectsScreen";
import { SettingsScreen } from "./screens/SettingsScreen";
import { TodayScreen } from "./screens/TodayScreen";
import { RitualEvening } from "./screens/RitualEvening";
import { RitualMorning } from "./screens/RitualMorning";
import { WeekScreen } from "./screens/WeekScreen";

type Tab = "inbox" | "today" | "week" | "projects" | "settings";

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
    // Настройки нужны оболочке: от них зависит, есть ли вкладка «Проекты».
    getSettings().then(setSettings).catch(() => undefined);
  }, [refreshSummary]);

  const onSettings = (s: Settings) => {
    setSettings(s);
    if (!s.projects_enabled && tab === "projects") setTab("settings");
  };

  const tabs: { id: Tab; label: string; count?: number; alert?: boolean; aria?: string }[] = [
    { id: "inbox", label: "Входящие", count: summary?.inbox },
    { id: "today", label: "Сегодня", count: summary ? summary.today + summary.overdue : undefined, alert: !!summary?.overdue },
    { id: "week", label: "Неделя" },
    ...(settings?.projects_enabled ? [{ id: "projects" as Tab, label: "Проекты" }] : []),
    { id: "settings", label: "⚙️", aria: "Настройки" },
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
        {tab === "today" && <TodayScreen onChanged={refreshSummary} inbox={summary?.inbox ?? 0} onOpenInbox={() => setTab("inbox")} />}
        {tab === "week" && <WeekScreen onChanged={refreshSummary} />}
        {tab === "projects" && <ProjectsScreen />}
        {tab === "settings" && <SettingsScreen settings={settings} onSettings={onSettings} />}
      </main>
      <nav className="tabbar" role="tablist">
        {tabs.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            aria-label={t.aria ? t.aria : undefined}
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
