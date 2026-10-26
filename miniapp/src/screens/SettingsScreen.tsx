import { useCallback, useEffect, useState } from "react";
import { Settings, Stats, getStats, patchSettings } from "../api";
import { ErrorView, errorText } from "../components/Status";
import { alertUser, haptic } from "../telegram";

const MODES: { value: Settings["confirm_mode"]; label: string; hint: string }[] = [
  { value: "all", label: "Всегда", hint: "превью перед каждой задачей" },
  { value: "uncertain", label: "Если не уверен", hint: "только когда бот сомневается" },
  { value: "off", label: "Никогда", hint: "задачи создаются сразу" },
];

// Сводки проверяются по получасам.
const TIMES = Array.from({ length: 48 }, (_, i) => `${String(Math.floor(i / 2)).padStart(2, "0")}:${i % 2 ? "30" : "00"}`);
const STUCK_DAYS: Settings["stuck_days"][] = [7, 14, 30];

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
            <div className="section-head">
              <h2>Ритуалы</h2>
            </div>
            <Toggle
              label="Утренний разбор"
              hint="сводка в чат, если есть что решать"
              checked={settings.morning_enabled}
              disabled={saving}
              onChange={(v) => void change({ morning_enabled: v })}
            />
            <TimeSelect
              label="Время утренней сводки"
              value={settings.morning_time}
              disabled={saving || !settings.morning_enabled}
              onChange={(v) => void change({ morning_time: v })}
            />
            <Toggle
              label="Вечернее закрытие"
              hint="если на сегодня остались незакрытые задачи"
              checked={settings.evening_enabled}
              disabled={saving}
              onChange={(v) => void change({ evening_enabled: v })}
            />
            <TimeSelect
              label="Время вечерней сводки"
              value={settings.evening_time}
              disabled={saving || !settings.evening_enabled}
              onChange={(v) => void change({ evening_time: v })}
            />
            <label className="toggle">
              <span>
                «Застряло» — старше
                <small>показывать до 3 за утро</small>
              </span>
              <select
                aria-label="Застряло: порог в днях"
                value={settings.stuck_days}
                disabled={saving}
                onChange={(e) => void change({ stuck_days: Number(e.target.value) as Settings["stuck_days"] })}
              >
                {STUCK_DAYS.map((d) => (
                  <option key={d} value={d}>
                    {d} дней
                  </option>
                ))}
              </select>
            </label>
            <Toggle
              label="Сводки в выходные"
              hint="в сб и вс задач в 4 раза меньше"
              checked={settings.weekend_rituals}
              disabled={saving}
              onChange={(v) => void change({ weekend_rituals: v })}
            />
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

function TimeSelect({ label, value, disabled, onChange }: { label: string; value: string; disabled: boolean; onChange: (v: string) => void }) {
  return (
    <label className="toggle sub">
      <span>Время</span>
      <select aria-label={label} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        {TIMES.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
      </select>
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
