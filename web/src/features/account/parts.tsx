import { AlertTriangle, Inbox, KeyRound, Lock, RefreshCw, Timer, WifiOff } from "lucide-react";
import type { ReactNode } from "react";
import type { ApiError } from "../../api";
import { direction, EMPTY } from "../../format";

const TONE = {
  up: { text: "text-gain", soft: "bg-gain-soft", sign: "+" },
  down: { text: "text-loss", soft: "bg-loss-soft", sign: "−" },
  flat: { text: "text-muted", soft: "bg-canvas", sign: "" },
} as const;

/** 수익·상승은 빨강 +, 손실·하락은 파랑 −, 0은 회색. 색을 못 구분해도 부호로 알 수 있다. 수익률은 parens로 괄호에 넣는다. */
export function Change({
  value,
  format,
  className = "",
  parens = false,
}: {
  value: number | null;
  format: (value: number | null) => string;
  className?: string;
  parens?: boolean;
}) {
  if (value === null) return <span className={`text-muted ${className}`}>{EMPTY}</span>;
  const tone = TONE[direction(value)];
  return (
    <span className={`whitespace-nowrap ${tone.text} ${className}`}>
      {parens && "("}
      {tone.sign}
      {format(Math.abs(value))}
      {parens && ")"}
    </span>
  );
}

/** 손익과 수익률을 한 줄 알약 모양으로 묶는다. */
export function ChangePill({
  amount,
  rate,
  formatAmount,
  formatRate,
}: {
  amount: number | null;
  rate: number | null;
  formatAmount: (value: number | null) => string;
  formatRate: (value: number | null) => string;
}) {
  return (
    <span
      className={`inline-flex flex-wrap items-center gap-x-1.5 rounded-full px-3 py-1 text-sm font-semibold ${
        TONE[direction(amount)].soft
      }`}
    >
      <Change value={amount} format={formatAmount} />
      <Change value={rate} format={formatRate} parens />
    </span>
  );
}

