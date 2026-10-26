import { MouseEvent } from "react";
import { openLink } from "../telegram";

// Адрес до пробела, кавычек и ёлочек; хвостовая пунктуация срезается ниже.
const URL_RE = /https?:\/\/[^\s<>"«»]+/g;
const TAIL = /[.,;:!?'"\]}]$/;

// «…/SALARY-1?» → «…/SALARY-1». Закрывающая скобка срезается, только если
// она лишняя: у «wiki/A_(b)» она часть адреса.
export function trimUrl(url: string): string {
  let u = url;
  for (;;) {
    if (TAIL.test(u)) u = u.slice(0, -1);
    else if (u.endsWith(")") && (u.match(/\(/g)?.length ?? 0) < (u.match(/\)/g)?.length ?? 0)) u = u.slice(0, -1);
    else return u;
  }
}

// Короткая метка ссылки: ключ тикета (SALARY-634569), иначе домен.
export function linkLabel(url: string): string {
  const rest = url.replace(/^https?:\/\//, "").replace(/[?#].*$/, "").replace(/\/+$/, "");
  const last = rest.split("/").pop() ?? "";
  if (/^[A-Z]+-\d+$/.test(last)) return last;
  return rest.split("/")[0];
}

// Текст с ссылками: длинный адрес растягивает задачу на 3–4 строки, поэтому
// вместо него — метка. Нажатие открывает ссылку и не раскрывает строку.
export function Title({ text }: { text: string }) {
  const parts: (string | { url: string })[] = [];
  let pos = 0;
  for (const m of text.matchAll(URL_RE)) {
    const url = trimUrl(m[0]);
    parts.push(text.slice(pos, m.index), { url });
    pos = (m.index ?? 0) + url.length;
  }
  parts.push(text.slice(pos));
  const open = (e: MouseEvent, url: string) => {
    e.preventDefault();
    e.stopPropagation();
    openLink(url);
  };
  return (
    <>
      {parts.map((p, i) =>
        typeof p === "string" ? (
          p
        ) : (
          <a key={i} className="link" href={p.url} onClick={(e) => open(e, p.url)}>
            🔗 {linkLabel(p.url)}
          </a>
        ),
      )}
    </>
  );
}
