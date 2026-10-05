import { Clock, Inbox, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import type { EnvironmentValue, RankingDirection, RankingItem, RankingKind } from "../../api";
import { findEnvironment } from "../../environments";
import {
  EMPTY,
  formatCompactCount,
  formatCompactKrw,
  formatCompactUsd,
  formatForeign,
  formatKrw,
  formatTime,
} from "../../format";
import { ErrorNotice } from "../account/parts";
import { KINDS, useRankings, type CardState } from "./useRankings";

type Market = "domestic" | "us";

const EXCHANGES: Record<Market, { value: string; label: string }[]> = {
  domestic: [
    { value: "all", label: "전체" },
    { value: "kospi", label: "코스피" },
    { value: "kosdaq", label: "코스닥" },
  ],
  us: [
    { value: "all", label: "전체" },
    { value: "nyse", label: "NYSE" },
    { value: "nasdaq", label: "NASDAQ" },
    { value: "amex", label: "AMEX" },
  ],
};

interface CardSpec {
  title: string;
  metricLabel: string;
  metric: (item: RankingItem, market: Market) => ReactNode;
}

const CARDS: Record<RankingKind, CardSpec> = {
  trading_value: {
    title: "거래대금 상위",
    metricLabel: "거래대금",
    metric: (item, market) => (
      <>
        <p className="text-sm font-semibold">
          {market === "domestic" ? formatCompactKrw(item.trading_value ?? null) : formatCompactUsd(item.trading_value ?? null)}
        </p>
        {market === "domestic" && (
          <p className="text-xs text-muted">
            전일 {item.previous_rank === null || item.previous_rank === undefined ? EMPTY : `${item.previous_rank}위`}
          </p>
        )}
      </>
    ),
  },
  gainers: {
    title: "상승률 상위",
    metricLabel: "등락률",
    metric: (item) => <RateChange direction={item.direction} rate={item.change_rate} className="text-[15px] font-bold" />,
  },
  volume: {
    title: "거래량 상위",
    metricLabel: "거래량",
    metric: (item) => <p className="text-sm font-semibold">{formatCompactCount(item.volume ?? null)}</p>,
  },
};

export function RankingPage({ environment }: { environment: EnvironmentValue }) {
  const { cards, exchange, busy, fetchedAt, setExchange, refresh, retry } = useRankings(environment);
  const env = findEnvironment(environment);
  const refreshing = KINDS.some((kind) => {
    const card = cards[kind];
    return card.status === "ready" && card.refreshing;
  });

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <div className="flex items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-2xl font-bold md:text-[28px]">순위</h2>
          <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted">
            <span className="font-semibold text-sub">{env.label}</span>
            {fetchedAt && (
              <span className="inline-flex items-center gap-1">
                <Clock className="size-3.5" aria-hidden />
                {formatTime(fetchedAt)} 조회
              </span>
            )}
          </p>
        </div>
        <button
          type="button"
          onClick={refresh}
          disabled={busy}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-xl bg-surface px-3.5 py-2.5 text-sm font-semibold text-sub shadow-[0_1px_3px_rgba(0,0,0,0.06)] hover:text-ink disabled:cursor-not-allowed disabled:opacity-60"
        >
          <RefreshCw className={`size-4 ${refreshing ? "animate-spin" : ""}`} aria-hidden />
          {refreshing ? "새로고침 중" : "새로고침"}
        </button>
      </div>

      <ExchangeSwitch options={EXCHANGES[env.market]} value={exchange} onChange={setExchange} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {KINDS.map((kind) => (
          <RankingCard
            key={kind}
            spec={CARDS[kind]}
            state={cards[kind]}
            market={env.market}
            onRetry={() => retry(kind)}
          />
        ))}
      </div>
    </div>
  );
}

