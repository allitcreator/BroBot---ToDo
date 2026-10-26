import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import * as api from "./api";
import * as tg from "./telegram";

vi.mock("./api", async (orig) => {
  const real = await orig<typeof import("./api")>();
  return {
    ...real,
    getInbox: vi.fn(), scheduleTask: vi.fn(), deleteTask: vi.fn(), getToday: vi.fn(), getWeek: vi.fn(),
    getSummary: vi.fn(), completeTask: vi.fn(), moveTasks: vi.fn(), getSettings: vi.fn(), getTaskTime: vi.fn(),
    getMorning: vi.fn(), getPlan: vi.fn(), ritualDone: vi.fn(), moveKeys: vi.fn(),
  };
});
vi.mock("./telegram", () => ({
  confirm: vi.fn(),
  alertUser: vi.fn(),
  haptic: vi.fn(),
  initData: () => "",
  ready: () => undefined,
}));

function iso(offset: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

const t = (key: string, title: string, extra: Partial<api.RitualTask> = {}): api.RitualTask => ({
  key, title, note: "", date: null, reminder: false, calendar: false, created: "2026-09-01T10:00:00Z", ...extra,
});

const morning: api.Morning = {
  overdue: [t("o1", "Оплатить интернет", { date: iso(-1) }), t("o2", "Полить цветы", { date: iso(-2) })],
  stuck: [t("s1", "Статья про индексы"), t("s2", "Разобрать гараж")],
  inbox: [t("i1", "Пост про Postgres", { note: "https://t.me/c/1/2" }), t("i2", "Купить корм коту")],
};

const plan: api.Plan = {
  events: [{ time: "11:00", title: "Созвон с командой" }],
  today: [t("o1", "Оплатить интернет"), t("p2", "Позвонить в сервис", { time: "15:00", reminder: true })],
  norm: 5,
};

beforeEach(() => {
  window.history.replaceState(null, "", "/?ritual=morning");
  vi.mocked(api.getSettings).mockResolvedValue({
    confirm_mode: "all", research_enabled: true, projects_enabled: true,
    morning_enabled: true, morning_time: "09:30", evening_enabled: true, evening_time: "20:30",
    stuck_days: 14, weekend_rituals: false,
  });
  vi.mocked(api.getSummary).mockResolvedValue({ inbox: 2, today: 1, overdue: 2 });
  vi.mocked(api.getMorning).mockResolvedValue(structuredClone(morning));
  vi.mocked(api.getPlan).mockResolvedValue(plan);
  vi.mocked(api.getToday).mockResolvedValue({ overdue: [], today: [] });
  vi.mocked(api.scheduleTask).mockResolvedValue({ ok: true, date: iso(0) });
  vi.mocked(api.deleteTask).mockResolvedValue({ ok: true });
  vi.mocked(api.moveKeys).mockResolvedValue({ moved: 1, total: 1, failed: [] });
  vi.mocked(api.ritualDone).mockResolvedValue({ ok: true });
  vi.mocked(tg.confirm).mockResolvedValue(true);
});

afterEach(() => window.history.replaceState(null, "", "/"));

const next = () => userEvent.click(screen.getByRole("button", { name: /^(Дальше|Остальное на сегодня)/ }));

describe("Утренний разбор", () => {
  it("открывается по ?ritual=morning с первого непустого шага", async () => {
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Просрочено" })).toBeInTheDocument();
    expect(screen.getByText("Шаг 1 из 4")).toBeInTheDocument();
    expect(screen.getByText("Оплатить интернет")).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  });

  it("пустые шаги пропускаются", async () => {
    vi.mocked(api.getMorning).mockResolvedValue({ overdue: [], stuck: [], inbox: [t("i1", "Купить корм коту")] });
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Входящие" })).toBeInTheDocument();
    expect(screen.getByText("Шаг 1 из 2")).toBeInTheDocument();
  });

  it("«Остальное на сегодня» переносит оставшиеся просроченные", async () => {
    render(<App />);
    await screen.findByText("Оплатить интернет");
    const card = screen.getByText("Полить цветы").closest("li")!;
    await userEvent.click(within(card).getByRole("button", { name: "Удалить" }));
    await waitFor(() => expect(screen.queryByText("Полить цветы")).not.toBeInTheDocument());

    await next();
    expect(api.moveKeys).toHaveBeenCalledWith(["o1"], "today");
    expect(await screen.findByRole("heading", { name: "Застряло" })).toBeInTheDocument();
  });

  it("у просроченной задачи со временем спрашивает время", async () => {
    vi.mocked(api.getMorning).mockResolvedValue({ ...structuredClone(morning), overdue: [t("o1", "Созвон", { date: iso(-1), calendar: true })] });
    vi.mocked(api.getTaskTime).mockResolvedValue({ time: "11:00" });
    render(<App />);
    await screen.findByText("Созвон");
    await next();
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(within(dialog).getByLabelText("Время: Созвон")).toHaveValue("11:00"));
    await userEvent.click(within(dialog).getByRole("button", { name: "Перенести" }));
    expect(api.moveKeys).toHaveBeenCalledWith(["o1"], "today", { o1: "11:00" });
  });

  it("застрявшие по одной: через 2 недели и пропуск", async () => {
    vi.mocked(api.getMorning).mockResolvedValue({ ...structuredClone(morning), overdue: [] });
    render(<App />);
    expect(await screen.findByText("Статья про индексы")).toBeInTheDocument();
    expect(screen.queryByText("Разобрать гараж")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Через 2 недели" }));
    expect(api.scheduleTask).toHaveBeenCalledWith("s1", iso(14));
    expect(await screen.findByText("Разобрать гараж")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Пропустить" }));
    expect(await screen.findByRole("heading", { name: "Входящие" })).toBeInTheDocument();
    expect(api.scheduleTask).toHaveBeenCalledTimes(1);
  });

  it("входящие по одной с источником", async () => {
    vi.mocked(api.getMorning).mockResolvedValue({ overdue: [], stuck: [], inbox: structuredClone(morning.inbox) });
    render(<App />);
    expect(await screen.findByText("Пост про Postgres")).toBeInTheDocument();
    expect(screen.getByText("https://t.me/c/1/2")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Сегодня" }));
    expect(api.scheduleTask).toHaveBeenCalledWith("i1", "today");
    expect(await screen.findByText("Купить корм коту")).toBeInTheDocument();
  });

  it("ошибка оставляет карточку и показывает причину", async () => {
    vi.mocked(api.getMorning).mockResolvedValue({ overdue: [], stuck: [], inbox: structuredClone(morning.inbox) });
    vi.mocked(api.scheduleTask).mockRejectedValue(new api.ApiError(502, "MS To Do не ответил"));
    render(<App />);
    await screen.findByText("Пост про Postgres");
    await userEvent.click(screen.getByRole("button", { name: "Сегодня" }));
    await waitFor(() => expect(tg.alertUser).toHaveBeenCalledWith("MS To Do не ответил"));
    expect(screen.getByText("Пост про Postgres")).toBeInTheDocument();
  });

  it("план дня: встречи, задачи и норма; «Начать день» закрывает разбор", async () => {
    vi.mocked(api.getMorning).mockResolvedValue({ overdue: [], stuck: [], inbox: [] });
    render(<App />);
    expect(await screen.findByRole("heading", { name: "План дня" })).toBeInTheDocument();
    expect(screen.getByText("Созвон с командой")).toBeInTheDocument();
    expect(screen.getByText("Позвонить в сервис")).toBeInTheDocument();
    expect(screen.getByText(/2 задачи и 1 встреча/)).toBeInTheDocument();
    expect(screen.getByText(/обычно закрываешь 5/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Начать день" }));
    expect(api.ritualDone).toHaveBeenCalledWith("morning");
    expect(await screen.findByRole("tablist")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Сегодня/ })).toHaveAttribute("aria-selected", "true");
  });

  it("«Закрыть» выходит из разбора без отметки", async () => {
    render(<App />);
    await screen.findByText("Оплатить интернет");
    await userEvent.click(screen.getByRole("button", { name: "Закрыть" }));
    expect(await screen.findByRole("tablist")).toBeInTheDocument();
    expect(api.ritualDone).not.toHaveBeenCalled();
  });
});
