import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import * as api from "./api";
import * as tg from "./telegram";

// Поведение строк в стиле Things 3: чекбокс закрывает, тап раскрывает
// действия, ссылки в названии — короткой меткой.

vi.mock("./api", async (orig) => {
  const real = await orig<typeof import("./api")>();
  return {
    ...real,
    getInbox: vi.fn(), getSummary: vi.fn(), getSettings: vi.fn(), getToday: vi.fn(), completeTask: vi.fn(), scheduleTask: vi.fn(),
  };
});
vi.mock("./telegram", () => ({
  confirm: vi.fn(), alertUser: vi.fn(), haptic: vi.fn(), initData: () => "", ready: () => undefined, openLink: vi.fn(),
}));

const task = (key: string, title: string): api.Task => ({ key, title, note: "", date: "2026-10-27", reminder: false, calendar: false });

beforeEach(() => {
  vi.mocked(api.getSummary).mockResolvedValue({ inbox: 0, today: 2, overdue: 0 });
  vi.mocked(api.getSettings).mockResolvedValue({
    confirm_mode: "all", research_enabled: true, projects_enabled: true,
    morning_enabled: true, morning_time: "09:30", evening_enabled: true, evening_time: "20:30",
    stuck_days: 14, weekend_rituals: false,
  });
  vi.mocked(api.getToday).mockResolvedValue({
    overdue: [],
    today: [
      task("t1", "Посмотреть статус заявки https://st.example.ru/SALARY-634569"),
      task("t2", "Посмотреть https://wiki.example.ru/users/someone/workflow/"),
    ],
  });
  vi.mocked(api.completeTask).mockResolvedValue({ ok: true });
  vi.mocked(api.scheduleTask).mockResolvedValue({ ok: true, date: "2026-10-28" });
});

describe("Строки задач", () => {
  it("ссылка в названии — короткая метка, открывается по нажатию", async () => {
    render(<App />);
    const chip = await screen.findByRole("link", { name: /SALARY-634569/ });
    expect(screen.getByRole("link", { name: /wiki\.example\.ru/ })).toBeInTheDocument();
    expect(screen.queryByText(/https:\/\//)).not.toBeInTheDocument();
    await userEvent.click(chip);
    expect(tg.openLink).toHaveBeenCalledWith("https://st.example.ru/SALARY-634569");
    expect(screen.queryByRole("button", { name: "Завтра" }), "нажатие на ссылку не раскрывает строку").not.toBeInTheDocument();
  });

  it("действия скрыты, пока не нажать на задачу", async () => {
    render(<App />);
    await screen.findByText(/Посмотреть статус заявки/);
    expect(screen.queryByRole("button", { name: "Завтра" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByText(/Посмотреть статус заявки/));
    const row = screen.getByText(/Посмотреть статус заявки/).closest("li")!;
    await userEvent.click(within(row).getByRole("button", { name: "Завтра" }));
    expect(api.scheduleTask).toHaveBeenCalledWith("t1", "tomorrow");
  });

  it("чекбокс закрывает задачу без раскрытия", async () => {
    render(<App />);
    await screen.findByText(/Посмотреть статус заявки/);
    const row = screen.getByText(/Посмотреть статус заявки/).closest("li")!;
    await userEvent.click(within(row).getByRole("button", { name: "Готово" }));
    expect(api.completeTask).toHaveBeenCalledWith("t1");
    await waitFor(() => expect(screen.queryByText(/Посмотреть статус заявки/)).not.toBeInTheDocument());
  });

  it("заголовок «Сегодня» с датой и числом задач", async () => {
    render(<App />);
    expect(await screen.findByRole("heading", { level: 1, name: /Сегодня/ })).toBeInTheDocument();
    expect(screen.getByText(/2 задачи/)).toBeInTheDocument();
  });
});
