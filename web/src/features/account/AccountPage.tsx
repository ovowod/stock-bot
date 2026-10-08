import { Clock, Landmark, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import type { DomesticAccount, EnvironmentValue, UsAccount } from "../../api";
import { findEnvironment } from "../../environments";
import { useOrderPanel } from "../order/OrderPanel";
import { formatCount, formatForeign, formatKrw, formatRate, formatTime } from "../../format";
import {
  AccountSkeleton,
  Change,
  ChangePill,
  ErrorNotice,
  HoldingsList,
  MiniStat,
  Panel,
  Row,
} from "./parts";
import { OpenOrdersPanel } from "./OpenOrders";
import { useAccount, useOpenOrders } from "./useAccount";

export function AccountPage({ environment }: { environment: EnvironmentValue }) {
  const { state, refresh, retry } = useAccount(environment);
  const env = findEnvironment(environment);
  const openOrders = useOpenOrders(environment);
  const ready = state.status === "ready" ? state : null;
  // 상단 새로고침과 주문 접수는 계좌와 미체결 주문을 함께 다시 불러온다.
  const refreshAll = () => {
    void refresh();
    void openOrders.refresh();
  };
  const openOrdersPanel = (
    <OpenOrdersPanel
      env={env}
      state={openOrders.state}
      onRetry={openOrders.retry}
      onRefresh={openOrders.refresh}
      onChanged={refreshAll}
    />
  );

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <div className="flex items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-2xl font-bold md:text-[28px]">계좌 확인</h2>
          <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted">
            <span className="font-semibold text-sub">{env.label}</span>
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
          onClick={refreshAll}
          disabled={state.status !== "ready" || state.refreshing}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-xl bg-surface px-3.5 py-2.5 text-sm font-semibold text-sub shadow-[0_1px_3px_rgba(0,0,0,0.06)] hover:text-ink disabled:cursor-not-allowed disabled:opacity-60"
        >
          <RefreshCw className={`size-4 ${ready?.refreshing ? "animate-spin" : ""}`} aria-hidden />
          {ready?.refreshing ? "새로고침 중" : "새로고침"}
        </button>
      </div>

      {state.status === "loading" && <AccountSkeleton />}
      {state.status === "error" && <ErrorNotice error={state.error} onRetry={retry} />}
      {ready && (
        <div
          className={`space-y-4 transition-opacity ${ready.refreshing ? "opacity-60" : ""}`}
          aria-busy={ready.refreshing}
        >
          {ready.refreshError && <ErrorNotice error={ready.refreshError} onRetry={refresh} compact />}
          {ready.data.market === "domestic" ? (
            <DomesticView data={ready.data} onSold={refreshAll} openOrders={openOrdersPanel} />
          ) : (
            <UsView data={ready.data} onSold={refreshAll} openOrders={openOrdersPanel} />
          )}
        </div>
      )}
    </div>
  );
}

/**
 * 모바일: 요약 → 예수금 → 보유종목 → 미체결 주문.
 * 넓은 화면: 왼쪽에 요약·보유종목, 오른쪽에 예수금, 맨 아래 전체 너비로 미체결 주문.
 */
function Layout({
  hero,
  deposit,
  holdings,
  openOrders,
}: {
  hero: ReactNode;
  deposit: ReactNode;
  holdings: ReactNode;
  openOrders?: ReactNode;
}) {
  return (
    <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
      <div className="lg:col-start-1">{hero}</div>
      <div className="lg:col-start-2 lg:row-span-2 lg:row-start-1">{deposit}</div>
      <div className="lg:col-start-1">{holdings}</div>
      {openOrders && <div className="lg:col-span-2">{openOrders}</div>}
    </div>
  );
}

function HeroValue({ children }: { children: ReactNode }) {
  return (
    <p data-testid="hero-value" className="mt-1 text-[30px] leading-tight font-bold tracking-tight md:text-[34px]">
      {children}
    </p>
  );
}

