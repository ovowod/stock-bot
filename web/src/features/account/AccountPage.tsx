import { Banknote, Clock, Landmark, List, RefreshCw } from "lucide-react";
import type { DomesticAccount, EnvironmentValue, UsAccount } from "../../api";
import { findEnvironment } from "../../environments";
import { formatCount, formatForeign, formatKrw, formatRate, formatTime } from "../../format";
import {
  AccountSkeleton,
  Card,
  Change,
  ErrorNotice,
  HoldingsTable,
  KeyValue,
  StatCard,
  toneSoft,
} from "./parts";
import { useAccount } from "./useAccount";

export function AccountPage({ environment }: { environment: EnvironmentValue }) {
  const { state, refresh, retry } = useAccount(environment);
  const env = findEnvironment(environment);
  const ready = state.status === "ready" ? state : null;

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold tracking-tight md:text-2xl">계좌 확인</h2>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted">
            <span>{env.label}</span>
            {ready && (
              <>
                <span className="inline-flex items-center gap-1">
                  <Landmark className="size-3.5" aria-hidden />
                  {ready.data.account_no}
                </span>
                <span className="inline-flex items-center gap-1">
                  <Clock className="size-3.5" aria-hidden />
                  {formatTime(ready.data.fetched_at)} 조회
                </span>
              </>
            )}
          </p>
        </div>
        <button
          type="button"
          onClick={refresh}
          disabled={state.status !== "ready" || state.refreshing}
          className="inline-flex items-center gap-2 rounded-lg border border-line bg-surface px-3.5 py-2 text-sm font-medium hover:bg-canvas disabled:cursor-not-allowed disabled:opacity-60"
        >
          <RefreshCw
            className={`size-4 ${ready?.refreshing ? "animate-spin" : ""}`}
            aria-hidden
          />
          {ready?.refreshing ? "새로고침 중" : "새로고침"}
        </button>
      </div>

      {state.status === "loading" && <AccountSkeleton />}
      {state.status === "error" && <ErrorNotice error={state.error} onRetry={retry} />}
      {ready && (
        <div
          className={`space-y-5 transition-opacity ${ready.refreshing ? "opacity-60" : ""}`}
          aria-busy={ready.refreshing}
        >
          {ready.refreshError && <ErrorNotice error={ready.refreshError} onRetry={refresh} compact />}
          {ready.data.market === "domestic" ? (
            <DomesticView data={ready.data} />
          ) : (
            <UsView data={ready.data} />
          )}
        </div>
      )}
    </div>
  );
}

function DomesticView({ data }: { data: DomesticAccount }) {
  const { summary, deposit } = data;
  return (
    <>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="추정예탁자산" value={formatKrw(summary.estimated_assets)} />
        <StatCard label="총평가금액" value={formatKrw(summary.total_evaluation)} />
        <StatCard label="총매입금액" value={formatKrw(summary.total_purchase)} />
        <StatCard
          label="총평가손익"
          highlight={toneSoft(summary.total_profit_loss)}
          value={<Change value={summary.total_profit_loss} format={formatKrw} />}
          sub={<Change value={summary.total_return_rate} format={formatRate} />}
        />
      </div>

      <Card title="예수금" icon={<Banknote className="size-4 text-brand-600" aria-hidden />}>
        <dl className="grid px-4 md:grid-cols-2 md:gap-x-10 md:px-5 [&>div]:border-b [&>div]:border-line [&>div:last-child]:border-0">
          <KeyValue label="예수금" value={formatKrw(deposit.deposit)} />
          <KeyValue label="주문가능금액" value={formatKrw(deposit.orderable)} />
          <KeyValue label="출금가능금액" value={formatKrw(deposit.withdrawable)} />
          <KeyValue label="D+1 추정예수금" value={formatKrw(deposit.d1_estimated)} />
          <KeyValue label="D+2 추정예수금" value={formatKrw(deposit.d2_estimated)} />
        </dl>
      </Card>

      <Card title={`보유종목 ${data.holdings.length}`} icon={<List className="size-4 text-brand-600" aria-hidden />}>
        <HoldingsTable
          rows={data.holdings}
          rowKey={(h, i) => `${h.code}-${i}`}
          title={(h) => h.name}
          subtitle={(h) => h.code}
          aside={(h) => (
            <>
              <Change value={h.profit_loss} format={formatKrw} />
              <div className="text-xs">
                <Change value={h.return_rate} format={formatRate} />
              </div>
            </>
          )}
          columns={[
            { header: "보유수량", cell: (h) => formatCount(h.quantity) },
            { header: "매입가", cell: (h) => formatKrw(h.purchase_price) },
            { header: "현재가", cell: (h) => formatKrw(h.current_price) },
            { header: "평가금액", cell: (h) => formatKrw(h.evaluation_amount) },
            {
              header: "평가손익",
              cell: (h) => <Change value={h.profit_loss} format={formatKrw} />,
            },
            {
              header: "수익률",
              cell: (h) => <Change value={h.return_rate} format={formatRate} />,
            },
            { header: "보유비중", cell: (h) => formatRate(h.weight) },
          ]}
        />
      </Card>
    </>
  );
}

