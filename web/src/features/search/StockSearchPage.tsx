import { Inbox, LoaderCircle, Search } from "lucide-react";
import type { EnvironmentValue, StockSearchItem, StockSearchResult } from "../../api";
import { findEnvironment } from "../../environments";
import { ErrorNotice } from "../account/parts";
import { useStockSearch } from "./useStockSearch";

const PLACEHOLDER = {
  domestic: "종목명 또는 종목코드",
  us: "종목명(한글·영문) 또는 티커",
} as const;

export function StockSearchPage({ environment }: { environment: EnvironmentValue }) {
  const { query, state, change, retry } = useStockSearch(environment);
  const env = findEnvironment(environment);
  const result = state.status === "ready" ? state.result : state.status === "searching" ? state.previous : null;

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <div className="min-w-0">
        <h2 className="text-2xl font-bold md:text-[28px]">종목 검색</h2>
        <p className="mt-1.5 text-sm font-semibold text-sub">{env.label}</p>
      </div>

      <label className="flex items-center gap-2.5 rounded-2xl bg-surface px-4 py-3.5 shadow-[0_1px_3px_rgba(0,0,0,0.06)] focus-within:ring-2 focus-within:ring-brand-100">
        <Search className="size-5 shrink-0 text-muted" aria-hidden />
        <input
          type="search"
          value={query}
          onChange={(event) => change(event.target.value)}
          placeholder={PLACEHOLDER[env.market]}
          aria-label="종목 검색어"
          maxLength={50}
          autoFocus
          className="min-w-0 flex-1 bg-transparent text-[16px] outline-none placeholder:text-muted"
        />
        {state.status === "searching" && (
          <LoaderCircle className="size-5 shrink-0 animate-spin text-brand-600" aria-label="검색 중" role="status" />
        )}
      </label>

      {state.status === "idle" && (
        <Notice
          title="찾을 종목을 입력하세요"
          body={
            env.market === "domestic"
              ? "코스피·코스닥 종목(ETF·ETN·리츠 포함)을 종목명이나 종목코드로 찾습니다."
              : "NYSE·NASDAQ·AMEX 종목(ETF 포함)을 한글·영문 종목명이나 티커로 찾습니다."
          }
        />
      )}
      {state.status === "error" && (
        <ErrorNotice error={state.error} onRetry={retry} fallbackTitle="종목을 검색하지 못했습니다" />
      )}
      {result && <Results result={result} dimmed={state.status === "searching"} />}
    </div>
  );
}

function Results({ result, dimmed }: { result: StockSearchResult; dimmed: boolean }) {
  if (result.items.length === 0) {
    return <Notice title="검색 결과가 없습니다" body={`"${result.query}"에 맞는 종목이 없습니다.`} />;
  }
  return (
    <section
      aria-label="검색 결과"
      aria-busy={dimmed}
      className={`rounded-3xl bg-surface p-3 transition-opacity md:p-4 ${dimmed ? "opacity-60" : ""}`}
    >
      <p className="px-2 pb-2 text-sm text-muted">
        <span className="font-semibold text-sub">{result.total.toLocaleString("ko-KR")}개</span> 종목
      </p>
      <ul>
        {result.items.map((item) => (
          <ResultRow key={`${item.exchange ?? item.category}-${item.code}`} item={item} />
        ))}
      </ul>
      {result.truncated && (
        <p className="px-2 pt-3 text-sm text-muted">
          50개까지만 표시합니다. 검색어를 더 구체적으로 입력하세요.
        </p>
      )}
    </section>
  );
}

function ResultRow({ item }: { item: StockSearchItem }) {
  const meta = [item.code, item.exchange ?? item.category, item.industry].filter(Boolean).join(" · ");
  return (
    <li className="flex items-center gap-3 rounded-2xl px-2 py-2.5">
      <span
        className="flex size-10 shrink-0 items-center justify-center rounded-full bg-brand-50 text-[15px] font-bold text-brand-600"
        aria-hidden
      >
        {item.name.slice(0, 1)}
      </span>
      <div className="min-w-0 flex-1">
        <p className="flex min-w-0 items-center gap-1.5">
          <span className="truncate text-[15px] font-semibold">{item.name}</span>
          {item.is_etf && <Badge tone="brand">ETF</Badge>}
          {item.status && <Badge tone="real">{item.status}</Badge>}
        </p>
        {item.english_name && <p className="truncate text-xs text-sub">{item.english_name}</p>}
        <p className="truncate text-xs text-muted">{meta}</p>
      </div>
    </li>
  );
}

function Badge({ tone, children }: { tone: "brand" | "real"; children: string }) {
  const color = tone === "brand" ? "bg-brand-50 text-brand-700" : "bg-real-soft text-real";
  return <span className={`shrink-0 rounded-md px-1.5 py-0.5 text-[11px] font-bold ${color}`}>{children}</span>;
}

function Notice({ title, body }: { title: string; body: string }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-3xl bg-surface px-6 py-12 text-center">
      <span className="flex size-12 items-center justify-center rounded-full bg-canvas text-muted">
        <Inbox className="size-6" aria-hidden />
      </span>
      <p className="mt-1 text-[15px] font-semibold">{title}</p>
      <p className="text-sm text-muted">{body}</p>
    </div>
  );
}
