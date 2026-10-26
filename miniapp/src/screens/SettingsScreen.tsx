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
        <h1>
          <span className="ico">⚙️</span>Ещё
        </h1>
        <button className="ghost" onClick={() => void loadStats()} aria-label="Обновить">↻</button>
      </header>

      <section>
        <div className="group">
          <h2>Статистика</h2>
        </div>
        {stats.kind === "loading" && <div className="center small">Считаю…</div>}
        {stats.kind === "error" && <ErrorView {...stats} onRetry={() => void loadStats()} />}
        {stats.kind === "ready" && <StatsView s={stats.data} />}
      </section>

      {settings && (
        <>
          <section>
            <div className="group">
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
              hint="если на сегодня остались задачи"
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
            <label className="set">
              <span>
                «Застряло» — старше
                <small>до 3 задач за утро</small>
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

          <section>
            <div className="group">
              <h2>Создание задач</h2>
            </div>
            <label className="set">
              <span>
                Подтверждать задачи
                <small>{MODES.find((m) => m.value === settings.confirm_mode)?.hint}</small>
              </span>
              <select
                aria-label="Подтверждать задачи"
                value={settings.confirm_mode}
                disabled={saving}
                onChange={(e) => void change({ confirm_mode: e.target.value as Settings["confirm_mode"] })}
              >
                {MODES.map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
            </label>
          </section>
        </>
      )}
    </>
  );
}

// Переключатель в стиле iOS: role="switch", а не голый чекбокс.
function Toggle({ label, hint, checked, disabled, onChange }: { label: string; hint: string; checked: boolean; disabled: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="set">
      <span>
        {label}
        <small>{hint}</small>
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        className={checked ? "switch on" : "switch"}
        disabled={disabled}
        onClick={() => onChange(!checked)}
      />
    </div>
  );
}

function TimeSelect({ label, value, disabled, onChange }: { label: string; value: string; disabled: boolean; onChange: (v: string) => void }) {
  return (
    <label className="set sub">
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
    ["сделано сегодня", s.completed_today],
    ["открытых", s.open_tasks],
    ["во входящих", s.inbox],
  ];
  return (
    <>
      <div className="stats">
        {rows.map(([label, value]) => (
          <div key={label} className="stat">
            <b>{value}</b>
            <span>{label}</span>
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
