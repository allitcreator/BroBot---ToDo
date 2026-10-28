import { initData } from "./telegram";

// Служебный блок заметки задачи: номер на сегодня, исполнитель, рекомендации.
export type Executor = "agent" | "self" | null;
export type TaskMeta = { order?: number | null; executor?: Executor; advice?: string | null };

export type InboxItem = TaskMeta & {
  key: string;
  title: string;
  note: string;
  created: string | null;
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const resp = await fetch(path, {
    method,
    headers: {
      Authorization: `tma ${initData()}`,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  let data: { message?: string } = {};
  try {
    data = await resp.json();
  } catch {
    // тело не JSON — ниже сработает общий текст
  }
  if (!resp.ok) {
    throw new ApiError(resp.status, data.message ?? `Ошибка ${resp.status}`);
  }
  return data as T;
}

export function getInbox(): Promise<{ count: number; items: InboxItem[] }> {
  return call("GET", "/api/inbox");
}

// Что не удалось сдвинуть вместе с задачей: событие или напоминания.
export type MoveWarning = "calendar" | "reminder";

// time: "ЧЧ:ММ" — новое время напоминаний и события, null — снять их,
// не передано — то же время в новый день.
export function scheduleTask(
  key: string,
  date: string,
  time?: string | null,
): Promise<{ ok: true; date: string; warnings?: MoveWarning[] }> {
  return call("POST", `/api/tasks/${encodeURIComponent(key)}/schedule`, time === undefined ? { date } : { date, time });
}

export function getTaskTime(key: string): Promise<{ time: string | null }> {
  return call("GET", `/api/tasks/${encodeURIComponent(key)}/time`);
}

export function deleteTask(key: string): Promise<{ ok: true }> {
  return call("DELETE", `/api/tasks/${encodeURIComponent(key)}`);
}

export type Task = TaskMeta & {
  key: string;
  title: string;
  note: string;
  date: string | null;
  reminder: boolean;
  calendar: boolean;
};

// Утренний разбор: шаги и план дня.
export type RitualTask = Task & { created?: string | null; time?: string | null };
export type Morning = { overdue: RitualTask[]; stuck: RitualTask[]; inbox: RitualTask[] };
export type Plan = { events: { time: string | null; title: string }[] | null; today: RitualTask[]; norm: number | null };

export function getMorning(): Promise<Morning> {
  return call("GET", "/api/ritual/morning");
}

export function getPlan(): Promise<Plan> {
  return call("GET", "/api/ritual/plan");
}

export type Evening = {
  today_open: RitualTask[];
  done_today: number;
  tomorrow: RitualTask[];
  tomorrow_events: { time: string | null; title: string }[] | null;
  inbox_count: number;
};

export function getEvening(): Promise<Evening> {
  return call("GET", "/api/ritual/evening");
}

export function ritualDone(kind: "morning" | "evening"): Promise<{ ok: true }> {
  return call("POST", `/api/ritual/${kind}/done`);
}

// Перенос выбранных задач: to — today, tomorrow или ГГГГ-ММ-ДД.
export function moveKeys(keys: string[], to: string, times?: Times): Promise<MoveResult> {
  return call("POST", "/api/tasks/move", times ? { keys, to, times } : { keys, to });
}

export type Summary = { inbox: number; today: number; overdue: number; reading?: number };
export type MoveResult = { moved: number; total: number; failed: string[]; warnings?: MoveWarning[] };
// Время по ключу задачи; ключа нет — то же время.
export type Times = Record<string, string | null>;

// Порядок дня и исполнители одним запросом. order — ключи задач на сегодня
// по порядку (остальным сегодняшним номер снимается); meta — null снимает поле.
export type PlanBody = {
  order?: string[];
  meta?: Record<string, { executor?: Executor; advice?: string | null }>;
};

export function savePlan(body: PlanBody): Promise<{ saved: number; total: number; failed: string[] }> {
  return call("POST", "/api/tasks/plan", body);
}

export function getToday(): Promise<{ overdue: Task[]; today: Task[] }> {
  return call("GET", "/api/tasks?view=today");
}

export function getWeek(): Promise<{ days: { date: string; items: Task[] }[] }> {
  return call("GET", "/api/tasks?view=week");
}

export function getSummary(): Promise<Summary> {
  return call("GET", "/api/summary");
}

export function completeTask(key: string): Promise<{ ok: true }> {
  return call("POST", `/api/tasks/${encodeURIComponent(key)}/complete`);
}

export function moveTasks(scope: "overdue" | "today", to: "today" | "tomorrow", times?: Times): Promise<MoveResult> {
  return call("POST", "/api/tasks/move", times ? { scope, to, times } : { scope, to });
}

export type Settings = {
  confirm_mode: "all" | "uncertain" | "off";
  research_enabled: boolean;
  projects_enabled: boolean;
  // Ритуалы разбора: время «ЧЧ:00» или «ЧЧ:30» по Москве.
  morning_enabled: boolean;
  morning_time: string;
  evening_enabled: boolean;
  evening_time: string;
  stuck_days: 7 | 14 | 30;
  weekend_rituals: boolean;
};
export type Stats = {
  completed_today: number;
  created_today: number;
  open_tasks: number;
  overdue: number;
  inbox: number;
  week: { date: string; completed: number }[];
};

export function getSettings(): Promise<Settings> {
  return call("GET", "/api/settings");
}

export function patchSettings(changes: Partial<Settings>): Promise<Settings> {
  return call("PATCH", "/api/settings", changes);
}

export function getStats(): Promise<Stats> {
  return call("GET", "/api/stats");
}


// «Чтение»: материалы, опубликованные с Mac.
export type MaterialMeta = {
  id: number;
  title: string;
  source: string;
  created_at: string;
  pinned: boolean;
  read: boolean;
  chars: number;
};
export type Material = MaterialMeta & { markdown: string };

export function getReading(): Promise<{ unread: number; items: MaterialMeta[] }> {
  return call("GET", "/api/reading");
}

export function getMaterial(id: number): Promise<Material> {
  return call("GET", `/api/reading/${id}`);
}

export function setMaterialRead(id: number, read: boolean): Promise<{ ok: true }> {
  return call("POST", `/api/reading/${id}/read`, { read });
}

export function setMaterialPinned(id: number, pinned: boolean): Promise<{ ok: true }> {
  return call("POST", `/api/reading/${id}/pin`, { pinned });
}

export function deleteMaterial(id: number): Promise<{ ok: true }> {
  return call("DELETE", `/api/reading/${id}`);
}
