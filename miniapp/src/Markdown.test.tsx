import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Markdown, renderBlocks } from "./markdown";
import { ErrorBoundary } from "./components/ErrorBoundary";
import * as tg from "./telegram";

vi.mock("./telegram", () => ({ openLink: vi.fn() }));

beforeEach(() => vi.mocked(tg.openLink).mockClear());

const html = (md: string) => render(<Markdown text={md} />).container;

describe("рендер markdown: безопасность", () => {
  it("<script> остаётся текстом, элемента нет", () => {
    const c = html("до <script>window.__pwned = 1</script> после");
    expect(c.querySelector("script")).toBeNull();
    expect(c.textContent).toContain("<script>window.__pwned = 1</script>");
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it("<img onerror> остаётся текстом", () => {
    const c = html('<img src=x onerror="alert(1)">\n\n![x](https://e.com/a.png)');
    expect(c.querySelector("img")).toBeNull();
    expect(c.querySelector("[onerror]")).toBeNull();
    expect(c.textContent).toContain('<img src=x onerror="alert(1)">');
  });

  it.each([
    "[клик](javascript:alert(1))",
    "[клик](JaVaScRiPt:alert(1))",
    "[клик](data:text/html;base64,PHNjcmlwdD4=)",
    "[клик](vbscript:x)",
    "[клик](java\tscript:alert(1))",
    "[клик](//evil.example/x)",
    "[клик](/относительная)",
  ])("небезопасная ссылка %s не получает href", (md) => {
    const c = html(md);
    expect(c.querySelector("a")).toBeNull();
    expect(c.textContent).toContain("клик");
  });

  it("html-теги в заголовке, списке, таблице и цитате не становятся элементами", () => {
    const c = html("# <b onclick=1>H</b>\n\n- <iframe src=x></iframe>\n\n| a |\n|---|\n| <svg onload=1> |\n\n> <script>1</script>");
    for (const tag of ["b", "iframe", "svg", "script"]) expect(c.querySelector(tag)).toBeNull();
    expect(c.querySelector("[onclick],[onload]")).toBeNull();
  });

  it("http(s)-ссылка открывается через Telegram openLink, а не переходом", async () => {
    html("[сайт](https://example.com/a?b=1) и https://example.org/x.");
    const links = screen.getAllByRole("link");
    expect(links.map((l) => l.getAttribute("href"))).toEqual(["https://example.com/a?b=1", "https://example.org/x"]);
    await userEvent.click(links[0]);
    expect(tg.openLink).toHaveBeenCalledWith("https://example.com/a?b=1");
  });
});

describe("рендер markdown: разметка", () => {
  it("заголовки, жирный, курсив, код", () => {
    const c = html("# Тема\n\n## Раздел\n\nТекст **жирный** и *курсив* и `код`.");
    expect(c.querySelector("h2")?.textContent).toBe("Тема");
    expect(c.querySelector("h3")?.textContent).toBe("Раздел");
    expect(c.querySelector("strong")?.textContent).toBe("жирный");
    expect(c.querySelector("em")?.textContent).toBe("курсив");
    expect(c.querySelector("p code")?.textContent).toBe("код");
  });

  it("блок кода не разбирается", () => {
    const c = html("```\n**не жирный** <b>\n```");
    expect(c.querySelector("pre code")?.textContent).toBe("**не жирный** <b>");
    expect(c.querySelector("strong")).toBeNull();
  });

  it("списки с вложенностью и нумерованные", () => {
    const c = html("- один\n  - вложенный\n- два\n\n1. первый\n2. второй");
    expect(c.querySelectorAll("ul").length).toBe(2);
    expect(c.querySelector("ul ul li")?.textContent).toBe("вложенный");
    expect(c.querySelectorAll("ol li").length).toBe(2);
  });

  it("таблица", () => {
    const c = html("| Имя | Срок |\n|---|---|\n| Задача | завтра |\n| Другая | пн |");
    expect([...c.querySelectorAll("th")].map((e) => e.textContent)).toEqual(["Имя", "Срок"]);
    expect(c.querySelectorAll("tbody tr").length).toBe(2);
  });

  it("snake_case не превращается в курсив", () => {
    const c = html("поле some_field_name и other_name_x");
    expect(c.querySelector("em")).toBeNull();
  });
});

describe("рендер markdown: устойчивость к злому вводу", () => {
  const fast = (name: string, md: string) =>
    it(`${name}: разбор быстрее 100 мс`, () => {
      const t = performance.now();
      renderBlocks(md);
      expect(performance.now() - t).toBeLessThan(100);
    });

  fast("5000 обратных кавычек", "`".repeat(5000));
  fast("5000 обратных кавычек и текст", "`".repeat(5000) + " x");
  fast("заголовок с 5000 пробелов", "#" + " ".repeat(5000) + "x");
  fast("заголовок из пробелов и решёток", "# " + " #".repeat(5000));
  fast("таблица с 50 000 пробелов", "| a |" + " ".repeat(50000) + "\n|---|" + " ".repeat(50000) + "\n| b |");
  fast("5000 звёздочек", "**".repeat(5000));
  fast("5000 открывающих скобок ссылки", "[".repeat(5000) + "](");
  fast("5000 подчёркиваний", "_ ".repeat(5000));
  fast("разделитель с пробелами", "-" + " ".repeat(20000) + "x");
  fast("20000 маркеров цитаты", ">".repeat(20000) + " x");
  fast("вложенные отступы списка", Array.from({ length: 3000 }, (_, i) => " ".repeat(i) + "- x").join("\n"));

  it("цитата вложена не глубже 10 уровней и не роняет рендер", () => {
    const { container } = render(<Markdown text={">".repeat(20000) + " глубоко"} />);
    expect(container.textContent).toContain("глубоко");
    expect(container.querySelectorAll("blockquote").length).toBeLessThanOrEqual(10);
  });

  it("список вложен не глубже 10 уровней", () => {
    const md = Array.from({ length: 3000 }, (_, i) => " ".repeat(i * 2) + "- x").join("\n");
    const { container } = render(<Markdown text={md} />);
    expect(container.querySelectorAll("li").length).toBe(3000);
    expect(container.querySelectorAll("ul").length).toBeLessThanOrEqual(11);
  });

  it("таблица шире 30 колонок остаётся текстом", () => {
    const row = "|" + Array.from({ length: 40 }, (_, i) => `c${i}`).join("|") + "|";
    const sep = "|" + Array.from({ length: 40 }, () => "---").join("|") + "|";
    const c = html(`${row}\n${sep}\n${row}`);
    expect(c.querySelector("table")).toBeNull();
    expect(c.textContent).toContain("c39");
  });

  it("строк таблицы больше 500 — остаток текстом", () => {
    const rows = Array.from({ length: 700 }, (_, i) => `| r${i} |`).join("\n");
    const c = html(`| h |\n|---|\n${rows}`);
    expect(c.querySelectorAll("tbody tr").length).toBe(500);
    expect(c.textContent).toContain("r699");
  });

  it("абзац длиннее 20 000 символов выводится текстом без разметки", () => {
    const c = html("**жирный** " + "а".repeat(20000));
    expect(c.querySelector("strong")).toBeNull();
    expect(c.textContent).toContain("**жирный**");
  });

  it("ErrorBoundary показывает запасной текст вместо белого экрана", () => {
    const Boom = () => {
      throw new Error("boom");
    };
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(<ErrorBoundary><Boom /></ErrorBoundary>);
    expect(screen.getByText("Не удалось отрисовать")).toBeInTheDocument();
  });
});

describe("рендер markdown: разбор", () => {
  it("отступы списка 1→0 и 0→4→2 не теряют пункты", () => {
    const a = html("  - a\n- b");
    expect([...a.querySelectorAll("li")].map((e) => e.textContent)).toEqual(expect.arrayContaining(["a", "b"]));
    const b = html("- a\n    - b\n  - c");
    const text = [...b.querySelectorAll("li")].map((e) => e.textContent ?? "").join("|");
    for (const t of ["a", "b", "c"]) expect(text).toContain(t);
  });

  it("нумерованный список через пустую строку — один список, start из первого числа", () => {
    const c = html("1. a\n\n2. b");
    expect(c.querySelectorAll("ol").length).toBe(1);
    expect(c.querySelectorAll("ol li").length).toBe(2);
    expect(html("3. x\n4. y").querySelector("ol")).toHaveAttribute("start", "3");
  });

  it("__init__.py не становится жирным", () => {
    const c = html("файл __init__.py лежит рядом");
    expect(c.querySelector("strong")).toBeNull();
    expect(c.textContent).toContain("__init__.py");
  });

  it("ссылка со скобками в URL", () => {
    html("[вики](https://en.wikipedia.org/wiki/Foo_(bar)) и дальше");
    expect(screen.getByRole("link")).toHaveAttribute("href", "https://en.wikipedia.org/wiki/Foo_(bar)");
  });

  it("однострочный ```текст``` — не fence", () => {
    const c = html("```текст``` и дальше\n\nабзац");
    expect(c.querySelector("pre")).toBeNull();
    expect(c.textContent).toContain("абзац");
    expect(c.querySelector("code")?.textContent).toBe("текст");
  });

  it("заголовок: C# остаётся, закрывающие решётки снимаются", () => {
    expect(html("# C#").querySelector("h2")?.textContent).toBe("C#");
    expect(html("## Title ##").querySelector("h3")?.textContent).toBe("Title");
  });
});
