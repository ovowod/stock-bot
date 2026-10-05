import type { EnvironmentOption } from "../../environments";

type Market = EnvironmentOption["market"];

/** 주문 패널을 여는 화면이 넘기는 종목 정보. 모르는 값은 null이다. */
export interface OrderTarget {
  code: string;
  name: string;
  /** 미국 거래소(NYSE·NASDAQ·AMEX 등). 국내는 null. */
  exchange: string | null;
  /** 국내 종목 구분(코스피·코스닥·ETF 등). 미국은 null. */
  category: string | null;
  /** 관리종목 등 종목 상태. 정상이면 null. */
  status: string | null;
}

export type OrderType = "limit" | "market";

// 지금은 지정가·시장가만 지원한다. 키움 매수 TR의 다른 매매구분
// (국내 kt10000의 IOC·FOK·최유리·중간가·시간외 등, 미국 ust20000의 LOC·VWAP·TWAP)도
// 결국 지원할 예정이다. 목록은 .scratch/order-panel/spec.md의 Out of Scope에 있다.
export const ORDER_TYPES: { value: OrderType; label: string; trde_tp: Record<Market, string> }[] = [
  { value: "limit", label: "지정가", trde_tp: { domestic: "0", us: "00" } },
  { value: "market", label: "시장가", trde_tp: { domestic: "3", us: "03" } },
];

// 미국 매수 TR(ust20000)의 거래소 값은 NA·ND·NY뿐이고, 미국 모의투자도 이 세 거래소만 지원한다.
const US_ORDER_EXCHANGES = ["NYSE", "NASDAQ", "AMEX"];

/**
 * 매수할 수 없으면 그 이유를, 할 수 있으면 null을 돌려준다.
 * 국내는 주문을 KRX로 보내고 순위·검색 종목이 모두 코스피·코스닥이라 모의투자(KRX만 지원)에서도 막지 않는다.
 */
export function buyUnavailableReason(market: Market, isReal: boolean, exchange: string | null): string | null {
  if (market !== "us" || (exchange && US_ORDER_EXCHANGES.includes(exchange))) return null;
  const rule = isReal
    ? "키움 미국주식 매수는 NYSE·NASDAQ·AMEX 종목만 지원합니다."
    : "미국 모의투자는 NYSE·NASDAQ·AMEX 종목만 매수할 수 있습니다.";
  const actual = exchange ? `이 종목의 거래소는 ${exchange}입니다.` : "이 종목의 거래소를 확인할 수 없습니다.";
  return `${rule} ${actual}`;
}

// 키움 문서의 ord_qty·ord_uv 길이(12)를 따른다. 이 길이의 값은 숫자로 바꿔도 반올림되지 않는다.
const MAX_LENGTH = 12;

/** 수량 입력값을 검사한다. text는 주문에 그대로 보낼 문자열이다. */
export function parseQuantity(value: string): { quantity: number | null; text: string | null; error: string | null } {
  const text = value.trim();
  if (text === "") return { quantity: null, text: null, error: null };
  if (/^\d+$/.test(text) && text.length <= MAX_LENGTH && Number(text) >= 1) {
    return { quantity: Number(text), text, error: null };
  }
  return { quantity: null, text: null, error: "수량은 1주 이상, 12자리 이하의 정수로 입력하세요." };
}

/** 가격 입력값(쉼표 제외)을 검사한다. text는 주문에 그대로 보낼 문자열이다. */
export function parsePrice(
  value: string,
  market: Market,
): { price: number | null; text: string | null; error: string | null } {
  const text = value.trim();
  if (text === "") return { price: null, text: null, error: null };
  const pattern = market === "domestic" ? /^\d+$/ : /^\d+(\.\d+)?$/;
  if (pattern.test(text) && text.length <= MAX_LENGTH && Number(text) > 0) {
    // 키움 1517 응답에 적힌 규칙: 미국 가격은 $1 미만은 소수 넷째 자리, $1 이상은 소수 둘째 자리까지.
    const decimals = text.split(".")[1]?.length ?? 0;
    if (market === "us" && decimals > (Number(text) < 1 ? 4 : 2)) {
      return {
        price: null,
        text: null,
        error: "미국 주식 가격은 $1 이상이면 소수 둘째 자리, $1 미만이면 소수 넷째 자리까지 입력할 수 있습니다.",
      };
    }
    return { price: Number(text), text, error: null };
  }
  return {
    price: null,
    text: null,
    error:
      market === "domestic"
        ? "가격은 1원 이상, 12자리 이하의 정수로 입력하세요."
        : "가격은 0보다 크고 12글자 이하인 숫자로 입력하세요.",
  };
}

/** 최종 확인마다 새로 만드는 주문 키. 보안 연결이 아니어도 쓸 수 있는 getRandomValues로 만든다. */
export function newOrderKey(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** 가격 입력값의 정수 부분에 천 단위 쉼표를 넣는다. 숫자 형식이 아니면 그대로 둔다. */
export function groupThousands(value: string): string {
  const match = /^(\d+)(\.\d*)?$/.exec(value);
  if (!match) return value;
  return match[1].replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (match[2] ?? "");
}
