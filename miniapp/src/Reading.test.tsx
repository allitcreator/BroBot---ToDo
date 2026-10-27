import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import * as api from "./api";
import * as tg from "./telegram";

vi.mock("./api", async (orig) => {
  const real = await orig<typeof import("./api")>();
  return {
    ...real,
    getSummary: vi.fn(), getSettings: vi.fn(), getToday: vi.fn(), getStats: vi.fn(),
    getReading: vi.fn(), getMaterial: vi.fn(), setMaterialRead: vi.fn(), setMaterialPinned: vi.fn(), deleteMaterial: vi.fn(),
  };
});
vi.mock("./telegram", () => ({
  confirm: vi.fn(), alertUser: vi.fn(), haptic: vi.fn(), initData: () => "", ready: () => undefined, openLink: vi.fn(),
}));

const meta = (id: number, title: string, read = false): api.MaterialMeta => ({
  id, title, source: "personal/a.md", created_at: "2026-10-27T10:00:00Z", pinned: false, read, chars: 2400,
});
const full = (id: number, title: string, markdown: string): api.Material => ({ ...meta(id, title), markdown });

const settings: api.Settings = {
  confirm_mode: "all", research_enabled: true, projects_enabled: true,
  morning_enabled: true, morning_time: "09:30", evening_enabled: true, evening_time: "20:30",
  stuck_days: 14, weekend_rituals: false,
};

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  vi.mocked(api.getSummary).mockResolvedValue({ inbox: 0, today: 0, overdue: 0, reading: 2 });
  vi.mocked(api.getSettings).mockResolvedValue(settings);
  vi.mocked(api.getToday).mockResolvedValue({ overdue: [], today: [] });
  vi.mocked(api.getStats).mockResolvedValue({ completed_today: 0, created_today: 0, open_tasks: 0, overdue: 0, inbox: 0, week: [] });
  vi.mocked(api.getReading).mockResolvedValue({ unread: 2, items: [meta(1, "Первый"), meta(2, "Второй"), meta(3, "Старый", true)] });
  vi.mocked(api.getMaterial).mockImplementation(async (id) => full(id, `Материал ${id}`, `# Заголовок ${id}\n\nТекст ${id}`));
  vi.mocked(api.setMaterialRead).mockResolvedValue({ ok: true });
  vi.mocked(api.setMaterialPinned).mockResolvedValue({ ok: true });
  vi.mocked(api.deleteMaterial).mockResolvedValue({ ok: true });
  vi.mocked(tg.confirm).mockResolvedValue(true);
});

async function openReading() {
  render(<App />);
  await userEvent.click(await screen.findByRole("tab", { name: /Чтение/ }));
}

