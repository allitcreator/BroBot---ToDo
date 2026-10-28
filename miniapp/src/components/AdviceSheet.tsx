import { useState } from "react";
import { Title } from "./Title";

// Рекомендации агенту к задаче: лист с полем ввода. Пустой текст — снять.
export function AdviceSheet({ title, initial, onSave, onCancel }: {
  title: string;
  initial: string;
  onSave: (advice: string | null) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState(initial);
  return (
    <div className="sheet-backdrop">
      <div className="sheet" role="dialog" aria-label={`Рекомендации: ${title}`}>
        <h2>Рекомендации агенту</h2>
        <p className="hint">
          <Title text={title} />
        </p>
        <textarea aria-label="Текст рекомендаций" rows={5} maxLength={2000} value={text} onChange={(e) => setText(e.target.value)} />
        <div className="actions">
          <button onClick={() => onSave(text.trim() || null)}>Сохранить</button>
          <button className="ghost-text" onClick={onCancel}>
            Отмена
          </button>
        </div>
      </div>
    </div>
  );
}