function ExchangeSwitch({
  options,
  value,
  onChange,
}: {
  options: { value: string; label: string }[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div role="radiogroup" aria-label="거래소" className="inline-flex gap-1 rounded-2xl bg-surface p-1">
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => !active && onChange(option.value)}
            className={`rounded-xl px-3.5 py-2 text-sm font-semibold whitespace-nowrap transition ${
              active ? "bg-brand-50 text-brand-700" : "text-muted hover:text-sub"
            }`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

function RankingCard({
  spec,
  state,
  market,
  onRetry,
}: {
  spec: CardSpec;
  state: CardState;
  market: Market;
  onRetry: () => void;
}) {
  return (
    <section aria-label={spec.title} className="flex h-[560px] flex-col rounded-3xl bg-surface p-5">
      <div className="mb-2 flex items-center justify-between gap-3">
        <h3 className="text-[17px] font-bold">{spec.title}</h3>
        <span className="text-xs text-muted">{spec.metricLabel}</span>
      </div>
      {state.status === "loading" && <CardSkeleton title={spec.title} />}
      {state.status === "error" && (
        <div className="flex flex-1 flex-col justify-center">
          <ErrorNotice error={state.error} onRetry={onRetry} compact fallbackTitle="순위를 불러오지 못했습니다" />
        </div>
      )}
      {state.status === "ready" && (
        <>
          {state.refreshError && (
            <ErrorNotice
              error={state.refreshError}
              onRetry={onRetry}
              compact
              fallbackTitle="순위를 불러오지 못했습니다"
            />
          )}
          {state.data.items.length === 0 ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-2 text-center">
              <span className="flex size-12 items-center justify-center rounded-full bg-canvas text-muted">
                <Inbox className="size-6" aria-hidden />
              </span>
              <p className="mt-1 text-[15px] font-semibold">순위 데이터가 없습니다</p>
              <p className="text-sm text-muted">장 시작 전이거나 조건에 맞는 종목이 없습니다.</p>
            </div>
          ) : (
            <ol
              className={`-mx-2 min-h-0 flex-1 overflow-y-auto transition-opacity ${
                state.refreshing ? "opacity-60" : ""
              }`}
              aria-busy={state.refreshing}
            >
              {state.data.items.map((item, index) => (
                <RankingRow key={`${item.code}-${index}`} item={item} market={market} metric={spec.metric} />
              ))}
            </ol>
          )}
        </>
      )}
    </section>
  );
}

function RankingRow({
  item,
  market,
  metric,
}: {
  item: RankingItem;
  market: Market;
  metric: CardSpec["metric"];
}) {
  const price = market === "domestic" ? formatKrw(item.price) : formatForeign(item.price, "USD", 4);
  return (
    <li className="flex items-center gap-3 rounded-2xl px-2 py-2.5 hover:bg-canvas/70">
      <span className="w-6 shrink-0 text-center text-[15px] font-bold text-brand-600">{item.rank ?? EMPTY}</span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[15px] font-semibold">{item.name}</p>
        <p className="truncate text-xs text-muted">
          {item.exchange ? `${item.code} · ${item.exchange}` : item.code}
        </p>
      </div>
      <div className="shrink-0 text-right">
        <p className="text-sm font-semibold">{price}</p>
        <RateChange direction={item.direction} rate={item.change_rate} className="text-xs font-medium" />
      </div>
      <div className="w-24 shrink-0 text-right">{metric(item, market)}</div>
    </li>
  );
}

const TONE: Record<RankingDirection, { text: string; mark: string; sign: string }> = {
  up: { text: "text-gain", mark: "▲", sign: "+" },
  down: { text: "text-loss", mark: "▼", sign: "−" },
  flat: { text: "text-muted", mark: "", sign: "" },
  unknown: { text: "text-muted", mark: "", sign: "" },
};

/** 등락 방향은 키움이 준 기호를 따른다. 알 수 없는 기호는 보합으로 단정하지 않고 중립으로 둔다. */
export function RateChange({
  direction,
  rate,
  className = "",
}: {
  direction: RankingDirection;
  rate: number | null;
  className?: string;
}) {
  if (rate === null) return <p className={`text-muted ${className}`}>{EMPTY}</p>;
  const tone = TONE[direction];
  return (
    <p className={`whitespace-nowrap ${tone.text} ${className}`} data-direction={direction}>
      {tone.mark && (
        <span className="mr-0.5 text-[0.75em]" aria-hidden>
          {tone.mark}
        </span>
      )}
      {tone.sign}
      {Math.abs(rate).toFixed(2)}%
    </p>
  );
}

function CardSkeleton({ title }: { title: string }) {
  const bar = "rounded-lg bg-canvas";
  return (
    <div className="flex-1 animate-pulse space-y-4 pt-2" role="status" aria-label={`${title} 불러오는 중`}>
      {Array.from({ length: 8 }, (_, i) => (
        <div key={i} className="flex items-center gap-3">
          <div className={`h-4 w-5 ${bar}`} />
          <div className="flex-1 space-y-1.5">
            <div className={`h-4 w-28 ${bar}`} />
            <div className={`h-3 w-16 ${bar}`} />
          </div>
          <div className={`h-4 w-16 ${bar}`} />
          <div className={`h-4 w-14 ${bar}`} />
        </div>
      ))}
    </div>
  );
}
