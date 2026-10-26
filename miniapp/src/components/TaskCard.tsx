import { useState } from "react";

export type CardAction = { label: string; onClick: () => void; danger?: boolean };

type Props = {
  title: string;
  note?: string;
  meta?: string;
  disabled: boolean;
  actions: CardAction[];
  // Перенос на выбранную дату. Переносим только по «Перенести»: iOS при
  // открытии пикера сам подставляет сегодня и шлёт change.
  onPickDate?: (date: string) => void;
};

export function TaskCard({ title, note, meta, disabled, actions, onPickDate }: Props) {
  const [picking, setPicking] = useState<string | null>(null);

  return (
    <li className="card">
      <div className="title">{title}</div>
      {note && <div className="note">{note}</div>}
      {meta && <div className="meta">{meta}</div>}
      <div className="actions">
        {actions.map((a) => (
          <button key={a.label} className={a.danger ? "danger" : undefined} disabled={disabled} onClick={a.onClick}>
            {a.label}
          </button>
        ))}
        {onPickDate && (
          <button disabled={disabled} onClick={() => setPicking("")}>
            Дата
          </button>
        )}
      </div>
      {onPickDate && picking !== null && (
        <div className="picker">
          <input type="date" aria-label="Выбрать дату" value={picking} onChange={(e) => setPicking(e.target.value)} />
          <button
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
      )}
    </li>
  );
}
