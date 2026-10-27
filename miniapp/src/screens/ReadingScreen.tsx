import { useCallback, useEffect, useState } from "react";
import {
  ApiError, Material, MaterialMeta, deleteMaterial, getMaterial, getReading, setMaterialPinned, setMaterialRead,
} from "../api";
import { ErrorView, errorText } from "../components/Status";
import { shortDate } from "../format";
import { shortTitle } from "../hooks";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { Markdown } from "../markdown";
import {
  cacheList, cacheMaterial, cachedList, cachedMaterial, clearReadingCache, forgetMaterial, getFontSize, pruneCache,
} from "../reading";
import { alertUser, confirm, haptic } from "../telegram";

type Load =
  | { kind: "loading" }
  | { kind: "ready"; items: MaterialMeta[]; offline: boolean }
  | { kind: "error"; message: string; expired: boolean };

type Props = { onChanged?: () => void; initialId?: number | null; onConsumed?: () => void };

// Сети нет — нет и HTTP-статуса. Ответ сервера (даже 5xx) кэшем не подменяем.
const isNetworkError = (e: unknown) => !(e instanceof ApiError);
const isAuthError = (e: unknown) => e instanceof ApiError && (e.status === 401 || e.status === 403);

export function ReadingScreen({ onChanged, initialId = null, onConsumed }: Props) {
  const [state, setState] = useState<Load>({ kind: "loading" });
  const [material, setMaterial] = useState<Material | null>(null);
  const [offline, setOffline] = useState(false);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const { items } = await getReading();
      cacheList(items);
      pruneCache(items.map((i) => i.id));
      setState({ kind: "ready", items, offline: false });
    } catch (e) {
      if (isAuthError(e)) clearReadingCache();
      // Нет сети — последний список из кэша (метро); ответ сервера — ошибка как есть.
      const cached = isNetworkError(e) ? cachedList() : [];
      if (cached.length) setState({ kind: "ready", items: cached, offline: true });
      else setState({ kind: "error", ...errorText(e) });
    }
  }, []);

  const open = useCallback(async (id: number) => {
    try {
      const m = await getMaterial(id);
      cacheMaterial(m);
      setMaterial(m);
      setOffline(false);
    } catch (e) {
      if (isNetworkError(e)) {
        const cached = cachedMaterial(id);
        if (cached) {
          setMaterial(cached);
          setOffline(true);
          return;
        }
      } else if (e instanceof ApiError && e.status === 404) {
        forgetMaterial(id);
        alertUser("Материал удалён");
        void load();
        return;
      } else if (isAuthError(e)) {
        clearReadingCache();
      }
      alertUser((e as Error).message);
    }
  }, [load]);

  useEffect(() => {
    void load();
  }, [load]);

  // Параметр ?read=<id> открывает материал один раз, потом сбрасывается.
  useEffect(() => {
    if (initialId === null) return;
    void open(initialId);
    onConsumed?.();
  }, [initialId, open, onConsumed]);

  const back = () => {
    setMaterial(null);
    void load();
    onChanged?.();
  };

  if (material) {
    return (
      <Reader
        material={material}
        offline={offline}
        onBack={back}
        onChange={(m) => {
          setMaterial(m);
          cacheMaterial(m);
        }}
        onChanged={onChanged}
      />
    );
  }

  if (state.kind === "loading") return <div className="center">Загружаю…</div>;
  if (state.kind === "error") return <ErrorView {...state} onRetry={() => void load()} />;

  const unread = state.items.filter((i) => !i.read).length;
  return (
    <>
      <header>
        <h1>
          <span className="ico">📖</span>Чтение
        </h1>
        <button className="ghost" onClick={() => void load()} aria-label="Обновить">↻</button>
      </header>
      <p className="sub">
        {unread ? `${unread} не прочитано` : "всё прочитано"}
        {state.offline ? " · без сети" : ""}
      </p>
      {state.items.length === 0 && (
        <div className="empty">
          <div className="big">📖</div>
          Пока пусто
          <small>Материалы приходят с Mac командой «скинь почитать».</small>
        </div>
      )}
      <ul className="reading-list">
        {state.items.map((item) => (
          <li key={item.id}>
            <button className={item.read ? "reading-item read" : "reading-item"} onClick={() => void open(item.id)}>
              <span className="reading-title">
                {item.pinned ? "📌 " : ""}
                {item.title}
              </span>
              <small>
                {shortDate(item.created_at)} · {Math.max(1, Math.round(item.chars / 1200))} мин
              </small>
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}

function Reader({
  material, offline, onBack, onChange, onChanged,
}: {
  material: Material;
  offline: boolean;
  onBack: () => void;
  onChange: (m: Material) => void;
  onChanged?: () => void;
}) {
  const [busy, setBusy] = useState(false);

  const act = async (call: () => Promise<unknown>, next: Material) => {
    if (busy) return;
    setBusy(true);
    try {
      await call();
      haptic("success");
      onChange(next);
      onChanged?.();
    } catch (e) {
      haptic("error");
      alertUser((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (busy || !(await confirm(`Удалить «${shortTitle(material.title)}»?`))) return;
    setBusy(true);
    try {
      await deleteMaterial(material.id);
      forgetMaterial(material.id);
      haptic("success");
      onBack();
    } catch (e) {
      haptic("error");
      alertUser((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <header>
        <button className="ghost-text" onClick={onBack}>‹ Назад</button>
      </header>
      {offline && <p className="ritual-note">Без сети: показана сохранённая копия.</p>}
      <article className="reader" style={{ fontSize: `${getFontSize()}px` }}>
        <h1>{material.title}</h1>
        <ErrorBoundary>
          <Markdown text={material.markdown} />
        </ErrorBoundary>
      </article>
      <div className="reader-actions">
        <button
          disabled={busy || offline}
          onClick={() => void act(() => setMaterialRead(material.id, !material.read), { ...material, read: !material.read })}
        >
          {material.read ? "Не прочитано" : "Прочитано"}
        </button>
        <button
          className="quiet"
          disabled={busy || offline}
          onClick={() => void act(() => setMaterialPinned(material.id, !material.pinned), { ...material, pinned: !material.pinned })}
        >
          {material.pinned ? "Открепить" : "Закрепить"}
        </button>
        <button className="quiet danger" disabled={busy || offline} onClick={() => void remove()}>Удалить</button>
      </div>
    </>
  );
}
