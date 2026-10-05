export type EnvironmentValue = "domestic_real" | "us_real" | "domestic_paper" | "us_paper";

export interface DomesticHolding {
  code: string;
  name: string;
  quantity: number | null;
  tradable_quantity: number | null;
  purchase_price: number | null;
  current_price: number | null;
  purchase_amount: number | null;
  evaluation_amount: number | null;
  profit_loss: number | null;
  return_rate: number | null;
  weight: number | null;
}

export interface DomesticAccount {
  market: "domestic";
  environment: EnvironmentValue;
  account_no: string;
  fetched_at: string;
  summary: {
    estimated_assets: number | null;
    total_evaluation: number | null;
    total_purchase: number | null;
    total_profit_loss: number | null;
    total_return_rate: number | null;
  };
  deposit: {
    deposit: number | null;
    orderable: number | null;
    withdrawable: number | null;
    d1_estimated: number | null;
    d2_estimated: number | null;
  };
  holdings: DomesticHolding[];
}

export interface UsHolding {
  code: string;
  name: string;
  exchange: string;
  currency: string;
  quantity: number | null;
  sellable_quantity: number | null;
  purchase_price: number | null;
  current_price: number | null;
  purchase_amount: number | null;
  evaluation_amount: number | null;
  profit_loss: number | null;
  return_rate: number | null;
  evaluation_amount_krw: number | null;
  profit_loss_krw: number | null;
}

export interface UsAccount {
  market: "us";
  environment: EnvironmentValue;
  account_no: string;
  fetched_at: string;
  currency: string;
  summary: {
    total_evaluation: number | null;
    total_purchase: number | null;
    total_profit_loss: number | null;
    total_return_rate: number | null;
    today_realized_profit_loss: number | null;
    today_realized_return_rate: number | null;
  };
  summary_krw: {
    total_evaluation: number | null;
    total_purchase: number | null;
    total_profit_loss: number | null;
    today_realized_profit_loss: number | null;
  };
  deposit: {
    krw_deposit: number | null;
    currencies: {
      currency: string;
      currency_name: string;
      deposit: number | null;
      orderable: number | null;
      withdrawable: number | null;
    }[];
  };
  holdings: UsHolding[];
}

export type Account = DomesticAccount | UsAccount;

export class ApiError extends Error {
  constructor(
    readonly kind: string,
    message: string,
    readonly requestId: string | null,
    readonly missing: string[] = [],
  ) {
    super(message);
  }
}

export type RankingKind = "trading_value" | "gainers" | "volume" | "popular";
export type RankingDirection = "up" | "down" | "flat" | "unknown";

export interface RankingItem {
  rank: number | null;
  code: string;
  name: string;
  exchange: string | null;
  price: number | null;
  direction: RankingDirection;
  change_rate: number | null;
  trading_value?: number | null;
  previous_rank?: number | null;
  volume?: number | null;
  rank_change?: number | null;
}

export interface Ranking {
  environment: EnvironmentValue;
  market: "domestic" | "us";
  kind: RankingKind;
  exchange?: string;
  period?: string;
  /** 인기 종목의 집계 시각. 구할 수 없으면 null. */
  base_time?: string | null;
  fetched_at: string;
  items: RankingItem[];
}

export interface StockSearchItem {
  code: string;
  name: string;
  english_name: string | null;
  exchange: string | null;
  category: string | null;
  industry: string | null;
  status: string | null;
  is_etf: boolean | null;
}

export interface StockSearchResult {
  environment: EnvironmentValue;
  market: "domestic" | "us";
  query: string;
  total: number;
  truncated: boolean;
  list_fetched_at: string;
  items: StockSearchItem[];
}

export function searchStocks(
  environment: EnvironmentValue,
  query: string,
  signal: AbortSignal,
): Promise<StockSearchResult> {
  const params = new URLSearchParams({ q: query });
  return getJson<StockSearchResult>(`/api/environments/${environment}/stocks?${params}`, signal);
}