export function Panel({
  title,
  aside,
  children,
  className = "",
}: {
  title?: string;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-3xl bg-surface p-5 md:p-6 ${className}`}>
      {title && (
        <div className="mb-3 flex items-center justify-between gap-3">
          <h3 className="text-[17px] font-bold">{title}</h3>
          {aside && <span className="text-sm text-muted">{aside}</span>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Row({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 py-2.5">
      <dt className="shrink-0 text-[15px] whitespace-nowrap text-sub">{label}</dt>
      <dd className="text-right">
        <div className="text-[15px] font-semibold">{value}</div>
        {sub && <div className="text-xs text-muted">{sub}</div>}
      </dd>
    </div>
  );
}

export function MiniStat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="min-w-0 rounded-2xl bg-canvas px-4 py-3.5">
      <p className="text-[13px] text-muted">{label}</p>
      <p className="mt-0.5 truncate text-[17px] font-bold">{value}</p>
      {sub && <p className="truncate text-xs text-muted">{sub}</p>}
    </div>
  );
}

export interface HoldingView {
  key: string;
  name: string;
  meta: ReactNode;
  value: ReactNode;
  change: ReactNode;
  details: { label: string; value: ReactNode }[];
}

/** 토스식 종목 목록. 넓은 화면에서는 가운데에 세부 값을 함께 펼친다. */
export function HoldingsList({ items }: { items: HoldingView[] }) {
  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-12 text-center">
        <span className="flex size-12 items-center justify-center rounded-full bg-canvas text-muted">
          <Inbox className="size-6" aria-hidden />
        </span>
        <p className="mt-1 text-[15px] font-semibold">보유종목이 없습니다</p>
        <p className="text-sm text-muted">이 계좌에는 현재 보유 중인 종목이 없습니다.</p>
      </div>
    );
  }
  return (
    <ul className="-mx-2">
      {items.map((item) => (
        <li
          key={item.key}
          className="flex items-center gap-3 rounded-2xl px-2 py-3 transition hover:bg-canvas/70"
        >
          <span
            className="flex size-10 shrink-0 items-center justify-center rounded-full bg-brand-50 text-[15px] font-bold text-brand-600"
            aria-hidden
          >
            {item.name.slice(0, 1)}
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-[15px] font-semibold">{item.name}</p>
            <p className="truncate text-[13px] text-muted">{item.meta}</p>
          </div>
          <dl className="hidden shrink-0 gap-6 xl:flex">
            {item.details.map((detail) => (
              <div key={detail.label} className="w-24 text-right">
                <dt className="text-xs text-muted">{detail.label}</dt>
                <dd className="text-sm font-medium">{detail.value}</dd>
              </div>
            ))}
          </dl>
          <div className="shrink-0 text-right xl:w-44">
            <p className="text-[15px] font-bold">{item.value}</p>
            <p className="text-[13px] font-medium">{item.change}</p>
          </div>
        </li>
      ))}
    </ul>
  );
}

const ERROR_ICON: Record<string, typeof AlertTriangle> = {
  config_error: KeyRound,
  account_mismatch: Lock,
  rate_limited: Timer,
  network: WifiOff,
  connection_error: WifiOff,
};

const ERROR_TITLE: Record<string, string> = {
  config_error: "설정을 확인해야 합니다",
  account_mismatch: "계좌번호가 일치하지 않습니다",
  rate_limited: "호출 한도를 넘었습니다",
  network: "서버에 연결할 수 없습니다",
  connection_error: "키움 서버에 연결하지 못했습니다",
  incomplete_result: "조회 결과를 모두 가져오지 못했습니다",
  response_format_error: "키움 응답을 해석하지 못했습니다",
  kiwoom_error: "키움 API 오류",
  bad_request: "요청 조건이 올바르지 않습니다",
};

export function ErrorNotice({
  error,
  onRetry,
  compact = false,
  fallbackTitle = "계좌 정보를 불러오지 못했습니다",
}: {
  error: ApiError;
  onRetry: () => void;
  compact?: boolean;
  fallbackTitle?: string;
}) {
  const Icon = ERROR_ICON[error.kind] ?? AlertTriangle;
  return (
    <div role="alert" className={`rounded-3xl bg-surface ${compact ? "p-4" : "p-6 md:p-8"}`}>
      <div className={`flex gap-4 ${compact ? "items-center" : "flex-col items-start md:flex-row"}`}>
        <span className="flex size-11 shrink-0 items-center justify-center rounded-full bg-real-soft text-real">
          <Icon className="size-5" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[17px] font-bold">
            {ERROR_TITLE[error.kind] ?? fallbackTitle}
          </p>
          <p className="mt-1 text-[15px] break-words text-sub">{error.message}</p>
          {error.missing.length > 0 && (
            <p className="mt-2 text-sm text-sub">
              프로젝트 최상위 <code className="rounded-md bg-canvas px-1.5 py-0.5">.env</code>에 다음
              값을 채워 주세요:{" "}
              {error.missing.map((name) => (
                <code key={name} className="mr-1 rounded-md bg-canvas px-1.5 py-0.5">
                  {name}
                </code>
              ))}
            </p>
          )}
          {error.requestId && <p className="mt-2 text-xs text-muted">요청 ID: {error.requestId}</p>}
        </div>
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-xl bg-brand-50 px-4 py-2.5 text-sm font-semibold text-brand-700 hover:bg-brand-100"
        >
          <RefreshCw className="size-4" aria-hidden />
          다시 시도
        </button>
      </div>
    </div>
  );
}

export function AccountSkeleton() {
  const bar = "rounded-lg bg-canvas";
  return (
    <div className="animate-pulse" aria-label="계좌 정보를 불러오는 중" role="status">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="space-y-4">
          <div className="rounded-3xl bg-surface p-6">
            <div className={`h-4 w-24 ${bar}`} />
            <div className={`mt-3 h-9 w-56 ${bar}`} />
            <div className="mt-3 h-7 w-40 rounded-full bg-canvas" />
            <div className="mt-6 grid grid-cols-2 gap-3">
              <div className="h-16 rounded-2xl bg-canvas" />
              <div className="h-16 rounded-2xl bg-canvas" />
            </div>
          </div>
          <div className="space-y-4 rounded-3xl bg-surface p-6">
            {Array.from({ length: 3 }, (_, i) => (
              <div key={i} className="flex items-center gap-3">
                <div className="size-10 rounded-full bg-canvas" />
                <div className="flex-1 space-y-2">
                  <div className={`h-4 w-28 ${bar}`} />
                  <div className={`h-3 w-20 ${bar}`} />
                </div>
                <div className={`h-4 w-20 ${bar}`} />
              </div>
            ))}
          </div>
        </div>
        <div className="h-72 rounded-3xl bg-surface" />
      </div>
    </div>
  );
}
