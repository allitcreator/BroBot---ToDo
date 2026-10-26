import { initData } from "./telegram";

export type InboxItem = {
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

export function scheduleTask(key: string, date: string): Promise<{ ok: true; date: string }> {
  return call("POST", `/api/tasks/${encodeURIComponent(key)}/schedule`, { date });
}

export function deleteTask(key: string): Promise<{ ok: true }> {
  return call("DELETE", `/api/tasks/${encodeURIComponent(key)}`);
}

export type Task = {
  key: string;
  title: string;
  note: string;
  date: string | null;
  reminder: boolean;
  calendar: boolean;
};

export type Summary = { inbox: number; today: number; overdue: number };
export type MoveResult = { moved: number; total: number; failed: string[] };

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

export function moveTasks(scope: "overdue" | "today", to: "today" | "tomorrow"): Promise<MoveResult> {
  return call("POST", "/api/tasks/move", { scope, to });
}
