import { KeyboardEvent, useState } from "react";
import { Title } from "./Title";

// quiet — второстепенное действие («Пропустить»): тихая ссылка под сеткой.
export type CardAction = { label: string; onClick: () => void; danger?: boolean; main?: boolean; quiet?: boolean };

type Props = {
  title: string;
  note?: string;
  meta?: string;
  disabled: boolean;
  actions: CardAction[];
  // Перенос на выбранную дату. Переносим только по «Перенести»: iOS при
  // открытии пикера сам подставляет сегодня и шлёт change.
  onPickDate?: (date: string) => void;
  // Чекбокс слева: закрыть задачу.
  onDone?: () => void;
  // Действия видны сразу (входящие, разбор); иначе — по нажатию на задачу.
  open?: boolean;
  // Крупная карточка шага разбора: одна задача, решения сеткой.
  big?: boolean;
};

// Строка задачи в стиле Things 3: чекбокс, название, под ним заметка и метки.
export function TaskCard({ title, note, meta, disabled, actions, onPickDate, onDone, open = false, big = false }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [picking, setPicking] = useState<string | null>(null);
  const shown = open || big || expanded;

  const quiet = actions.filter((a) => a.quiet);
  const buttons = (
    <div className={big ? "opts" : "actions"}>
      {actions.filter((a) => !a.quiet).map((a) => (
        <button key={a.label} className={[a.danger && "danger", a.main && "main"].filter(Boolean).join(" ") || undefined} disabled={disabled} onClick={a.onClick}>
          {a.label}
        </button>
      ))}
      {onPickDate && (
        <button disabled={disabled} onClick={() => setPicking("")}>
          Дата
        </button>
      )}
    </div>
  );

  const picker = onPickDate && picking !== null && (
    <div className="picker">
      <input type="date" aria-label="Выбрать дату" value={picking} onChange={(e) => setPicking(e.target.value)} />
      <button
        className="main"
        disabled={!picking || disabled}
        onClick={() => {
          const date = picking;
          setPicking(null);
          onPickDate(date);
        }}
      >
        Перенести
      </button>
      <button className="ghost-text" onClick={() => setPicking(null)}>
        Отмена
      </button>
    </div>
  );

  if (big) {
    return (
      <li className="bigcard">
        <div className="card-box">
          <div className="title">
            <Title text={title} />
          </div>
          {note && (
            <div className="note">
              <Title text={note} />
            </div>
          )}
          {meta && <div className="meta">{meta}</div>}
        </div>
        {buttons}
        {picker}
        {quiet.map((a) => (
          <button key={a.label} className="ghost-text wide quiet" disabled={disabled} onClick={a.onClick}>
            {a.label}
          </button>
        ))}
      </li>
    );
  }

  return (
    <li className={shown ? "row open" : "row"}>
      {onDone ? <button className="cb" aria-label={`Готово: ${title}`} disabled={disabled} onClick={onDone} /> : <span className="cb-space" />}
      <div className="row-body">
        <div
          className={open ? "row-main" : "row-main tappable"}
          {...(open
            ? {}
            : {
                role: "button",
                tabIndex: 0,
                "aria-expanded": expanded,
                onClick: () => setExpanded((v) => !v),
                onKeyDown: (e: KeyboardEvent) => {
                  if (e.target !== e.currentTarget || (e.key !== "Enter" && e.key !== " ")) return;
                  e.preventDefault();
                  setExpanded((v) => !v);
                },
              })}
        >
          <div className="title">
            <Title text={title} />
          </div>
          {note && (
            <div className="note">
              <Title text={note} />
            </div>
          )}
          {meta && <div className="meta">{meta}</div>}
        </div>
        {shown && buttons}
        {picker}
      </div>
    </li>
  );
}
