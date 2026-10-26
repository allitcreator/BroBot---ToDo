export function dayLabel(iso: string, today: string): string {
  if (iso === today) return "Сегодня";
  const d = new Date(`${iso}T12:00:00`);
  const t = new Date(`${today}T12:00:00`);
  const diff = Math.round((d.getTime() - t.getTime()) / 86400000);
  if (diff === 1) return "Завтра";
  const label = d.toLocaleDateString("ru-RU", { weekday: "short", day: "numeric", month: "short" });
  return label.charAt(0).toUpperCase() + label.slice(1);
}

export function shortDate(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
}

export function badges(t: { reminder: boolean; calendar: boolean }): string {
  return [t.reminder ? "🔔" : "", t.calendar ? "🗓" : ""].filter(Boolean).join(" ");
}

export function localToday(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
