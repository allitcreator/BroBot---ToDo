import "@testing-library/jest-dom/vitest";

// В части окружений (Node с встроенным localStorage) jsdom отдаёт заглушку без
// методов Storage — подставляем рабочее хранилище в памяти.
if (typeof window.localStorage?.clear !== "function") {
  const data = new Map<string, string>();
  const storage = {
    getItem: (k: string) => (data.has(k) ? data.get(k)! : null),
    setItem: (k: string, v: string) => void data.set(k, String(v)),
    removeItem: (k: string) => void data.delete(k),
    clear: () => data.clear(),
    key: (i: number) => [...data.keys()][i] ?? null,
    get length() {
      return data.size;
    },
  };
  Object.defineProperty(window, "localStorage", { value: storage, configurable: true });
}
