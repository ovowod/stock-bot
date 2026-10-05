import { AlertTriangle, Inbox, KeyRound, Lock, RefreshCw, Timer, WifiOff } from "lucide-react";
import type { ReactNode } from "react";
import type { ApiError } from "../../api";
import { direction, EMPTY } from "../../format";

const TONE = {
  up: { text: "text-gain", soft: "bg-gain-soft", mark: "▲", sign: "+" },
  down: { text: "text-loss", soft: "bg-loss-soft", mark: "▼", sign: "−" },
  flat: { text: "text-muted", soft: "bg-canvas", mark: "", sign: "" },
} as const;

/** 수익·상승은 빨강 ▲ +, 손실·하락은 파랑 ▼ −, 0은 회색. 색을 못 구분해도 기호로 알 수 있다. */
export function Change({
  value,
  format,
  className = "",
}: {
  value: number | null;
  format: (value: number | null) => string;
  className?: string;
}) {
  if (value === null) return <span className={`text-muted ${className}`}>{EMPTY}</span>;
  const tone = TONE[direction(value)];
  return (
    <span className={`whitespace-nowrap ${tone.text} ${className}`}>
      {tone.mark && (
        <span className="mr-0.5 text-[0.75em]" aria-hidden>
          {tone.mark}
        </span>
      )}
      {tone.sign}
      {format(Math.abs(value))}
    </span>
  );
}

export function toneSoft(value: number | null): string {
  return TONE[direction(value)].soft;
}

export function Card({
  title,
  icon,
  children,
  className = "",
}: {
  title?: string;
  icon?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-line bg-surface ${className}`}>
      {title && (
        <h3 className="flex items-center gap-2 border-b border-line px-4 py-3 text-sm font-semibold md:px-5">
          {icon}
          {title}
        </h3>
      )}
      {children}
    </section>
  );
}

export function StatCard({
  label,
  value,
  sub,
  highlight,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  highlight?: string;
}) {
  return (
    <div className={`rounded-xl border border-line p-4 md:p-5 ${highlight ?? "bg-surface"}`}>
      <p className="text-xs font-medium text-muted md:text-sm">{label}</p>
      <p className="mt-1.5 text-lg font-semibold tracking-tight break-words md:text-2xl">{value}</p>
      {sub && <p className="mt-1 text-xs text-muted md:text-sm">{sub}</p>}
    </div>
  );
}

export function KeyValue({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2.5">
      <dt className="text-sm text-muted">{label}</dt>
      <dd className="text-right text-sm font-medium">{value}</dd>
    </div>
  );
}

export interface Column<T> {
  header: string;
  align?: "left" | "right";
  cell: (row: T) => ReactNode;
}

/** 데스크톱은 표, 모바일은 카드 목록. 화면 전체가 가로로 밀리지 않는다. */
export function HoldingsTable<T>({
  rows,
  rowKey,
  title,
  subtitle,
  aside,
  columns,
}: {
  rows: T[];
  rowKey: (row: T, index: number) => string;
  title: (row: T) => ReactNode;
  subtitle: (row: T) => ReactNode;
  aside: (row: T) => ReactNode;
  columns: Column<T>[];
}) {
  if (rows.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 px-4 py-12 text-center text-muted">
        <Inbox className="size-8" aria-hidden />
        <p className="text-sm font-medium text-ink">보유종목이 없습니다</p>
        <p className="text-xs">이 계좌에는 현재 보유 중인 종목이 없습니다.</p>
      </div>
    );
  }
  return (
    <>
      <div className="hidden overflow-x-auto md:block">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-xs text-muted">
              <th className="px-5 py-2.5 text-left font-medium">종목</th>
              {columns.map((column) => (
                <th
                  key={column.header}
                  className={`px-3 py-2.5 font-medium whitespace-nowrap last:pr-5 ${
                    column.align === "left" ? "text-left" : "text-right"
                  }`}
                >
                  {column.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={rowKey(row, index)} className="border-b border-line last:border-0 hover:bg-canvas/60">
                <td className="px-5 py-3">
                  <div className="font-medium">{title(row)}</div>
                  <div className="text-xs text-muted">{subtitle(row)}</div>
                </td>
                {columns.map((column) => (
                  <td
                    key={column.header}
                    className={`px-3 py-3 whitespace-nowrap last:pr-5 ${
                      column.align === "left" ? "text-left" : "text-right"
                    }`}
                  >
                    {column.cell(row)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="divide-y divide-line md:hidden">
        {rows.map((row, index) => (
          <li key={rowKey(row, index)} className="px-4 py-3.5">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="truncate font-medium">{title(row)}</div>
                <div className="text-xs text-muted">{subtitle(row)}</div>
              </div>
              <div className="text-right text-sm font-semibold">{aside(row)}</div>
            </div>
            <dl className="mt-2.5 grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
              {columns.map((column) => (
                <div key={column.header} className="flex justify-between gap-2">
                  <dt className="text-muted">{column.header}</dt>
                  <dd className="text-right font-medium">{column.cell(row)}</dd>
                </div>
              ))}
            </dl>
          </li>
        ))}
      </ul>
    </>
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
};

export function ErrorNotice({
  error,
  onRetry,
  compact = false,
}: {
  error: ApiError;
  onRetry: () => void;
  compact?: boolean;
}) {
  const Icon = ERROR_ICON[error.kind] ?? AlertTriangle;
  return (
    <div
      role="alert"
      className={`rounded-xl border border-real/25 bg-real-soft text-real ${compact ? "p-3" : "p-5 md:p-6"}`}
    >
      <div className="flex items-start gap-3">
        <Icon className="mt-0.5 size-5 shrink-0" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="font-semibold">{ERROR_TITLE[error.kind] ?? "계좌 정보를 불러오지 못했습니다"}</p>
          <p className="mt-1 text-sm break-words text-ink/80">{error.message}</p>
          {error.missing.length > 0 && (
            <p className="mt-2 text-sm text-ink/80">
              프로젝트 최상위 <code className="rounded bg-surface px-1">.env</code>에 다음 값을 채워
              주세요:{" "}
              {error.missing.map((name) => (
                <code key={name} className="mr-1 rounded bg-surface px-1">
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
          className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-real/30 bg-surface px-3 py-1.5 text-sm font-medium text-real hover:bg-real-soft"
        >
          <RefreshCw className="size-4" aria-hidden />
          다시 시도
        </button>
      </div>
    </div>
  );
}

export function AccountSkeleton() {
  return (
    <div className="animate-pulse space-y-5" aria-label="계좌 정보를 불러오는 중" role="status">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="h-24 rounded-xl border border-line bg-surface p-4 md:h-28">
            <div className="h-3 w-20 rounded bg-line" />
            <div className="mt-3 h-6 w-28 rounded bg-line" />
          </div>
        ))}
      </div>
      <div className="h-40 rounded-xl border border-line bg-surface" />
      <div className="h-64 rounded-xl border border-line bg-surface" />
    </div>
  );
}
