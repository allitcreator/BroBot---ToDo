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
    getInbox: vi.fn(), getToday: vi.fn(), getWeek: vi.fn(), getSummary: vi.fn(), getSettings: vi.fn(),
    getMorning: vi.fn(), getPlan: vi.fn(), ritualDone: vi.fn(), savePlan: vi.fn(), completeTask: vi.fn(),
  };
});
vi.mock("./telegram", () => ({
  confirm: vi.fn(),
  alertUser: vi.fn(),
  haptic: vi.fn(),
  initData: () => "",
  ready: () => undefined,
  openLink: vi.fn(),
}));

const t = (key: string, title: string, extra: Partial<api.RitualTask> = {}): api.RitualTask => ({
  key, title, note: "", date: null, reminder: false, calendar: false, order: null, executor: null, advice: null, ...extra,
});

const plan: api.Plan = {
  events: [],
  today: [t("a", "Написать отчёт"), t("b", "Позвонить в сервис", { time: "15:00" }), t("c", "Купить корм")],
  norm: null,
};

const pick = (title: string) => screen.getByRole("button", { name: new RegExp(`^Порядок: .*${title}`) });
const who = (title: string) => screen.getByRole("button", { name: new RegExp(`^Исполнитель .*${title}`) });
const num = (title: string) => within(pick(title)).getByTestId("num").textContent;

beforeEach(() => {
  vi.mocked(api.getSettings).mockResolvedValue({
    confirm_mode: "all", research_enabled: true, projects_enabled: true,
    morning_enabled: true, morning_time: "09:30", evening_enabled: true, evening_time: "20:30",
    stuck_days: 14, weekend_rituals: false,
  });
  vi.mocked(api.getSummary).mockResolvedValue({ inbox: 0, today: 3, overdue: 0 });
  vi.mocked(api.getMorning).mockResolvedValue({ overdue: [], stuck: [], inbox: [] });
  vi.mocked(api.getPlan).mockResolvedValue(structuredClone(plan));
  vi.mocked(api.getToday).mockResolvedValue({ overdue: [], today: [] });
  vi.mocked(api.ritualDone).mockResolvedValue({ ok: true });
  vi.mocked(api.savePlan).mockResolvedValue({ saved: 1, total: 1, failed: [] });
});

afterEach(() => window.history.replaceState(null, "", "/"));