describe("Чтение", () => {
  it("счётчик на вкладке и список", async () => {
    await openReading();
    expect(screen.getByRole("tab", { name: /Чтение/ })).toHaveTextContent("2");
    expect(await screen.findByText("2 не прочитано")).toBeInTheDocument();
    expect(screen.getByText("Старый").closest("button")).toHaveClass("read");
  });

  it("материал открывается, кэшируется и отмечается прочитанным", async () => {
    await openReading();
    await userEvent.click(await screen.findByText("Первый"));
    expect(await screen.findByRole("heading", { name: "Заголовок 1" })).toBeInTheDocument();
    expect(window.localStorage.getItem("reading:cache")).toContain("Текст 1");
    await userEvent.click(screen.getByRole("button", { name: "Прочитано" }));
    expect(api.setMaterialRead).toHaveBeenCalledWith(1, true);
    expect(await screen.findByRole("button", { name: "Не прочитано" })).toBeInTheDocument();
  });

  it("закрепить", async () => {
    await openReading();
    await userEvent.click(await screen.findByText("Первый"));
    await userEvent.click(await screen.findByRole("button", { name: "Закрепить" }));
    expect(api.setMaterialPinned).toHaveBeenCalledWith(1, true);
    expect(await screen.findByRole("button", { name: "Открепить" })).toBeInTheDocument();
  });

  it("удаление чистит кэш и возвращает к списку", async () => {
    await openReading();
    await userEvent.click(await screen.findByText("Первый"));
    await screen.findByRole("heading", { name: "Заголовок 1" });
    vi.mocked(api.getReading).mockResolvedValue({ unread: 1, items: [meta(2, "Второй")] });
    await userEvent.click(screen.getByRole("button", { name: "Удалить" }));
    expect(api.deleteMaterial).toHaveBeenCalledWith(1);
    await waitFor(() => expect(window.localStorage.getItem("reading:cache")).not.toContain("Текст 1"));
    expect(await screen.findByText("Второй")).toBeInTheDocument();
    expect(screen.queryByText("Первый")).not.toBeInTheDocument();
  });

  it("без сети материал читается из кэша", async () => {
    await openReading();
    await userEvent.click(await screen.findByText("Первый"));
    await screen.findByRole("heading", { name: "Заголовок 1" });
    await userEvent.click(screen.getByRole("button", { name: /Назад/ }));

    vi.mocked(api.getReading).mockRejectedValue(new Error("Failed to fetch"));
    vi.mocked(api.getMaterial).mockRejectedValue(new Error("Failed to fetch"));
    await userEvent.click(screen.getByRole("button", { name: "Обновить" }));
    expect(await screen.findByText(/без сети/)).toBeInTheDocument();
    await userEvent.click(screen.getByText("Первый"));
    expect(await screen.findByText("Текст 1")).toBeInTheDocument();
    expect(screen.getByText(/Без сети/)).toBeInTheDocument();
  });

  it("не открытый материал без сети — сообщение, не падение", async () => {
    await openReading();
    await screen.findByText("Второй");
    vi.mocked(api.getMaterial).mockRejectedValue(new Error("Failed to fetch"));
    await userEvent.click(screen.getByText("Второй"));
    await waitFor(() => expect(tg.alertUser).toHaveBeenCalledWith("Failed to fetch"));
  });

  it("?read=<id> открывает материал сразу", async () => {
    window.history.replaceState(null, "", "/?read=2");
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Заголовок 2" })).toBeInTheDocument();
    expect(api.getMaterial).toHaveBeenCalledWith(2);
  });

  it("вредный текст материала не исполняется", async () => {
    vi.mocked(api.getMaterial).mockResolvedValue(full(1, "Х", '<img src=x onerror="alert(1)"> [a](javascript:alert(1))'));
    window.history.replaceState(null, "", "/?read=1");
    const { container } = render(<App />);
    await screen.findByRole("heading", { name: "Х" });
    expect(container.querySelector("img, script, a[href^='javascript']")).toBeNull();
  });

  it("размер шрифта из ⚙️ применяется в чтении", async () => {
    render(<App />);
    await userEvent.click(await screen.findByRole("tab", { name: /Ещё/ }));
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Размер шрифта" }), "20");
    expect(window.localStorage.getItem("reading:font")).toBe("20");
    await userEvent.click(screen.getByRole("tab", { name: /Чтение/ }));
    await userEvent.click(await screen.findByText("Первый"));
    const article = (await screen.findByRole("heading", { name: "Материал 1" })).closest("article")!;
    expect(article).toHaveStyle({ fontSize: "20px" });
  });

  it("?read=<id> потребляется один раз: назад, другая вкладка, обратно — список", async () => {
    window.history.replaceState(null, "", "/?read=2");
    render(<App />);
    await screen.findByRole("heading", { name: "Заголовок 2" });
    await userEvent.click(screen.getByRole("button", { name: /Назад/ }));
    await userEvent.click(screen.getByRole("tab", { name: /Ещё/ }));
    vi.mocked(api.getMaterial).mockClear();
    await userEvent.click(screen.getByRole("tab", { name: /Чтение/ }));
    expect(await screen.findByText("Первый")).toBeInTheDocument();
    expect(api.getMaterial).not.toHaveBeenCalled();
    expect(window.location.search).not.toContain("read=");
  });

  it("404 при открытии: кэш забыт, сообщение, список обновлён", async () => {
    await openReading();
    await userEvent.click(await screen.findByText("Первый"));
    await screen.findByRole("heading", { name: "Заголовок 1" });
    await userEvent.click(screen.getByRole("button", { name: /Назад/ }));
    vi.mocked(api.getMaterial).mockRejectedValue(new api.ApiError(404, "Нет такого материала"));
    await userEvent.click(await screen.findByText("Первый"));
    await waitFor(() => expect(tg.alertUser).toHaveBeenCalledWith("Материал удалён"));
    expect(window.localStorage.getItem("reading:cache")).not.toContain("Текст 1");
  });

  it("ошибка сервера (5xx) не подменяется кэшем", async () => {
    await openReading();
    await userEvent.click(await screen.findByText("Первый"));
    await screen.findByRole("heading", { name: "Заголовок 1" });
    await userEvent.click(screen.getByRole("button", { name: /Назад/ }));
    vi.mocked(api.getMaterial).mockRejectedValue(new api.ApiError(502, "Ошибка 502"));
    await userEvent.click(await screen.findByText("Первый"));
    await waitFor(() => expect(tg.alertUser).toHaveBeenCalledWith("Ошибка 502"));
    expect(screen.queryByText(/Без сети/)).not.toBeInTheDocument();
  });

  it.each([401, 403])("%i при загрузке списка чистит кэш и не показывает старое", async (status) => {
    await openReading();
    await userEvent.click(await screen.findByText("Первый"));
    await screen.findByRole("heading", { name: "Заголовок 1" });
    await userEvent.click(screen.getByRole("button", { name: /Назад/ }));
    vi.mocked(api.getReading).mockRejectedValue(new api.ApiError(status, "нет доступа"));
    await userEvent.click(screen.getByRole("button", { name: "Обновить" }));
    await waitFor(() => expect(window.localStorage.getItem("reading:cache")).toBeNull());
    expect(window.localStorage.getItem("reading:list")).toBeNull();
    expect(screen.queryByText("Первый")).not.toBeInTheDocument();
  });

  it("после успешного списка из кэша уходят материалы, которых на сервере нет", async () => {
    await openReading();
    await userEvent.click(await screen.findByText("Первый"));
    await screen.findByRole("heading", { name: "Заголовок 1" });
    await userEvent.click(screen.getByRole("button", { name: /Назад/ }));
    vi.mocked(api.getReading).mockResolvedValue({ unread: 1, items: [meta(2, "Второй")] });
    await userEvent.click(screen.getByRole("button", { name: "Обновить" }));
    await screen.findByText("Второй");
    expect(window.localStorage.getItem("reading:cache")).not.toContain("Текст 1");
  });

  it("при переполнении localStorage старые материалы вытесняются", async () => {
    const real = window.localStorage.setItem.bind(window.localStorage);
    const spy = vi.spyOn(window.localStorage, "setItem").mockImplementation((k: string, v: string) => {
      if (k === "reading:cache" && v.length > 2500) throw new DOMException("quota", "QuotaExceededError");
      real(k, v);
    });
    const { cacheMaterial, cachedMaterials } = await import("./reading");
    const big = (id: number) => ({ ...full(id, `М${id}`, "я".repeat(900)), read: false });
    cacheMaterial(big(1));
    cacheMaterial(big(2));
    cacheMaterial(big(3));
    expect(cachedMaterials().map((m) => m.id)).toEqual([3, 2]);
    spy.mockRestore();
  });
});

