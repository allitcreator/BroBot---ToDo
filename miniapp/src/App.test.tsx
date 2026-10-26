import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import * as api from "./api";
import * as tg from "./telegram";

vi.mock("./api", async (orig) => {
  const real = await orig<typeof import("./api")>();
  return {
    ...real,
    getInbox: vi.fn(), scheduleTask: vi.fn(), deleteTask: vi.fn(),
    getToday: vi.fn(), getWeek: vi.fn(), getSummary: vi.fn(), completeTask: vi.fn(), moveTasks: vi.fn(),
    getSettings: vi.fn(),
  };
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
  vi.mocked(api.getSettings).mockResolvedValue({ confirm_mode: "all", research_enabled: true, projects_enabled: true });
  vi.mocked(api.getSummary).mockResolvedValue({ inbox: 2, today: 1, overdue: 1 });
  vi.mocked(api.getInbox).mockResolvedValue({ count: items.length, items });
  vi.mocked(api.scheduleTask).mockResolvedValue({ ok: true, date: "2026-10-26" });
  vi.mocked(api.deleteTask).mockResolvedValue({ ok: true });
});

describe("Входящие", () => {
  it("показывает элементы и счётчик", async () => {
    render(<App />);
    expect(await screen.findByText("Статья про индексы")).toBeInTheDocument();
    expect(screen.getByText("Посмотреть фильм")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Входящие · 2");
  });

  it("«Сегодня» назначает день и убирает элемент из списка", async () => {
    render(<App />);
    await screen.findByText("Статья про индексы");
    await userEvent.click(screen.getAllByRole("button", { name: "Сегодня" })[0]);

    expect(api.scheduleTask).toHaveBeenCalledWith("aaaa1111", "today");
    await waitFor(() => expect(screen.queryByText("Статья про индексы")).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Входящие · 1");
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


const task = (key: string, title: string, date: string, extra: Partial<api.Task> = {}): api.Task => ({
  key, title, note: "", date, reminder: false, calendar: false, ...extra,
});

function iso(offset: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

describe("Вкладки", () => {
  it("показывают счётчики и переключают экраны", async () => {
    vi.mocked(api.getToday).mockResolvedValue({ overdue: [], today: [task("t1", "Позвонить", iso(0))] });
    render(<App />);
    expect(await screen.findByRole("tab", { name: /Входящие\s*2/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Сегодня\s*2/ })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: /Сегодня/ }));
    expect(await screen.findByText("Позвонить")).toBeInTheDocument();
  });
});

describe("Сегодня", () => {
  beforeEach(() => {
    vi.mocked(api.getToday).mockResolvedValue({
      overdue: [task("o1", "Просрочка", iso(-2))],
      today: [task("t1", "Позвонить", iso(0), { reminder: true })],
    });
    vi.mocked(api.completeTask).mockResolvedValue({ ok: true });
    vi.mocked(api.moveTasks).mockResolvedValue({ moved: 1, total: 1, failed: [] });
  });

  async function open() {
    render(<App />);
    await userEvent.click(await screen.findByRole("tab", { name: /Сегодня/ }));
    await screen.findByText("Позвонить");
  }

  it("просроченные отдельным блоком, у задачи значок напоминания", async () => {
    await open();
    expect(screen.getByText(/Просрочено · 1/)).toBeInTheDocument();
    expect(screen.getByText("Просрочка")).toBeInTheDocument();
    expect(screen.getByText(/🔔/)).toBeInTheDocument();
  });

  it("«Готово» закрывает задачу и обновляет счётчики", async () => {
    await open();
    const callsBefore = vi.mocked(api.getSummary).mock.calls.length;
    const card = screen.getByText("Позвонить").closest("li")!;
    await userEvent.click(within(card).getByRole("button", { name: "Готово" }));

    expect(api.completeTask).toHaveBeenCalledWith("t1");
    await waitFor(() => expect(screen.queryByText("Позвонить")).not.toBeInTheDocument());
    expect(vi.mocked(api.getSummary).mock.calls.length).toBeGreaterThan(callsBefore);
  });

  it("«Все на сегодня» спрашивает и переносит просроченные", async () => {
    vi.mocked(tg.confirm).mockResolvedValue(true);
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Все на сегодня" }));
    expect(api.moveTasks).toHaveBeenCalledWith("overdue", "today");
  });

  it("частичный перенос сообщает, что не удалось", async () => {
    vi.mocked(tg.confirm).mockResolvedValue(true);
    vi.mocked(api.moveTasks).mockResolvedValue({ moved: 1, total: 2, failed: ["Вторая"] });
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Перенести все на завтра" }));
    await waitFor(() => expect(tg.alertUser).toHaveBeenCalledWith(expect.stringContaining("Перенесено 1 из 2")));
  });
});

describe("Неделя", () => {
  it("задачи по дням, перенос перекладывает карточку в другой день", async () => {
    vi.mocked(api.getWeek).mockResolvedValue({
      days: [0, 1, 2, 3, 4, 5, 6].map((i) => ({ date: iso(i), items: i === 1 ? [task("w1", "Отчёт", iso(1))] : [] })),
    });
    vi.mocked(api.scheduleTask).mockResolvedValue({ ok: true, date: iso(3) });
    render(<App />);
    await userEvent.click(await screen.findByRole("tab", { name: /Неделя/ }));
    await screen.findByText("Отчёт");
    expect(screen.getByText("Завтра")).toBeInTheDocument();

    const card = screen.getByText("Отчёт").closest("li")!;
    await userEvent.click(within(card).getByRole("button", { name: "Дата" }));
    fireEvent.change(within(card).getByLabelText("Выбрать дату"), { target: { value: iso(3) } });
    await userEvent.click(within(card).getByRole("button", { name: "Перенести" }));

    expect(api.scheduleTask).toHaveBeenCalledWith("w1", iso(3));
    const sections = screen.getAllByRole("heading", { level: 2 });
    const target = sections[3].closest("section")!;
    expect(within(target).getByText("Отчёт")).toBeInTheDocument();
  });
});
