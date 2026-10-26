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
