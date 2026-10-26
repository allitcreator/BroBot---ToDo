// Тонкая обёртка над Telegram.WebApp: вне Telegram (тесты, браузер) всё
// работает без него, просто без подписи и нативных диалогов.

type WebApp = {
  initData: string;
  ready(): void;
  expand(): void;
  showConfirm(message: string, callback: (ok: boolean) => void): void;
  showAlert(message: string, callback?: () => void): void;
  HapticFeedback?: { notificationOccurred(type: "success" | "error" | "warning"): void };
  openLink?(url: string): void;
};

function webApp(): WebApp | undefined {
  return (window as unknown as { Telegram?: { WebApp?: WebApp } }).Telegram?.WebApp;
}

export function initData(): string {
  return webApp()?.initData ?? "";
}

export function ready(): void {
  const app = webApp();
  app?.ready();
  app?.expand();
}

export function confirm(message: string): Promise<boolean> {
  const app = webApp();
  if (!app) return Promise.resolve(window.confirm(message));
  return new Promise((resolve) => app.showConfirm(message, resolve));
}

export function alertUser(message: string): void {
  const app = webApp();
  if (app) app.showAlert(message);
  else window.alert(message);
}

export function haptic(type: "success" | "error"): void {
  webApp()?.HapticFeedback?.notificationOccurred(type);
}

// Ссылка из названия задачи: в Telegram — его браузер, мини-апп остаётся открытым.
export function openLink(url: string): void {
  const app = webApp();
  if (app?.openLink) app.openLink(url);
  else window.open(url, "_blank", "noopener");
}
