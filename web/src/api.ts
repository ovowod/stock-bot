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

export async function fetchAccount(
  environment: EnvironmentValue,
  signal: AbortSignal,
): Promise<Account> {
  let response: Response;
  try {
    response = await fetch(`/api/environments/${environment}/account`, { signal });
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
  return body as Account;
}
