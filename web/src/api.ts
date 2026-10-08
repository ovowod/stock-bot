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

/** 서버가 로그인 세션이 없다고 거부한 요청의 오류 종류. */
export const UNAUTHORIZED = "unauthorized";

/** 로그인 세션이 없어 거부된 요청이 주문이었는지. 주문이면 보내지 않았다고 알려야 한다. */
export type UnauthorizedSource = "order" | "request";

const unauthorizedListeners = new Set<(source: UnauthorizedSource) => void>();

/** 어떤 요청이든 로그인 세션이 없다고 거부되면 listener를 부른다. 해제 함수를 돌려준다. */
export function onUnauthorized(listener: (source: UnauthorizedSource) => void): () => void {
  unauthorizedListeners.add(listener);
  return () => unauthorizedListeners.delete(listener);
}

function reportIfUnauthorized(kind: string | undefined, source: UnauthorizedSource) {
  if (kind === UNAUTHORIZED) unauthorizedListeners.forEach((listener) => listener(source));
}

/** 로그인 세션이 있으면 true. 서버에 연결하지 못하면 ApiError를 던진다. */
export async function checkSession(): Promise<boolean> {
  let response: Response;
  try {
    response = await fetch("/api/auth/session");
  } catch {
    throw networkError();
  }
  if (response.status === 401) return false;
  if (!response.ok) throw await responseError(response);
  return true;
}

/** 비밀번호로 로그인한다. 실패하면 서버가 준 오류 종류로 ApiError를 던진다. */
export async function login(password: string): Promise<void> {
  let response: Response;
  try {
    response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
  } catch {
    throw networkError();
  }
  if (!response.ok) throw await responseError(response);
}

/**
 * 이 브라우저의 로그인 세션을 끝낸다. 서버는 이 브라우저의 쿠키가 현재 세션일 때만 지운다.
 * 응답을 받지 못해도 화면은 로그아웃된 것으로 다루므로 오류를 던지지 않는다.
 */
export async function logout(): Promise<void> {
  try {
    await fetch("/api/auth/logout", { method: "POST" });
  } catch {
    // 서버에 닿지 못했다. 서버 세션은 만료나 다음 로그인 때 끝난다.
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

/** 한 종목의 보유수량과 매도 가능 수량. 보유하지 않으면 held가 false이고 두 수량은 0이다. */
export interface Holding {
  code: string;
  held: boolean;
  quantity: number;
  sellable_quantity: number;
  fetched_at: string;
}

export function fetchHolding(environment: EnvironmentValue, code: string, signal: AbortSignal): Promise<Holding> {
  return getJson<Holding>(`/api/environments/${environment}/holdings/${encodeURIComponent(code)}`, signal);
}

export interface OrderRequest {
  order_key: string;
  side: "buy" | "sell";
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
 * 매수·매도 주문을 보낸다. 어떤 경우에도 다시 보내지 않는다.
 * 응답을 받지 못했거나(연결 끊김, 시간 초과) 해석하지 못하면 접수 여부 확인 불가로 던진다.
 * 같은 주문 키의 중복 거부(409)도 이전 요청이 접수됐을 수 있으므로 확인 불가로 본다.
 */
export function placeOrder(
  environment: EnvironmentValue,
  order: OrderRequest,
): Promise<{ result: OrderAccepted; requestId: string | null }> {
  return sendOrderRequest<OrderAccepted>(`/api/environments/${environment}/orders`, order);
}

/**
 * 키움에 주문으로 접수되는 요청(매수·매도·주문 취소)을 한 번만 보낸다. 결과 분류는 placeOrder와 같다.
 * 성공 응답에 새 주문번호(order_no)가 없으면 확인 불가다.
 */
async function sendOrderRequest<T extends { order_no: string }>(
  url: string,
  payload: object,
): Promise<{ result: T; requestId: string | null }> {
  const unknown = (requestId: string | null = null) =>
    new ApiError(ORDER_RESULT_UNKNOWN, "접수 여부를 확인할 수 없습니다.", requestId);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ORDER_TIMEOUT_MS);
  let response: Response;
  let body: { error?: { kind?: string; message?: string; request_id?: string } } & Partial<T>;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
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
    return { result: body as T, requestId };
  }
  const kind = body?.error?.kind;
  // 로그인 세션이 없으면 서버가 주문을 키움에 보내기 전에 거부한다. 확인 불가가 아니라 실패다.
  reportIfUnauthorized(kind, "order");
  if (!kind || UNKNOWN_KINDS.has(kind)) throw unknown(requestId);
  throw new ApiError(kind, body.error?.message ?? `요청이 실패했습니다. (HTTP ${response.status})`, requestId);
}

export function fetchAccount(environment: EnvironmentValue, signal: AbortSignal): Promise<Account> {
  return getJson<Account>(`/api/environments/${environment}/account`, signal);
}

/** 이 앱에서 취소할 수 없는 이유. 실전투자, 신용주문, KRX가 아닌 거래소(NXT·통합) 주문. */
export type CancelBlockedReason = "real" | "credit" | "exchange";

export interface OpenOrder {
  order_no: string;
  code: string;
  name: string;
  side: "buy" | "sell" | null;
  /** 매수, 매도, 매수정정, 매수신용 등 키움 주문구분. */
  side_label: string;
  order_type: string;
  /** 시장가처럼 가격이 없으면 null. */
  price: number | null;
  ordered_quantity: number | null;
  remaining_quantity: number;
  time: string;
  exchange: string;
  cancelable: boolean;
  blocked_reason: CancelBlockedReason | null;
}

export interface OpenOrders {
  orders: OpenOrder[];
  fetched_at: string;
}

export function fetchOpenOrders(environment: EnvironmentValue, signal: AbortSignal): Promise<OpenOrders> {
  return getJson<OpenOrders>(`/api/environments/${environment}/open-orders`, signal);
}

export interface CancelRequest {
  order_key: string;
  order_no: string;
  quantity: string;
}

export interface CancelAccepted {
  order_key: string;
  /** 키움이 취소 주문에 붙인 새 주문번호. */
  order_no: string;
  original_order_no: string;
  /** 키움이 확정해 준 취소 수량. 잔량 전부로 보내 키움이 0을 주면 null이다. */
  cancel_quantity: number | null;
  accepted_at: string;
}

/** 국내 미체결 주문을 취소한다. 결과 분류는 placeOrder와 같고, 다시 보내지 않는다. */
export function cancelOrder(
  environment: EnvironmentValue,
  request: CancelRequest,
): Promise<{ result: CancelAccepted; requestId: string | null }> {
  return sendOrderRequest<CancelAccepted>(`/api/environments/${environment}/cancellations`, request);
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
    throw networkError();
  }
  if (!response.ok) throw await responseError(response);
  return (await response.json().catch(() => null)) as T;
}

function networkError(): ApiError {
  return new ApiError("network", "서버에 연결할 수 없습니다. 서버가 실행 중인지 확인하세요.", null);
}

async function responseError(response: Response): Promise<ApiError> {
  const body = await response.json().catch(() => null);
  const error = body?.error;
  reportIfUnauthorized(error?.kind, "request");
  return new ApiError(
    error?.kind ?? "unknown",
    error?.message ?? `요청이 실패했습니다. (HTTP ${response.status})`,
    error?.request_id ?? response.headers.get("X-Request-ID"),
    error?.missing ?? [],
  );
}