function UsView({ data }: { data: UsAccount }) {
  const { summary, summary_krw: krw, deposit } = data;
  const usd = (value: number | null) => formatForeign(value, data.currency || "USD");
  const price = (value: number | null) => formatForeign(value, data.currency || "USD", 4);
  return (
    <>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          label="총평가금액"
          value={usd(summary.total_evaluation)}
          sub={formatKrw(krw.total_evaluation)}
        />
        <StatCard
          label="총매입금액"
          value={usd(summary.total_purchase)}
          sub={formatKrw(krw.total_purchase)}
        />
        <StatCard
          label="총평가손익"
          highlight={toneSoft(summary.total_profit_loss)}
          value={<Change value={summary.total_profit_loss} format={usd} />}
          sub={
            <>
              <Change value={summary.total_return_rate} format={formatRate} /> ·{" "}
              <Change value={krw.total_profit_loss} format={formatKrw} />
            </>
          }
        />
        <StatCard
          label="당일 실현손익"
          value={<Change value={summary.today_realized_profit_loss} format={usd} />}
          sub={
            <>
              <Change value={summary.today_realized_return_rate} format={formatRate} /> ·{" "}
              <Change value={krw.today_realized_profit_loss} format={formatKrw} />
            </>
          }
        />
      </div>

      <Card title="예수금" icon={<Banknote className="size-4 text-brand-600" aria-hidden />}>
        <dl className="border-b border-line px-4 md:px-5">
          <KeyValue label="원화예수금" value={formatKrw(deposit.krw_deposit)} />
        </dl>
        {deposit.currencies.length === 0 ? (
          <p className="px-4 py-4 text-sm text-muted md:px-5">외화예수금 내역이 없습니다.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-xs text-muted">
                  <th className="px-4 py-2.5 text-left font-medium md:px-5">통화</th>
                  <th className="px-3 py-2.5 text-right font-medium">외화예수금</th>
                  <th className="px-3 py-2.5 text-right font-medium">주문가능</th>
                  <th className="px-4 py-2.5 text-right font-medium md:px-5">출금가능</th>
                </tr>
              </thead>
              <tbody>
                {deposit.currencies.map((c) => (
                  <tr key={c.currency} className="border-b border-line last:border-0">
                    <td className="px-4 py-2.5 whitespace-nowrap md:px-5">
                      <span className="font-medium">{c.currency}</span>{" "}
                      <span className="text-xs text-muted">{c.currency_name}</span>
                    </td>
                    <td className="px-3 py-2.5 text-right whitespace-nowrap">
                      {formatForeign(c.deposit, c.currency)}
                    </td>
                    <td className="px-3 py-2.5 text-right whitespace-nowrap">
                      {formatForeign(c.orderable, c.currency)}
                    </td>
                    <td className="px-4 py-2.5 text-right whitespace-nowrap md:px-5">
                      {formatForeign(c.withdrawable, c.currency)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title={`보유종목 ${data.holdings.length}`} icon={<List className="size-4 text-brand-600" aria-hidden />}>
        <HoldingsTable
          rows={data.holdings}
          rowKey={(h, i) => `${h.code}-${i}`}
          title={(h) => h.name}
          subtitle={(h) => `${h.code} · ${h.exchange}`}
          aside={(h) => (
            <>
              <Change value={h.profit_loss} format={usd} />
              <div className="text-xs">
                <Change value={h.return_rate} format={formatRate} />
              </div>
            </>
          )}
          columns={[
            { header: "보유수량", cell: (h) => formatCount(h.quantity) },
            { header: "매도가능", cell: (h) => formatCount(h.sellable_quantity) },
            { header: "매입단가", cell: (h) => price(h.purchase_price) },
            { header: "현재가", cell: (h) => price(h.current_price) },
            { header: "평가금액", cell: (h) => usd(h.evaluation_amount) },
            {
              header: "평가손익",
              cell: (h) => <Change value={h.profit_loss} format={usd} />,
            },
            {
              header: "수익률",
              cell: (h) => <Change value={h.return_rate} format={formatRate} />,
            },
          ]}
        />
      </Card>
    </>
  );
}