describe("План дня: порядок тапами", () => {
  beforeEach(() => window.history.replaceState(null, "", "/?ritual=morning"));

  it("тап даёт следующий номер, повторный снимает и сдвигает остальные", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "План дня" });
    await userEvent.click(pick("Купить корм"));
    await userEvent.click(pick("Написать отчёт"));
    await userEvent.click(pick("Позвонить в сервис"));
    expect([num("Купить корм"), num("Написать отчёт"), num("Позвонить в сервис")]).toEqual(["1", "2", "3"]);

    await userEvent.click(pick("Купить корм"));
    expect([num("Купить корм"), num("Написать отчёт"), num("Позвонить в сервис")]).toEqual(["", "1", "2"]);

    await userEvent.click(screen.getByRole("button", { name: "Сбросить" }));
    expect([num("Написать отчёт"), num("Позвонить в сервис")]).toEqual(["", ""]);
  });

  it("исполнитель по кругу, рекомендации у агента; «Начать день» сохраняет одним запросом", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "План дня" });
    await userEvent.click(pick("Позвонить в сервис"));
    await userEvent.click(who("Написать отчёт")); // сам
    expect(who("Написать отчёт")).toHaveTextContent("👤");
    await userEvent.click(who("Написать отчёт")); // агент
    expect(who("Написать отчёт")).toHaveTextContent("🤖");

    await userEvent.click(screen.getByRole("button", { name: /Рекомендации: Написать отчёт/ }));
    const sheet = screen.getByRole("dialog", { name: /Рекомендации/ });
    await userEvent.type(within(sheet).getByRole("textbox"), "собери цифры за неделю");
    await userEvent.click(within(sheet).getByRole("button", { name: "Сохранить" }));
    expect(screen.getByText("собери цифры за неделю")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Начать день" }));
    expect(api.savePlan).toHaveBeenCalledTimes(1);
    expect(api.savePlan).toHaveBeenCalledWith({
      order: ["b"],
      meta: { a: { executor: "agent", advice: "собери цифры за неделю" } },
    });
    expect(api.ritualDone).toHaveBeenCalledWith("morning");
  });

  it("без изменений ничего не сохраняет", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "План дня" });
    await userEvent.click(screen.getByRole("button", { name: "Начать день" }));
    expect(api.savePlan).not.toHaveBeenCalled();
    expect(api.ritualDone).toHaveBeenCalledWith("morning");
  });

  it("номера и исполнители с сервера видны сразу; снятый исполнитель уходит как null", async () => {
    vi.mocked(api.getPlan).mockResolvedValue({
      ...plan,
      today: [t("c", "Купить корм", { order: 1, executor: "self" }), t("a", "Написать отчёт", { order: 2, executor: "agent", advice: "черновик" })],
    });
    render(<App />);
    await screen.findByRole("heading", { name: "План дня" });
    expect([num("Купить корм"), num("Написать отчёт")]).toEqual(["1", "2"]);
    expect(screen.getByText("черновик")).toBeInTheDocument();
    await userEvent.click(who("Написать отчёт")); // агент → без отметки
    await userEvent.click(screen.getByRole("button", { name: "Начать день" }));
    expect(api.savePlan).toHaveBeenCalledWith({ meta: { a: { executor: null, advice: null } } });
  });

  it("случайный тап по исполнителю не стирает рекомендации: круг назад к 🤖 их возвращает", async () => {
    vi.mocked(api.getPlan).mockResolvedValue({
      ...plan,
      today: [t("a", "Написать отчёт", { executor: "agent", advice: "черновик письма" })],
    });
    render(<App />);
    await screen.findByRole("heading", { name: "План дня" });
    await userEvent.click(who("Написать отчёт")); // без отметки
    expect(screen.queryByText("черновик письма")).not.toBeInTheDocument();
    await userEvent.click(who("Написать отчёт")); // сам
    await userEvent.click(who("Написать отчёт")); // агент
    expect(screen.getByText("черновик письма")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Начать день" }));
    expect(api.savePlan).not.toHaveBeenCalled();
  });

  it("строка порядка отдаёт состояние через aria-pressed", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: "План дня" });
    expect(pick("Купить корм")).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(pick("Купить корм"));
    expect(pick("Купить корм")).toHaveAttribute("aria-pressed", "true");
  });

  it("ошибка сохранения оставляет в плане", async () => {
    vi.mocked(api.savePlan).mockRejectedValue(new Error("MS To Do не ответил"));
    render(<App />);
    await screen.findByRole("heading", { name: "План дня" });
    await userEvent.click(pick("Купить корм"));
    await userEvent.click(screen.getByRole("button", { name: "Начать день" }));
    await waitFor(() => expect(tg.alertUser).toHaveBeenCalledWith("MS To Do не ответил"));
    expect(api.ritualDone).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: "План дня" })).toBeInTheDocument();
  });

  it("частичный сбой называет задачи и всё равно закрывает разбор", async () => {
    vi.mocked(api.savePlan).mockResolvedValue({ saved: 1, total: 2, failed: ["Купить корм"] });
    render(<App />);
    await screen.findByRole("heading", { name: "План дня" });
    await userEvent.click(pick("Купить корм"));
    await userEvent.click(screen.getByRole("button", { name: "Начать день" }));
    await waitFor(() => expect(tg.alertUser).toHaveBeenCalledWith(expect.stringContaining("Купить корм")));
    expect(api.ritualDone).toHaveBeenCalledWith("morning");
  });
});

