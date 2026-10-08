const krw = new Intl.NumberFormat("ko-KR");
const count = new Intl.NumberFormat("ko-KR");

export const EMPTY = "–";

// 손익은 <Change>가 절댓값에 부호를 붙인다. 그 밖의 값은 여기서 음수 부호를 유지한다.
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

/** 미체결 주문의 가격. 국내는 원, 미국은 보유종목과 같은 USD 4자리 표시다. */
export function formatOrderPrice(price: number, market: "domestic" | "us"): string {
  return market === "domestic" ? formatKrw(price) : formatForeign(price, "USD", 4);
}

/** 큰 원화 금액을 짧게: 5.36조, 5,308억, 1,234만 */
export function formatCompactKrw(value: number | null): string {
  if (value === null) return EMPTY;
  const abs = Math.abs(value);
  const body =
    abs >= 1e12
      ? `${(abs / 1e12).toFixed(2)}조`
      : abs >= 1e8
        ? `${krw.format(Math.round(abs / 1e8))}억`
        : abs >= 1e4
          ? `${krw.format(Math.round(abs / 1e4))}만`
          : `${krw.format(abs)}원`;
  return `${minus(value)}${body}`;
}

/** 큰 달러 금액을 짧게: $1.23B, $104.1M, $12.3K */
export function formatCompactUsd(value: number | null): string {
  if (value === null) return EMPTY;
  const abs = Math.abs(value);
  const body =
    abs >= 1e9
      ? `${(abs / 1e9).toFixed(2)}B`
      : abs >= 1e6
        ? `${(abs / 1e6).toFixed(1)}M`
        : abs >= 1e3
          ? `${(abs / 1e3).toFixed(1)}K`
          : abs.toFixed(0);
  return `${minus(value)}$${body}`;
}

/** 큰 수량을 짧게: 5.05억주, 1,307만주 */
export function formatCompactCount(value: number | null, unit = "주"): string {
  if (value === null) return EMPTY;
  if (value >= 1e8) return `${(value / 1e8).toFixed(2)}억${unit}`;
  if (value >= 1e4) return `${count.format(Math.round(value / 1e4))}만${unit}`;
  return `${count.format(value)}${unit}`;
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

/** 한국시간 기준 HH:MM */
export function formatKstTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Seoul",
  });
}

export type Direction = "up" | "down" | "flat";

export function direction(value: number | null): Direction {
  if (value === null || value === 0) return "flat";
  return value > 0 ? "up" : "down";
}
