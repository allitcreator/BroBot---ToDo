import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import * as api from "./api";

vi.mock("./markdown", () => ({
  Markdown: () => {
    throw new Error("render failed");
  },
}));
vi.mock("./api", async (orig) => {
  const real = await orig<typeof import("./api")>();
  return { ...real, getSummary: vi.fn(), getSettings: vi.fn(), getToday: vi.fn(), getReading: vi.fn(), getMaterial: vi.fn(), deleteMaterial: vi.fn() };
});
vi.mock("./telegram", () => ({
  confirm: vi.fn().mockResolvedValue(true), alertUser: vi.fn(), haptic: vi.fn(), initData: () => "", ready: () => undefined, openLink: vi.fn(),
}));

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/?read=1");
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  vi.mocked(api.getSummary).mockResolvedValue({ inbox: 0, today: 0, overdue: 0, reading: 1 });
  vi.mocked(api.getSettings).mockResolvedValue({
    confirm_mode: "all", research_enabled: true, projects_enabled: true, morning_enabled: true, morning_time: "09:30",
    evening_enabled: true, evening_time: "20:30", stuck_days: 14, weekend_rituals: false,
  });
  vi.mocked(api.getToday).mockResolvedValue({ overdue: [], today: [] });
  vi.mocked(api.getReading).mockResolvedValue({ unread: 1, items: [] });
  vi.mocked(api.getMaterial).mockResolvedValue({
    id: 1, title: "Сломанный", source: "", created_at: "2026-10-27T10:00:00Z", pinned: false, read: false, chars: 10, markdown: "x",
  });
  vi.mocked(api.deleteMaterial).mockResolvedValue({ ok: true });
});

describe("Чтение: сбой рендера", () => {
  it("вместо белого экрана — сообщение, а «Удалить» остаётся доступным", async () => {
    render(<App />);
    expect(await screen.findByText("Не удалось отрисовать")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Удалить" }));
    expect(api.deleteMaterial).toHaveBeenCalledWith(1);
  });
});