describe("Экран «Сегодня»: номер, исполнитель и режим «Порядок»", () => {
  const today = [
    t("c", "Купить корм", { order: 1, executor: "agent", advice: "в Ozon" }),
    t("a", "Написать отчёт", { order: 2 }),
    t("b", "Позвонить в сервис", { executor: "self" }),
  ];

  beforeEach(() => vi.mocked(api.getToday).mockResolvedValue({ overdue: [], today }));

  it("номер и исполнитель видны в строке", async () => {
    render(<App />);
    const row = (await screen.findByText("Купить корм")).closest("li")!;
    expect(row).toHaveTextContent("1 ·");
    expect(row).toHaveTextContent("🤖");
    expect(screen.getByText("Позвонить в сервис").closest("li")).toHaveTextContent("👤");
  });

  it("«Порядок» меняет номера и сохраняет, «Отмена» — без запроса", async () => {
    render(<App />);
    await screen.findByText("Купить корм");
    await userEvent.click(screen.getByRole("button", { name: "Порядок" }));
    await userEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(api.savePlan).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Порядок" }));
    expect(num("Купить корм")).toBe("1");
    await userEvent.click(pick("Купить корм")); // снять: «Написать отчёт» станет первым
    await userEvent.click(pick("Позвонить в сервис"));
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(api.savePlan).toHaveBeenCalledWith({ order: ["a", "b"] });
    await waitFor(() => expect(api.getToday).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: "Порядок" })).toBeInTheDocument();
  });

  it("в режиме «Порядок» список не перезагружается, а устаревшие ключи не уходят", async () => {
    vi.mocked(api.getToday).mockResolvedValue({ overdue: [t("o", "Просрочка", { date: "2026-01-01" })], today });
    render(<App />);
    await screen.findByText("Купить корм");
    await userEvent.click(screen.getByRole("button", { name: "Порядок" }));
    expect(screen.getByRole("button", { name: "Обновить" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Все на сегодня" })).toBeDisabled();
  });
});

describe("Экран «Сегодня»: исполнитель и рекомендации в раскрытой строке", () => {
  const today = [
    t("c", "Купить корм", { executor: "agent", advice: "сначала проверить логи" }),
    t("b", "Позвонить в сервис"),
  ];

  beforeEach(() => vi.mocked(api.getToday).mockResolvedValue({ overdue: [], today }));

  const expand = async (title: string) => {
    await userEvent.click(await screen.findByText(title));
    return screen.getByText(title).closest("li")!;
  };

  it("рекомендации видны только в раскрытой строке, текущий исполнитель отмечен", async () => {
    render(<App />);
    await screen.findByText("Купить корм");
    expect(screen.queryByText(/сначала проверить логи/)).not.toBeInTheDocument();
    const row = await expand("Купить корм");
    expect(within(row).getByText("Рекомендации: сначала проверить логи")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "🤖 Агенту" })).toHaveAttribute("aria-pressed", "true");
    expect(within(row).getByRole("button", { name: "👤 Сам" })).toHaveAttribute("aria-pressed", "false");
  });

  it("«👤 Сам» меняет исполнителя и снимает рекомендации, «🤖 Агенту» ставит агента", async () => {
    render(<App />);
    let row = await expand("Купить корм");
    await userEvent.click(within(row).getByRole("button", { name: "👤 Сам" }));
    expect(api.savePlan).toHaveBeenCalledWith({ meta: { c: { executor: "self", advice: null } } });
    await waitFor(() => expect(api.getToday).toHaveBeenCalledTimes(2));

    vi.mocked(api.savePlan).mockClear();
    row = await expand("Позвонить в сервис");
    await userEvent.click(within(row).getByRole("button", { name: "🤖 Агенту" }));
    expect(api.savePlan).toHaveBeenCalledWith({ meta: { b: { executor: "agent", advice: null } } });
  });

  it("тап по выбранному исполнителю снимает отметку", async () => {
    render(<App />);
    const row = await expand("Купить корм");
    await userEvent.click(within(row).getByRole("button", { name: "🤖 Агенту" }));
    expect(api.savePlan).toHaveBeenCalledWith({ meta: { c: { executor: null, advice: null } } });
  });

  it("«✎ Рекомендации» у агента: лист с текстом, сохранение одним запросом", async () => {
    render(<App />);
    const row = await expand("Купить корм");
    await userEvent.click(within(row).getByRole("button", { name: "✎ Рекомендации" }));
    const sheet = screen.getByRole("dialog", { name: /Рекомендации/ });
    const box = within(sheet).getByRole("textbox");
    expect(box).toHaveValue("сначала проверить логи");
    await userEvent.clear(box);
    await userEvent.type(box, "без коммитов");
    await userEvent.click(within(sheet).getByRole("button", { name: "Сохранить" }));
    expect(api.savePlan).toHaveBeenCalledWith({ meta: { c: { executor: "agent", advice: "без коммитов" } } });
  });

  it("у задачи без агента кнопки рекомендаций нет", async () => {
    render(<App />);
    const row = await expand("Позвонить в сервис");
    expect(within(row).queryByRole("button", { name: "✎ Рекомендации" })).not.toBeInTheDocument();
  });
});

describe("planChanges", () => {
  it("не падает на задаче без правки и не шлёт ключи, которых нет в списке", async () => {
    const { initialPlan, planChanges } = await import("./components/DayPlanner");
    const before = [t("a", "А", { order: 1 }), t("b", "Б")];
    const edit = initialPlan(before);
    edit.order = ["gone", "a", "b"];
    const now = [...before, t("new", "Новая")];
    expect(planChanges(now, edit)).toEqual({ order: ["a", "b"] });
  });
});
