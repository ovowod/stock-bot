import { AlertTriangle, Inbox, KeyRound, Lock, RefreshCw, Timer, WifiOff } from "lucide-react";
import { useState, type ReactNode } from "react";
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
      {/* 좁은 화면에서도 금액을 말줄임으로 자르지 않는다. 글씨를 줄이고, 그래도 넘치면 줄을 바꾼다. */}
      <p className="mt-0.5 text-[15px] font-bold [overflow-wrap:anywhere] md:text-[17px]">{value}</p>
      {sub && <p className="text-xs text-muted [overflow-wrap:anywhere]">{sub}</p>}
    </div>
  );
}

export interface HoldingView {
  key: string;
  name: string;
  /** 모바일 목록 둘째 줄의 수량(예: "100주"). */
  quantity: string;
  /** 모바일에서 줄을 눌러 펼치는 세부 정보. */
  details: { label: string; value: string }[];
  /** 둘째 줄: 수량, 현재가. 오른쪽 평가금액(수량 × 현재가)과 짝을 이룬다. */
  position: string[];
  /** 셋째 줄: 종목코드, 거래소, 평균단가, 보유비중 등. */
  info: string[];
  /** 오른쪽 위: 평가금액. */
  value: ReactNode;
  /** 평가금액 아래 줄(예: 원화 환산 평가금액). */
  valueSub?: ReactNode;
  change: ReactNode;
}

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
        <li key={item.key}>
          {/* 넓은 화면: 세부 정보까지 3줄로 모두 보여준다. */}
          <div className="hidden items-start gap-3 rounded-2xl px-2 py-3 transition hover:bg-canvas/70 md:flex">
            <Avatar name={item.name} />
            {/* 왼쪽은 종목 정보, 오른쪽은 평가금액과 손익이다. 두 칸은 각자 위에서부터 쌓아, 이름이 길어도 어긋나지 않는다. */}
            <div className="min-w-0 flex-1">
              {/* 한글 이름은 띄어쓰기 자리에서만 줄을 바꾼다("아이셰어/즈"처럼 끊지 않는다). */}
              <p className="text-[15px] font-semibold break-words break-keep">{item.name}</p>
              <Parts parts={item.position} className={SUB_TEXT} />
              <Parts parts={item.info} className={SUB_TEXT} />
            </div>
            <div className="shrink-0 text-right">
              <p className="text-[15px] font-bold">{item.value}</p>
              {item.valueSub && <p className={SUB_TEXT}>{item.valueSub}</p>}
              <p className="text-[13px] font-medium">{item.change}</p>
            </div>
          </div>
          <MobileHolding item={item} />
        </li>
      ))}
    </ul>
  );
}

/**
 * 좁은 화면: 이름·수량과 평가금액·손익만 두 줄로 보여주고(토스증권 목록 방식),
 * 현재가·평균단가 같은 세부 정보는 줄을 눌러 펼친다.
 */
function MobileHolding({ item }: { item: HoldingView }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="md:hidden">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-start gap-3 rounded-2xl px-2 py-3 text-left transition hover:bg-canvas/70"
      >
        <Avatar name={item.name} />
        {/* 이름은 평가금액과, 수량은 손익과 짝을 지어 한 줄씩 둔다. 긴 손익이 이름 칸을 좁히지 않는다. */}
        <span className="grid min-w-0 flex-1 grid-cols-[1fr_auto] items-baseline gap-x-3">
          <span className="min-w-0 text-[15px] font-semibold break-words break-keep">{item.name}</span>
          <span className="text-right text-[15px] font-bold">{item.value}</span>
          <span className="text-[13px] text-muted">{item.quantity}</span>
          <span className="text-right text-[13px] font-medium">{item.change}</span>
        </span>
      </button>
      {open && (
        <dl className="mx-2 mb-2 grid grid-cols-2 gap-x-4 gap-y-2 rounded-2xl bg-canvas px-4 py-3">
          {item.details.map((detail) => (
            <div key={detail.label} className="min-w-0">
              <dt className="text-xs text-muted">{detail.label}</dt>
              <dd data-term={detail.label} className="text-sm font-medium break-words">
                {detail.value}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function Avatar({ name }: { name: string }) {
  return (
    <span
      className="flex size-10 shrink-0 items-center justify-center rounded-full bg-brand-50 text-[15px] font-bold text-brand-600"
      aria-hidden
    >
      {name.slice(0, 1)}
    </span>
  );
}

// 이름·평가금액보다 한 단계 작은 보조 정보.
const SUB_TEXT = "text-[13px] text-muted";

/** 가운뎃점으로 이은 한 줄. 좁으면 가운뎃점 자리에서만 줄을 바꾸고, 각 덩어리 안에서는 끊지 않는다. */
function Parts({ parts, className }: { parts: string[]; className: string }) {
  return (
    <p className={`min-w-0 ${className}`}>
      {parts.map((part, index) => (
        <span key={part}>
          {index > 0 && " · "}
          <span className="whitespace-nowrap">{part}</span>
        </span>
      ))}
    </p>
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
