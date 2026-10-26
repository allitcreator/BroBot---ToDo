import { MouseEvent } from "react";
import { openLink } from "../telegram";

const URL_RE = /https?:\/\/[^\s<>"]+/g;

// Короткая метка ссылки: ключ тикета (SALARY-634569), иначе домен.
export function linkLabel(url: string): string {
  const rest = url.replace(/^https?:\/\//, "").replace(/[/.,)]+$/, "");
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
    const url = m[0].replace(/[.,)]+$/, "");
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
