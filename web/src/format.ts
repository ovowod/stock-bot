const krw = new Intl.NumberFormat("ko-KR");
const count = new Intl.NumberFormat("ko-KR");

export const EMPTY = "–";

// 손익은 <Change>가 절댓값에 ▲▼와 부호를 붙인다. 그 밖의 값은 여기서 음수 부호를 유지한다.
const minus = (value: number) => (value < 0 ? "−" : "");

export function formatKrw(value: number | null): string {
  return value === null ? EMPTY : `${minus(value)}${krw.format(Math.abs(value))}원`;
}

export function formatForeign(value: number | null, currency: string, digits = 2): string {
  if (value === null) return EMPTY;
  const formatted = new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: digits,
  }).format(Math.abs(value));
  return currency === "USD" ? `${minus(value)}$${formatted}` : `${minus(value)}${formatted} ${currency}`;
}

export function formatRate(value: number | null): string {
  return value === null ? EMPTY : `${minus(value)}${Math.abs(value).toFixed(2)}%`;
}

export function formatCount(value: number | null, unit = "주"): string {
  return value === null ? EMPTY : `${count.format(value)}${unit}`;
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export type Direction = "up" | "down" | "flat";

export function direction(value: number | null): Direction {
  if (value === null || value === 0) return "flat";
  return value > 0 ? "up" : "down";
}
