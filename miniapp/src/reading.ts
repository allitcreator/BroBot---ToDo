// Локальное состояние «Чтения»: кэш последних открытых материалов (чтобы
// читать без сети) и размер шрифта. Кэш чистится при удалении материала.

export type CachedMaterial = {
  id: number;
  title: string;
  source: string;
  created_at: string;
  pinned: boolean;
  read: boolean;
  chars: number;
  markdown: string;
};

const CACHE_KEY = "reading:cache";
const LIST_KEY = "reading:list";
const FONT_KEY = "reading:font";
export const CACHE_LIMIT = 10;

function load<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function save(key: string, value: unknown): boolean {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false; // переполнено или запрещено — читаем без кэша
  }
}

export function cachedMaterials(): CachedMaterial[] {
  return load<CachedMaterial[]>(CACHE_KEY, []);
}

export function cacheMaterial(m: CachedMaterial): void {
  const rest = cachedMaterials().filter((c) => c.id !== m.id);
  const items = [m, ...rest].slice(0, CACHE_LIMIT);
  // Квота переполнена — вытесняем самые старые, а не теряем свежий материал.
  while (!save(CACHE_KEY, items) && items.length > 1) items.pop();
}

export function cachedMaterial(id: number): CachedMaterial | undefined {
  return cachedMaterials().find((c) => c.id === id);
}

export function forgetMaterial(id: number): void {
  save(CACHE_KEY, cachedMaterials().filter((c) => c.id !== id));
  save(LIST_KEY, cachedList().filter((c) => c.id !== id));
}

// Сессия недействительна (401/403): чужие или старые тексты на устройстве не держим.
export function clearReadingCache(): void {
  try {
    window.localStorage.removeItem(CACHE_KEY);
    window.localStorage.removeItem(LIST_KEY);
  } catch {
    // нечего чистить
  }
}

// Список с сервера — источник правды: материалов, которых в нём нет, в кэше быть не должно.
export function pruneCache(serverIds: number[]): void {
  const keep = new Set(serverIds);
  const items = cachedMaterials();
  const kept = items.filter((c) => keep.has(c.id));
  if (kept.length !== items.length) save(CACHE_KEY, kept);
}

// Последний список без текстов: показывается, когда сети нет.
export type CachedListItem = Omit<CachedMaterial, "markdown">;

export function cachedList(): CachedListItem[] {
  return load<CachedListItem[]>(LIST_KEY, []);
}

export function cacheList(items: CachedListItem[]): void {
  save(LIST_KEY, items);
}

export const FONT_SIZES = [14, 16, 18, 20, 22] as const;
export type FontSize = (typeof FONT_SIZES)[number];

export function getFontSize(): FontSize {
  const v = load<number>(FONT_KEY, 16);
  return (FONT_SIZES as readonly number[]).includes(v) ? (v as FontSize) : 16;
}

export function setFontSize(size: FontSize): void {
  save(FONT_KEY, size);
}