export interface Quote {
  code: string;
  price: number | null;
  fetched_at: string;
}

export function fetchQuote(
  environment: EnvironmentValue,
  code: string,
  exchange: string | null,
  signal: AbortSignal,
): Promise<Quote> {
  const params = new URLSearchParams({ code });
  if (exchange) params.set("exchange", exchange);
  return getJson<Quote>(`/api/environments/${environment}/quote?${params}`, signal);
}

export interface OrderRequest {
  order_key: string;
  code: string;
  exchange?: string;
  order_type: "limit" | "market";
  quantity: string;
  price?: string;
}

export interface OrderAccepted {
  order_key: string;
  order_no: string;
  accepted_at: string;
}

export const ORDER_TIMEOUT_MS = 30_000;
/** 주문이 키움에 접수됐는지 알 수 없는 결과. 실패와 달리 다시 주문하기 전에 확인이 필요하다. */
export const ORDER_RESULT_UNKNOWN = "order_result_unknown";
const UNKNOWN_KINDS = new Set([ORDER_RESULT_UNKNOWN, "duplicate_order"]);

/**
 * 매수 주문을 보낸다. 어떤 경우에도 다시 보내지 않는다.
 * 응답을 받지 못했거나(연결 끊김, 시간 초과) 해석하지 못하면 접수 여부 확인 불가로 던진다.
 * 같은 주문 키의 중복 거부(409)도 이전 요청이 접수됐을 수 있으므로 확인 불가로 본다.
 */
export async function placeOrder(
  environment: EnvironmentValue,
  order: OrderRequest,
): Promise<{ result: OrderAccepted; requestId: string | null }> {
  const unknown = (requestId: string | null = null) =>
    new ApiError(ORDER_RESULT_UNKNOWN, "접수 여부를 확인할 수 없습니다.", requestId);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ORDER_TIMEOUT_MS);
  let response: Response;
  let body: { error?: { kind?: string; message?: string; request_id?: string } } & Partial<OrderAccepted>;
  try {
    response = await fetch(`/api/environments/${environment}/orders`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(order),
      signal: controller.signal,
    });
    body = await response.json();
  } catch {
    throw unknown();
  } finally {
    clearTimeout(timer);
  }
  const requestId = body?.error?.request_id ?? response.headers.get("X-Request-ID");
  if (response.ok) {
    if (typeof body?.order_no !== "string" || !body.order_no) throw unknown(requestId);
    return { result: body as OrderAccepted, requestId };
  }
  const kind = body?.error?.kind;
  if (!kind || UNKNOWN_KINDS.has(kind)) throw unknown(requestId);
  throw new ApiError(kind, body.error?.message ?? `요청이 실패했습니다. (HTTP ${response.status})`, requestId);
}

export function fetchAccount(environment: EnvironmentValue, signal: AbortSignal): Promise<Account> {
  return getJson<Account>(`/api/environments/${environment}/account`, signal);
}

export interface RankingConditions {
  exchange: string;
  period: string;
}

export function fetchRanking(
  environment: EnvironmentValue,
  kind: RankingKind,
  conditions: RankingConditions,
  signal: AbortSignal,
): Promise<Ranking> {
  // 인기 종목은 집계 구간으로, 나머지 순위는 거래소로 조회한다.
  const query = new URLSearchParams(
    kind === "popular" ? { period: conditions.period } : { exchange: conditions.exchange },
  );
  return getJson<Ranking>(`/api/environments/${environment}/rankings/${kind}?${query}`, signal);
}

async function getJson<T>(url: string, signal: AbortSignal): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, { signal });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new ApiError("network", "서버에 연결할 수 없습니다. 서버가 실행 중인지 확인하세요.", null);
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const error = body?.error;
    throw new ApiError(
      error?.kind ?? "unknown",
      error?.message ?? `요청이 실패했습니다. (HTTP ${response.status})`,
      error?.request_id ?? response.headers.get("X-Request-ID"),
      error?.missing ?? [],
    );
  }
  return body as T;
}