function DomesticView({
  data,
  onSold,
  openOrders,
}: {
  data: DomesticAccount;
  onSold: () => void;
  openOrders: ReactNode;
}) {
  const { summary, deposit } = data;
  const openOrder = useOrderPanel();
  return (
    <Layout
      openOrders={openOrders}
      hero={
        <Panel>
          <p className="text-[15px] font-medium text-sub">추정예탁자산</p>
          <HeroValue>{formatKrw(summary.estimated_assets)}</HeroValue>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <span className="text-sm text-muted">총평가손익</span>
            <ChangePill
              amount={summary.total_profit_loss}
              rate={summary.total_return_rate}
              formatAmount={formatKrw}
              formatRate={formatRate}
            />
          </div>
          <div className="mt-5 grid grid-cols-2 gap-3">
            <MiniStat label="총평가금액" value={formatKrw(summary.total_evaluation)} />
            <MiniStat label="총매입금액" value={formatKrw(summary.total_purchase)} />
          </div>
        </Panel>
      }
      deposit={
        <Panel title="예수금">
          <div className="rounded-2xl bg-brand-50 px-4 py-4">
            <p className="text-[13px] font-medium text-brand-700">주문가능금액</p>
            <p className="mt-0.5 text-[22px] font-bold">{formatKrw(deposit.orderable)}</p>
          </div>
          <dl className="mt-2">
            <Row label="예수금" value={formatKrw(deposit.deposit)} />
            <Row label="출금가능금액" value={formatKrw(deposit.withdrawable)} />
            <Row label="D+1 추정예수금" value={formatKrw(deposit.d1_estimated)} />
            <Row label="D+2 추정예수금" value={formatKrw(deposit.d2_estimated)} />
          </dl>
        </Panel>
      }
      holdings={
        <Panel title="보유종목" aside={`${data.holdings.length}개`}>
          <HoldingsList
            items={data.holdings.map((h, i) => ({
              key: `${h.code}-${i}`,
              name: h.name,
              quantity: formatCount(h.quantity),
              position: [formatCount(h.quantity), `현재가 ${formatKrw(h.current_price)}`],
              info: [
                h.code,
                `평균 ${formatKrw(h.purchase_price)}`,
                `보유비중 ${formatRate(h.weight)}`,
                ...sellable(h.quantity, h.tradable_quantity),
              ],
              details: [
                { label: "현재가", value: formatKrw(h.current_price) },
                { label: "평균단가", value: formatKrw(h.purchase_price) },
                { label: "종목코드", value: h.code },
                { label: "보유비중", value: formatRate(h.weight) },
                ...sellableDetail(h.quantity, h.tradable_quantity),
              ],
              onSell: () =>
                openOrder({
                  code: h.code,
                  name: h.name,
                  exchange: null,
                  category: null,
                  status: null,
                  sell: { quantity: h.quantity, sellableQuantity: h.tradable_quantity, onAccepted: onSold },
                }),
              value: formatKrw(h.evaluation_amount),
              change: (
                <>
                  <Change value={h.profit_loss} format={formatKrw} />{" "}
                  <Change value={h.return_rate} format={formatRate} parens />
                </>
              ),
            }))}
          />
        </Panel>
      }
    />
  );
}

