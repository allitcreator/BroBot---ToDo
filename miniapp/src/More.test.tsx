import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import * as api from "./api";
import * as tg from "./telegram";

vi.mock("./api", async (orig) => {
  const real = await orig<typeof import("./api")>();
  return {
    ...real,
    getInbox: vi.fn(), getSummary: vi.fn(), getSettings: vi.fn(), patchSettings: vi.fn(), getStats: vi.fn(),
    getProjects: vi.fn(), getProject: vi.fn(), toggleStep: vi.fn(), appendSection: vi.fn(),
    startResearch: vi.fn(), researchStatus: vi.fn(),
  };
});
vi.mock("./telegram", () => ({
  confirm: vi.fn(), alertUser: vi.fn(), haptic: vi.fn(), initData: () => "", ready: () => undefined,
}));

const settings: api.Settings = {
  confirm_mode: "uncertain", research_enabled: true, projects_enabled: true,
  morning_enabled: true, morning_time: "09:30", evening_enabled: true, evening_time: "20:30",
  stuck_days: 14, weekend_rituals: false,
};

const project: api.Project = {
  title: "TeamNeedsBot",
  sections: [
    { key: "why", title: "Зачем", value: "Устал терять заявки" },
    { key: "risks", title: "Открытые риски", value: "" },
  ],
  steps: [
    { id: "s1", text: "Сделать MVP", checked: false },
    { id: "s2", text: "Показать команде", checked: true },
  ],
  body_valid: true,
  research_enabled: true,
  research: { status: "idle" },
};

beforeEach(() => {
  vi.mocked(api.getSummary).mockResolvedValue({ inbox: 0, today: 0, overdue: 0 });
  vi.mocked(api.getInbox).mockResolvedValue({ count: 0, items: [] });
  vi.mocked(api.getSettings).mockResolvedValue(settings);
  vi.mocked(api.getProjects).mockResolvedValue({ items: [{ key: "p1", title: "TeamNeedsBot", summary: "Устал терять заявки" }] });
  vi.mocked(api.getProject).mockResolvedValue(project);
  vi.mocked(api.toggleStep).mockResolvedValue({ ok: true });
  vi.mocked(api.appendSection).mockResolvedValue({ ok: true });
  vi.mocked(api.getStats).mockResolvedValue({
    completed_today: 3, created_today: 5, open_tasks: 16, overdue: 2, inbox: 4,
    week: [0, 1, 2, 3, 4, 5, 6].map((i) => ({ date: `2026-10-2${i}`, completed: i })),
  });
});

afterEach(() => {
  vi.useRealTimers();
});

async function openProject() {
  render(<App />);
  await userEvent.click(await screen.findByRole("tab", { name: /Проекты/ }));
  await userEvent.click(await screen.findByText("TeamNeedsBot"));
  await screen.findByText("Сделать MVP");
}

