import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import * as api from "./api";
import * as tg from "./telegram";

vi.mock("./api", async (orig) => {
  const real = await orig<typeof import("./api")>();
  return { ...real, getInbox: vi.fn(), scheduleTask: vi.fn(), deleteTask: vi.fn() };
});
vi.mock("./telegram", () => ({
  confirm: vi.fn(),
  alertUser: vi.fn(),
  haptic: vi.fn(),
  initData: () => "",
  ready: () => undefined,
}));

const items = [
  { key: "aaaa1111", title: "Статья про индексы", note: "https://t.me/c/1/2", created: "2026-10-26T10:00:00Z" },
  { key: "bbbb2222", title: "Посмотреть фильм", note: "", created: "2026-10-25T10:00:00Z" },
];

beforeEach(() => {
  vi.mocked(api.getInbox).mockResolvedValue({ count: items.length, items });
  vi.mocked(api.scheduleTask).mockResolvedValue({ ok: true, date: "2026-10-26" });
  vi.mocked(api.deleteTask).mockResolvedValue({ ok: true });
});

describe("Входящие", () => {
  it("показывает элементы и счётчик", async () => {
    render(<App />);
    expect(await screen.findByText("Статья про индексы")).toBeInTheDocument();
    expect(screen.getByText("Посмотреть фильм")).toBeInTheDocument();
    expect(screen.getByRole("heading")).toHaveTextContent("Входящие · 2");
  });

  it("«Сегодня» назначает день и убирает элемент из списка", async () => {
    render(<App />);
    await screen.findByText("Статья про индексы");
    await userEvent.click(screen.getAllByRole("button", { name: "Сегодня" })[0]);

    expect(api.scheduleTask).toHaveBeenCalledWith("aaaa1111", "today");
    await waitFor(() => expect(screen.queryByText("Статья про индексы")).not.toBeInTheDocument());
    expect(screen.getByRole("heading")).toHaveTextContent("Входящие · 1");
  });

  it("двойной тап не шлёт два запроса", async () => {
    let release: () => void = () => undefined;
    vi.mocked(api.scheduleTask).mockReturnValue(
      new Promise((resolve) => { release = () => resolve({ ok: true, date: "x" }); }),
    );
    render(<App />);
    await screen.findByText("Статья про индексы");
    const button = screen.getAllByRole("button", { name: "Завтра" })[0];
    await userEvent.click(button);
    await userEvent.click(button);
    release();

    expect(api.scheduleTask).toHaveBeenCalledTimes(1);
  });

  it("удаление спрашивает подтверждение и без него ничего не удаляет", async () => {
    vi.mocked(tg.confirm).mockResolvedValue(false);
    render(<App />);
    await screen.findByText("Статья про индексы");
    await userEvent.click(screen.getAllByRole("button", { name: "Удалить" })[0]);

    expect(tg.confirm).toHaveBeenCalled();
    expect(api.deleteTask).not.toHaveBeenCalled();
    expect(screen.getByText("Статья про индексы")).toBeInTheDocument();
  });

  it("при ошибке элемент возвращается и человек видит причину", async () => {
    vi.mocked(api.scheduleTask).mockRejectedValue(new api.ApiError(502, "MS To Do не ответил"));
    render(<App />);
    await screen.findByText("Статья про индексы");
    await userEvent.click(screen.getAllByRole("button", { name: "Сегодня" })[0]);

    await waitFor(() => expect(tg.alertUser).toHaveBeenCalledWith("MS To Do не ответил"));
    expect(screen.getByText("Статья про индексы")).toBeInTheDocument();
  });

  it("выбор даты не переносит задачу, пока не нажато «Перенести»", async () => {
    render(<App />);
    await screen.findByText("Статья про индексы");
    await userEvent.click(screen.getAllByRole("button", { name: "Дата" })[0]);

    // iOS при открытии пикера сам подставляет сегодня и шлёт change.
    const input = screen.getByLabelText("Выбрать дату");
    fireEvent.change(input, { target: { value: "2026-10-26" } });
    fireEvent.change(input, { target: { value: "2026-11-03" } });
    expect(api.scheduleTask).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Перенести" }));
    expect(api.scheduleTask).toHaveBeenCalledWith("aaaa1111", "2026-11-03");
  });

  it("отмена выбора даты оставляет задачу во входящих", async () => {
    render(<App />);
    await screen.findByText("Статья про индексы");
    await userEvent.click(screen.getAllByRole("button", { name: "Дата" })[0]);
    fireEvent.change(screen.getByLabelText("Выбрать дату"), { target: { value: "2026-11-03" } });
    await userEvent.click(screen.getByRole("button", { name: "Отмена" }));

    expect(api.scheduleTask).not.toHaveBeenCalled();
    expect(screen.getByText("Статья про индексы")).toBeInTheDocument();
    expect(screen.queryByLabelText("Выбрать дату")).not.toBeInTheDocument();
  });

  it("пустые входящие", async () => {
    vi.mocked(api.getInbox).mockResolvedValue({ count: 0, items: [] });
    render(<App />);
    expect(await screen.findByText(/всё разобрано/i)).toBeInTheDocument();
  });

  it("просроченная подпись — просьба переоткрыть, а не пустой экран", async () => {
    vi.mocked(api.getInbox).mockRejectedValue(new api.ApiError(401, "подпись просрочена"));
    render(<App />);
    expect(await screen.findByText(/открой мини-апп заново/i)).toBeInTheDocument();
  });
});
