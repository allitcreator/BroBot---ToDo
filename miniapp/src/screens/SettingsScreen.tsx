import { useCallback, useEffect, useState } from "react";
import { Settings, Stats, getStats, patchSettings } from "../api";
import { ErrorView, errorText } from "../components/Status";
import { alertUser, haptic } from "../telegram";

const MODES: { value: Settings["confirm_mode"]; label: string; hint: string }[] = [
  { value: "all", label: "Всегда", hint: "превью перед каждой задачей" },
  { value: "uncertain", label: "Если не уверен", hint: "только когда бот сомневается" },
  { value: "off", label: "Никогда", hint: "задачи создаются сразу" },
];

type Props = { settings: Settings | null; onSettings: (s: Settings) => void };

export function SettingsScreen({ settings, onSettings }: Props) {
  const [stats, setStats] = useState<{ kind: "loading" } | { kind: "ready"; data: Stats } | { kind: "error"; message: string; expired: boolean }>({ kind: "loading" });
  const [saving, setSaving] = useState(false);

  const loadStats = useCallback(async () => {
    setStats({ kind: "loading" });
    try {
      setStats({ kind: "ready", data: await getStats() });
    } catch (e) {
      setStats({ kind: "error", ...errorText(e) });
    }
  }, []);

  useEffect(() => {
    void loadStats();
  }, [loadStats]);

  const change = async (changes: Partial<Settings>) => {
    if (saving || !settings) return;
    setSaving(true);
    try {
      onSettings(await patchSettings(changes));
      haptic("success");
    } catch (e) {
      haptic("error");
      alertUser((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <header>
        <h1>Настройки</h1>
        <button className="ghost" onClick={() => void loadStats()} aria-label="Обновить">↻</button>
      </header>

      <section className="card">
        <div className="section-head">
          <h2>Статистика</h2>
        </div>
        {stats.kind === "loading" && <div className="center small">Считаю…</div>}
        {stats.kind === "error" && <ErrorView {...stats} onRetry={() => void loadStats()} />}
        {stats.kind === "ready" && <StatsView s={stats.data} />}
      </section>

      {settings && (
        <>
          <section className="card">
            <div className="section-head">
              <h2>Подтверждать задачи</h2>
            </div>
            <div className="modes" role="radiogroup">
              {MODES.map((m) => (
                <label key={m.value} className="mode">
                  <input
                    type="radio"
                    name="confirm_mode"
                    disabled={saving}
                    checked={settings.confirm_mode === m.value}
                    onChange={() => void change({ confirm_mode: m.value })}
                    aria-label={m.label}
                  />
                  <span>
                    {m.label}
                    <small>{m.hint}</small>
                  </span>
                </label>
              ))}
            </div>
          </section>

          <section className="card">
            <Toggle
              label="Разбор идей в проекты"
              hint="идею бот разбирает вопросами и заводит проектом"
              checked={settings.projects_enabled}
              disabled={saving}
              onChange={(v) => void change({ projects_enabled: v })}
            />
            <Toggle
              label="Разведка в интернете"
              hint="ищет готовые решения по проекту, ~полцента за запуск"
              checked={settings.research_enabled}
              disabled={saving}
              onChange={(v) => void change({ research_enabled: v })}
            />
          </section>
        </>
      )}
    </>
  );
}

function Toggle({ label, hint, checked, disabled, onChange }: { label: string; hint: string; checked: boolean; disabled: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="toggle">
      <span>
        {label}
        <small>{hint}</small>
      </span>
      <input type="checkbox" role="checkbox" aria-label={label} checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
    </label>
  );
}

function StatsView({ s }: { s: Stats }) {
  const max = Math.max(1, ...s.week.map((d) => d.completed));
  const rows: [string, number][] = [
    ["Выполнено сегодня", s.completed_today],
    ["Создано сегодня", s.created_today],
    ["Открытых задач", s.open_tasks],
    ["Во входящих", s.inbox],
    ["Просрочено", s.overdue],
  ];
  return (
    <>
      <div className="stats">
        {rows.map(([label, value]) => (
          <div key={label} className="stat">
            <span>{label}</span>
            <b>{value}</b>
          </div>
        ))}
      </div>
      <div className="week" aria-label="Выполнено по дням за неделю">
        {s.week.map((d) => (
          <div key={d.date} className="week-col" title={`${d.date}: ${d.completed}`}>
            <div className="week-value">{d.completed || ""}</div>
            <div data-testid="week-bar" className="week-bar" style={{ height: `${Math.round((d.completed / max) * 48) + 2}px` }} />
            <div className="week-day">{new Date(`${d.date}T12:00:00`).toLocaleDateString("ru-RU", { weekday: "short" })}</div>
          </div>
        ))}
      </div>
    </>
  );
}
