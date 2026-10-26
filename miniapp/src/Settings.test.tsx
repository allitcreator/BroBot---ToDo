import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import * as api from "./api";

vi.mock("./api", async (orig) => {
  const real = await orig<typeof import("./api")>();
  return {
    ...real,
    getInbox: vi.fn(), getSummary: vi.fn(), getSettings: vi.fn(), patchSettings: vi.fn(), getStats: vi.fn(),
    getToday: vi.fn(),
  };
});
vi.mock("./telegram", () => ({
  confirm: vi.fn(), alertUser: vi.fn(), haptic: vi.fn(), initData: () => "", ready: () => undefined, openLink: vi.fn(),
}));

const settings: api.Settings = {
  confirm_mode: "uncertain", research_enabled: true, projects_enabled: true,
  morning_enabled: true, morning_time: "09:30", evening_enabled: true, evening_time: "20:30",
  stuck_days: 14, weekend_rituals: false,
};

beforeEach(() => {
  vi.mocked(api.getSummary).mockResolvedValue({ inbox: 0, today: 0, overdue: 0 });
  vi.mocked(api.getInbox).mockResolvedValue({ count: 0, items: [] });
  vi.mocked(api.getToday).mockResolvedValue({ overdue: [], today: [] });
  vi.mocked(api.getSettings).mockResolvedValue(settings);
  vi.mocked(api.getStats).mockResolvedValue({
    completed_today: 3, created_today: 5, open_tasks: 16, overdue: 2, inbox: 4,
    week: [0, 1, 2, 3, 4, 5, 6].map((i) => ({ date: `2026-10-2${i}`, completed: i })),
  });
});

async function openSettings() {
  render(<App />);
  await userEvent.click(await screen.findByRole("tab", { name: /Ещё/ }));
  await screen.findByText("сделано сегодня");
}

describe("Ещё: статистика и настройки", () => {
  it("проектов в мини-аппе нет", async () => {
    await openSettings();
    expect(screen.queryByRole("tab", { name: /Проекты/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/проект/i)).not.toBeInTheDocument();
  });

  it("показывает счётчики и неделю", async () => {
    await openSettings();
    const stat = screen.getByText("сделано сегодня").closest("div")!;
    expect(within(stat).getByText("3")).toBeInTheDocument();
    expect(screen.getAllByTestId("week-bar")).toHaveLength(7);
  });

  it("режим подтверждения меняется", async () => {
    vi.mocked(api.patchSettings).mockResolvedValue({ ...settings, confirm_mode: "off" });
    await openSettings();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Подтверждать задачи" }), "off");
    expect(api.patchSettings).toHaveBeenCalledWith({ confirm_mode: "off" });
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Подтверждать задачи" })).toHaveValue("off"));
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
    fireEvent.click(screen.getByRole("switch", { name: /Вечернее закрытие/ }));
    expect(api.patchSettings).toHaveBeenCalledWith({ evening_enabled: false });
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Время вечерней сводки" })).toBeDisabled());
  });

  it("порог «застряло» и выходные", async () => {
    vi.mocked(api.patchSettings).mockResolvedValue({ ...settings, stuck_days: 30 });
    await openSettings();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: /Застряло/ }), "30");
    expect(api.patchSettings).toHaveBeenCalledWith({ stuck_days: 30 });
    fireEvent.click(screen.getByRole("switch", { name: /Сводки в выходные/ }));
    expect(api.patchSettings).toHaveBeenLastCalledWith({ weekend_rituals: true });
  });
});