function UsView({ data, onSold, openOrders }: { data: UsAccount; onSold: () => void; openOrders: ReactNode }) {
  const { summary, summary_krw: krw, deposit } = data;
  const openOrder = useOrderPanel();
  const currency = data.currency || "USD";
  const usd = (value: number | null) => formatForeign(value, currency);
  const price = (value: number | null) => formatForeign(value, currency, 4);
  return (
    <Layout
      openOrders={openOrders}
      hero={
        <Panel>
          <p className="text-[15px] font-medium text-sub">총평가금액</p>
          <HeroValue>{usd(summary.total_evaluation)}</HeroValue>
          <p className="mt-0.5 text-sm text-muted">{formatKrw(krw.total_evaluation)}</p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <span className="text-sm text-muted">총평가손익</span>
            <ChangePill
              amount={summary.total_profit_loss}
              rate={summary.total_return_rate}
              formatAmount={usd}
              formatRate={formatRate}
            />
            <Change value={krw.total_profit_loss} format={formatKrw} className="text-sm" />
          </div>
          <div className="mt-5 grid grid-cols-2 gap-3">
            <MiniStat
              label="총매입금액"
              value={usd(summary.total_purchase)}
              sub={formatKrw(krw.total_purchase)}
            />
            <MiniStat
              label="당일 실현손익"
              value={<Change value={summary.today_realized_profit_loss} format={usd} />}
              sub={<Change value={krw.today_realized_profit_loss} format={formatKrw} />}
            />
          </div>
        </Panel>
      }
      deposit={
        <Panel title="예수금">
          <div className="rounded-2xl bg-brand-50 px-4 py-4">
            <p className="text-[13px] font-medium text-brand-700">원화예수금</p>
            <p className="mt-0.5 text-[22px] font-bold">{formatKrw(deposit.krw_deposit)}</p>
          </div>
          {deposit.currencies.length === 0 ? (
            <p className="mt-4 text-sm text-muted">외화예수금 내역이 없습니다.</p>
          ) : (
            <dl className="mt-2">
              {deposit.currencies.map((c) => (
                <Row
                  key={c.currency}
                  label={`${c.currency} ${c.currency_name}`}
                  value={formatForeign(c.deposit, c.currency)}
                  sub={
                    <>
                      <span className="block whitespace-nowrap">
                        주문가능 {formatForeign(c.orderable, c.currency)}
                      </span>
                      <span className="block whitespace-nowrap">
                        출금가능 {formatForeign(c.withdrawable, c.currency)}
                      </span>
                    </>
                  }
                />
              ))}
            </dl>
          )}
        </Panel>
      }
      holdings={
        <Panel title="보유종목" aside={`${data.holdings.length}개`}>
          <HoldingsList
            items={data.holdings.map((h, i) => ({
              key: `${h.code}-${i}`,
              name: h.name,
              quantity: formatCount(h.quantity),
              position: [formatCount(h.quantity), `현재가 ${price(h.current_price)}`],
              info: [
                h.code,
                h.exchange,
                `평균 ${price(h.purchase_price)}`,
                ...sellable(h.quantity, h.sellable_quantity),
              ],
              details: [
                { label: "현재가", value: price(h.current_price) },
                { label: "평균단가", value: price(h.purchase_price) },
                { label: "종목코드", value: h.code },
                { label: "거래소", value: h.exchange },
                { label: "원화 평가금액", value: formatKrw(h.evaluation_amount_krw) },
                ...sellableDetail(h.quantity, h.sellable_quantity),
              ],
              onSell: () =>
                openOrder({
                  code: h.code,
                  name: h.name,
                  exchange: h.exchange,
                  category: null,
                  status: null,
                  sell: { quantity: h.quantity, sellableQuantity: h.sellable_quantity, onAccepted: onSold },
                }),
              value: usd(h.evaluation_amount),
              valueSub: formatKrw(h.evaluation_amount_krw),
              change: (
                <>
                  <Change value={h.profit_loss} format={usd} />{" "}
                  <Change value={h.return_rate} format={formatRate} parens />
                </>
              ),
            }))}
          />
        </Panel>
      }
    />
  );
}

/** 매도가능 수량은 보유 수량보다 적을 때(예: 매도 주문이 걸려 있을 때)만 보여준다. */
function sellableDetail(
  quantity: number | null,
  sellableQuantity: number | null,
): { label: string; value: string }[] {
  return quantity !== null && sellableQuantity !== null && sellableQuantity < quantity
    ? [{ label: "매도가능", value: formatCount(sellableQuantity) }]
    : [];
}

function sellable(quantity: number | null, sellableQuantity: number | null): string[] {
  return sellableDetail(quantity, sellableQuantity).map((detail) => `${detail.label} ${detail.value}`);
}