describe("Проекты", () => {
  it("вкладки нет, если проекты выключены", async () => {
    vi.mocked(api.getSettings).mockResolvedValue({ ...settings, projects_enabled: false });
    render(<App />);
    await screen.findByRole("tab", { name: /Входящие/ });
    await waitFor(() => expect(api.getSettings).toHaveBeenCalled());
    expect(screen.queryByRole("tab", { name: /Проекты/ })).not.toBeInTheDocument();
  });

  it("список и карточка с брифом и шагами", async () => {
    await openProject();
    expect(screen.getByText("Устал терять заявки")).toBeInTheDocument();
    expect(screen.getByText("Открытые риски")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Показать команде" })).toBeChecked();
  });

  it("шаг отмечается сразу и откатывается при ошибке", async () => {
    vi.mocked(api.toggleStep).mockRejectedValue(new api.ApiError(502, "MS To Do не ответил"));
    await openProject();
    const box = screen.getByRole("checkbox", { name: "Сделать MVP" });
    await userEvent.click(box);

    expect(api.toggleStep).toHaveBeenCalledWith("p1", "s1", true);
    await waitFor(() => expect(tg.alertUser).toHaveBeenCalledWith("MS To Do не ответил"));
    expect(box).not.toBeChecked();
  });

  it("«Дополнить» дописывает текст в секцию и перечитывает проект", async () => {
    await openProject();
    const section = screen.getByText("Открытые риски").closest("section")!;
    await userEvent.click(within(section).getByRole("button", { name: "Дополнить" }));
    await userEvent.type(within(section).getByRole("textbox"), "Не взлетит без интеграции");
    await userEvent.click(within(section).getByRole("button", { name: "Сохранить" }));

    expect(api.appendSection).toHaveBeenCalledWith("p1", "risks", "Не взлетит без интеграции");
    await waitFor(() => expect(api.getProject).toHaveBeenCalledTimes(2));
  });

  it("заметку правили руками — дополнять нельзя, объясняем почему", async () => {
    vi.mocked(api.getProject).mockResolvedValue({ ...project, body_valid: false });
    await openProject();
    expect(screen.queryByRole("button", { name: "Дополнить" })).not.toBeInTheDocument();
    expect(screen.getByText(/правили руками/)).toBeInTheDocument();
  });

  it("разведка идёт в фоне, статус опрашивается до готовности", async () => {
    vi.mocked(api.startResearch).mockResolvedValue({ status: "running" });
    vi.mocked(api.researchStatus)
      .mockResolvedValueOnce({ status: "running" })
      .mockResolvedValueOnce({ status: "done" });
    await openProject();

    vi.useFakeTimers({ shouldAdvanceTime: true });
    await userEvent.click(screen.getByRole("button", { name: /Разведать/ }));
    expect(await screen.findByText(/Ищу/)).toBeInTheDocument();

    await act(async () => { await vi.advanceTimersByTimeAsync(7000); });
    await waitFor(() => expect(api.researchStatus).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(api.getProject).toHaveBeenCalledTimes(2));
  });

  it("кнопки разведки нет, если она выключена", async () => {
    vi.mocked(api.getProject).mockResolvedValue({ ...project, research_enabled: false });
    await openProject();
    expect(screen.queryByRole("button", { name: /Разведать/ })).not.toBeInTheDocument();
  });

  it("«Назад» возвращает к списку", async () => {
    await openProject();
    await userEvent.click(screen.getByRole("button", { name: /Проекты/ }));
    expect(await screen.findByText("Устал терять заявки")).toBeInTheDocument();
    expect(screen.queryByText("Сделать MVP")).not.toBeInTheDocument();
  });
});

describe("Настройки и статистика", () => {
  async function openSettings() {
    render(<App />);
    await userEvent.click(await screen.findByRole("tab", { name: /Настройки/ }));
    await screen.findByText("Выполнено сегодня");
  }

  it("показывают счётчики и неделю", async () => {
    await openSettings();
    const row = screen.getByText("Выполнено сегодня").closest("div")!;
    expect(within(row).getByText("3")).toBeInTheDocument();
    expect(screen.getAllByTestId("week-bar")).toHaveLength(7);
  });

  it("режим подтверждения меняется", async () => {
    vi.mocked(api.patchSettings).mockResolvedValue({ ...settings, confirm_mode: "off" });
    await openSettings();
    await userEvent.click(screen.getByRole("radio", { name: "Никогда" }));
    expect(api.patchSettings).toHaveBeenCalledWith({ confirm_mode: "off" });
    await waitFor(() => expect(screen.getByRole("radio", { name: "Никогда" })).toBeChecked());
  });

  it("время утренней сводки меняется", async () => {
    vi.mocked(api.patchSettings).mockResolvedValue({ ...settings, morning_time: "08:00" });
    await openSettings();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Время утренней сводки" }), "08:00");
    expect(api.patchSettings).toHaveBeenCalledWith({ morning_time: "08:00" });
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Время утренней сводки" })).toHaveValue("08:00"));
  });

  it("выключенный вечер — время не выбрать", async () => {
    vi.mocked(api.patchSettings).mockResolvedValue({ ...settings, evening_enabled: false });
    await openSettings();
    fireEvent.click(screen.getByRole("checkbox", { name: /Вечернее закрытие/ }));
    expect(api.patchSettings).toHaveBeenCalledWith({ evening_enabled: false });
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Время вечерней сводки" })).toBeDisabled());
  });

  it("порог «застряло» и выходные", async () => {
    vi.mocked(api.patchSettings).mockResolvedValue({ ...settings, stuck_days: 30 });
    await openSettings();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: /Застряло/ }), "30");
    expect(api.patchSettings).toHaveBeenCalledWith({ stuck_days: 30 });
    fireEvent.click(screen.getByRole("checkbox", { name: /Сводки в выходные/ }));
    expect(api.patchSettings).toHaveBeenLastCalledWith({ weekend_rituals: true });
  });

  it("выключение проектов убирает вкладку", async () => {
    vi.mocked(api.patchSettings).mockResolvedValue({ ...settings, projects_enabled: false });
    await openSettings();
    expect(screen.getByRole("tab", { name: /Проекты/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: /Разбор идей в проекты/ }));
    await waitFor(() => expect(screen.queryByRole("tab", { name: /Проекты/ })).not.toBeInTheDocument());
  });
});
