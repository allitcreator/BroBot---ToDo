import { Fragment, ReactNode } from "react";
import { openLink } from "./telegram";

// Свой маленький рендер markdown. Материалы собраны из транскриптов и
// сторонних текстов — это недоверенный ввод, поэтому:
//  * только React-элементы: любой текст экранируется, HTML остаётся текстом,
//    dangerouslySetInnerHTML и сырой HTML не используются;
//  * ссылки — только http(s), открываются через Telegram.WebApp.openLink;
//    javascript:, data: и прочее выводится обычным текстом без href;
//  * картинки не поддерживаются (![alt](url) выводится как текст);
//  * все квантификаторы ограничены, вложенность и размер таблиц — тоже:
//    злой текст не должен вешать или ронять мини-апп (ReDoS, RangeError).

const MAX_TEXT = 20_000; // длиннее — выводим как есть, без разбора
const MAX_DEPTH = 10; // вложенность цитат и списков
const MAX_COLS = 30;
const MAX_ROWS = 500;

export function safeUrl(raw: string): string | null {
  try {
    const url = new URL(raw.trim());
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function Link({ url, children }: { url: string; children: ReactNode }) {
  return (
    <a
      href={url}
      rel="noopener noreferrer"
      onClick={(e) => {
        e.preventDefault();
        openLink(url);
      }}
    >
      {children}
    </a>
  );
}

// Порядок альтернатив важен: код раньше всего, жирный раньше курсива.
// Курсив только через *…*, жирный только **…**: _…_ и __…__ путаются со
// snake_case и __init__.py, а lookbehind роняет старый iOS.
const INLINE_SRC =
  "(`{1,10})([^`\\n]{1,2000}?)\\1(?!`)" + // 1,2: код
  "|\\*\\*(?=\\S)([^\\n]{0,500}?\\S)\\*\\*" + // 3: жирный
  "|\\*(?=\\S)([^*\\n]{0,500}?\\S)\\*" + // 4: курсив
  '|\\[([^\\]\\n]{1,500})\\]\\(([^\\s()]{1,2000}(?:\\([^\\s()]{0,200}\\)[^\\s()]{0,2000})*)(?:\\s+"[^"\\n]{0,300}")?\\)' + // 5,6: ссылка, в URL скобки на один уровень
  "|(https?:\\/\\/[^\\s<>()]{1,2000}[^\\s<>().,;:!?'\"])"; // 7: голый адрес

export function renderInline(text: string): ReactNode[] {
  if (text.length > MAX_TEXT) return [text];
  const out: ReactNode[] = [];
  let last = 0;
  let n = 0;
  // Свой экземпляр на вызов: рекурсия (жирный внутри ссылки) делит lastIndex общего regex.
  const re = new RegExp(INLINE_SRC, "g");
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const key = n++;
    if (m[2] !== undefined) out.push(<code key={key}>{m[2].trim()}</code>);
    else if (m[3] !== undefined) out.push(<strong key={key}>{renderInline(m[3])}</strong>);
    else if (m[4] !== undefined) out.push(<em key={key}>{renderInline(m[4])}</em>);
    else if (m[5] !== undefined) {
      const url = safeUrl(m[6]);
      out.push(url ? <Link key={key} url={url}>{renderInline(m[5])}</Link> : <Fragment key={key}>{m[5]}</Fragment>);
    } else if (m[7] !== undefined) {
      const url = safeUrl(m[7]);
      out.push(url ? <Link key={key} url={url}>{m[7]}</Link> : <Fragment key={key}>{m[7]}</Fragment>);
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

// Блочные признаки — строковые проверки или regex без вложенных квантификаторов.
const HEADING = /^ {0,3}(#{1,6})[ \t]+(.*)$/;
const LIST = /^([ \t]*)([-*+]|\d{1,9}[.)])[ \t]+(.*)$/;
const QUOTE = /^ {0,3}>[ \t]?(.*)$/;

function isFence(line: string): boolean {
  const t = line.trimStart();
  const mark = t.startsWith("```") ? "```" : t.startsWith("~~~") ? "~~~" : "";
  // Строка вида ```текст``` — это код в строке, а не блок.
  return !!mark && !(mark === "```" && t.slice(3).includes("`"));
}

function isHr(line: string): boolean {
  if (line.length - line.trimStart().length > 3) return false;
  const t = line.replace(/[ \t]/g, "");
  return t.length >= 3 && "-*_".includes(t[0]) && [...t].every((c) => c === t[0]);
}

// Закрывающие # снимаем, только если перед ними пробел: «C#» остаётся «C#».
function headingText(raw: string): string {
  let t = raw.trim();
  let j = t.length;
  while (j > 0 && t[j - 1] === "#") j--;
  if (j < t.length && (j === 0 || t[j - 1] === " " || t[j - 1] === "\t")) t = t.slice(0, j).trimEnd();
  return t;
}

function cells(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}

function isTableSep(line: string): boolean {
  if (!line.includes("|") || !line.includes("-")) return false;
  const cs = cells(line);
  return cs.length > 0 && cs.every((c) => /^:?-{1,200}:?$/.test(c));
}

type Item = { indent: number; ordered: boolean; num: number; text: string };
type ListNode = { ordered: boolean; start: number; items: { text: string; sub: ListNode[] }[] };

// Дерево списка по отступам: пункт глубже предыдущего — подсписок, мельче —
// возврат наверх. Любой отступ даёт пункт: ничего не теряется.
function buildList(items: Item[]): ListNode {
  const root: ListNode = { ordered: items[0].ordered, start: items[0].num, items: [] };
  const stack: { node: ListNode; indent: number }[] = [{ node: root, indent: items[0].indent }];
  for (const item of items) {
    while (stack.length > 1 && item.indent < stack[stack.length - 1].indent) stack.pop();
    let top = stack[stack.length - 1];
    if (item.indent > top.indent && top.node.items.length > 0 && stack.length < MAX_DEPTH) {
      const sub: ListNode = { ordered: item.ordered, start: item.num, items: [] };
      top.node.items[top.node.items.length - 1].sub.push(sub);
      top = { node: sub, indent: item.indent };
      stack.push(top);
    }
    top.node.items.push({ text: item.text, sub: [] });
  }
  return root;
}

function renderListNode(node: ListNode, key: string): ReactNode {
  const lis = node.items.map((it, i) => (
    <li key={`${key}-${i}`}>
      {renderInline(it.text)}
      {it.sub.map((s, j) => renderListNode(s, `${key}-${i}-${j}`))}
    </li>
  ));
  return node.ordered ? <ol key={key} start={node.start}>{lis}</ol> : <ul key={key}>{lis}</ul>;
}

export function renderBlocks(src: string, prefix = "b", depth = 0): ReactNode[] {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const out: ReactNode[] = [];
  const quotes = depth < MAX_DEPTH; // глубже — «>» остаётся текстом
  const isQuote = (l: string) => quotes && QUOTE.test(l);
  let i = 0;
  const startsBlock = (idx: number) => {
    const l = lines[idx];
    return isFence(l) || HEADING.test(l) || isHr(l) || LIST.test(l) || isQuote(l);
  };

  while (i < lines.length) {
    const line = lines[i];
    const key = `${prefix}${i}`;
    if (!line.trim()) {
      i++;
      continue;
    }
    if (isFence(line)) {
      const fence = line.trim().slice(0, 3);
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence)) body.push(lines[i++]);
      i++;
      out.push(<pre key={key}><code>{body.join("\n")}</code></pre>);
      continue;
    }
    const h = HEADING.exec(line);
    if (h) {
      const level = Math.min(h[1].length + 1, 6); // h1 экрана — название материала
      const Tag = `h${level}` as "h2";
      out.push(<Tag key={key}>{renderInline(headingText(h[2]))}</Tag>);
      i++;
      continue;
    }
    if (isHr(line)) {
      out.push(<hr key={key} />);
      i++;
      continue;
    }
    if (line.includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1]) && cells(line).length <= MAX_COLS) {
      const head = cells(line);
      i += 2;
      const rows: string[][] = [];
      const rest: string[] = [];
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) {
        if (rows.length < MAX_ROWS) rows.push(cells(lines[i]).slice(0, MAX_COLS));
        else rest.push(lines[i]);
        i++;
      }
      out.push(
        <div className="md-table" key={key}>
          <table>
            <thead>
              <tr>{head.map((c, j) => <th key={j}>{renderInline(c)}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((r, j) => (
                <tr key={j}>{head.map((_, k) => <td key={k}>{renderInline(r[k] ?? "")}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      if (rest.length) out.push(<p key={`${key}r`}>{rest.join(" ")}</p>); // остаток — текстом
      continue;
    }
    if (isQuote(line)) {
      const body: string[] = [];
      while (i < lines.length && isQuote(lines[i])) body.push(QUOTE.exec(lines[i++])![1]);
      out.push(<blockquote key={key}>{renderBlocks(body.join("\n"), `${key}q`, depth + 1)}</blockquote>);
      continue;
    }
    if (LIST.test(line)) {
      const items: Item[] = [];
      while (i < lines.length) {
        const m = LIST.exec(lines[i]);
        if (m) {
          items.push({
            indent: m[1].replace(/\t/g, "    ").length,
            ordered: /\d/.test(m[2]),
            num: parseInt(m[2], 10) || 1,
            text: m[3],
          });
          i++;
        } else if (!lines[i].trim()) {
          // Пустая строка не рвёт список, если за ней идёт следующий пункт.
          let k = i;
          while (k < lines.length && !lines[k].trim()) k++;
          const next = k < lines.length ? LIST.exec(lines[k]) : null;
          // Тот же вид списка (маркеры или номера) продолжается, другой — новый список.
          if (next && /\d/.test(next[2]) === items[items.length - 1].ordered) i = k;
          else break;
        } else if (/^[ \t]+/.test(lines[i]) && !startsBlock(i)) {
          items[items.length - 1].text += ` ${lines[i].trim()}`; // продолжение пункта
          i++;
        } else break;
      }
      out.push(renderListNode(buildList(items), key));
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && (para.length === 0 || !startsBlock(i))) para.push(lines[i++].trim());
    const text = para.join(" ");
    out.push(<p key={key}>{text.length > MAX_TEXT ? text : renderInline(text)}</p>);
  }
  return out;
}

export function Markdown({ text }: { text: string }) {
  return <div className="md">{renderBlocks(text)}</div>;
}
