import { ApiError } from "../api";

export function errorText(e: unknown): { message: string; expired: boolean } {
  const expired = e instanceof ApiError && e.status === 401;
  return {
    expired,
    message: expired ? "Сессия устарела — закрой и открой мини-апп заново." : (e as Error).message,
  };
}

export function ErrorView({ message, expired, onRetry }: { message: string; expired: boolean; onRetry: () => void }) {
  return (
    <div className="center">
      <p>{message}</p>
      {!expired && <button onClick={onRetry}>Повторить</button>}
    </div>
  );
}
