import { ShieldAlert } from "lucide-react";
import { useState } from "react";
import type { OpenOrder, OpenOrders } from "../../api";
import type { EnvironmentOption } from "../../environments";
import { formatCount, formatForeign, formatKrw } from "../../format";
import { Callout } from "../order/Sheet";
import { CancelSheet } from "./CancelSheet";
import { ErrorNotice, Panel } from "./parts";
import type { RemoteState } from "./useAccount";

const count = new Intl.NumberFormat("ko-KR");

type Market = EnvironmentOption["market"];

/** 국내 계좌 확인 맨 아래의 미체결 주문. 계좌와 따로 불러오므로 실패해도 이 패널에만 오류가 보인다. */
export function OpenOrdersPanel({
  env,
  state,
  onRetry,
  onRefresh,
  onChanged,
}: {
  env: EnvironmentOption;
  state: RemoteState<OpenOrders>;
  onRetry: () => void;
  onRefresh: () => void;
  /** 주문 취소가 접수됐거나 접수 여부를 모를 때. 계좌와 미체결 주문을 다시 불러온다. */
  onChanged: () => void;
}) {
  const ready = state.status === "ready" ? state : null;
  const isReal = env.isReal;
  const [cancelling, setCancelling] = useState<OpenOrder | null>(null);
  return (
    <Panel title="미체결 주문" label="미체결 주문" aside={ready ? `${ready.data.orders.length}건` : undefined}>
      {isReal && (
        <div className="mb-3">
          <Callout tone="muted" icon={ShieldAlert}>
            실전투자에서는 주문 취소를 할 수 없습니다.
          </Callout>
        </div>
      )}
      {state.status === "loading" && <OpenOrdersSkeleton />}
      {state.status === "error" && (
        <ErrorNotice error={state.error} onRetry={onRetry} compact fallbackTitle="미체결 주문을 불러오지 못했습니다" />
      )}
      {ready && (
        <div className={`space-y-3 transition-opacity ${ready.refreshing ? "opacity-60" : ""}`} aria-busy={ready.refreshing}>
          {ready.refreshError && (
            <ErrorNotice
              error={ready.refreshError}
              onRetry={onRefresh}
              compact
              fallbackTitle="미체결 주문을 불러오지 못했습니다"
            />
          )}
          {ready.data.orders.length === 0 ? (
            <p className="py-8 text-center text-[15px] text-muted">미체결 주문이 없습니다</p>
          ) : (
            <ul className="-mx-2">
              {ready.data.orders.map((order) => (
                <OpenOrderRow
                  key={order.order_no}
                  order={order}
                  market={env.market}
                  isReal={isReal}
                  onCancel={() => setCancelling(order)}
                />
              ))}
            </ul>
          )}
        </div>
      )}
      {cancelling && (
        <CancelSheet env={env} target={cancelling} onClose={() => setCancelling(null)} onChanged={onChanged} />
      )}
    </Panel>
  );
}

function OpenOrderRow({
  order,
  market,
  isReal,
  onCancel,
}: {
  order: OpenOrder;
  market: Market;
  isReal: boolean;
  onCancel: () => void;
}) {
  const quantity =
    order.ordered_quantity === null
      ? `미체결 ${formatCount(order.remaining_quantity)}`
      : `미체결 ${count.format(order.remaining_quantity)} / 주문 ${formatCount(order.ordered_quantity)}`;
  const info = [
    order.code,
    order.order_type,
    // 시장가처럼 가격이 없는 주문은 주문 유형만 보여준다.
    ...(order.price === null ? [] : [formatOrderPrice(order.price, market)]),
    order.time,
    ...(order.exchange === null ? [] : [order.exchange]),
  ];
  const blockedBadge = blockedLabel(order, market);
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-2xl px-2 py-3 transition hover:bg-canvas/70">
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-center gap-1.5">
          <SideBadge order={order} />
          <span className="text-[15px] font-semibold break-words break-keep">{order.name}</span>
          {blockedBadge && (
            <span className="rounded-full bg-canvas px-2 py-0.5 text-[11px] font-bold text-sub">{blockedBadge}</span>
          )}
        </p>
        <p className="mt-0.5 min-w-0 text-[13px] text-muted">
          {info.map((part, index) => (
            <span key={`${index}-${part}`}>
              {index > 0 && " · "}
              <span className="whitespace-nowrap">{part}</span>
            </span>
          ))}
        </p>
      </div>
      {/* 좁은 화면에서는 수량과 취소 버튼을 아래 줄 전체 너비로 내려 종목 정보가 좁아지지 않게 한다. */}
      <div className="flex w-full items-center justify-between gap-3 md:w-auto md:shrink-0 md:justify-end">
        <p className="text-[15px] font-bold whitespace-nowrap">{quantity}</p>
        {!isReal && !order.cancelable && <p className="text-xs whitespace-nowrap text-muted">키움 앱에서 취소하세요</p>}
        {/* 미국 취소는 서버가 받기 전까지 버튼을 열지 않는다(.scratch/us-cancel-order 티켓 02). */}
        {!isReal && order.cancelable && market === "domestic" && (
          <button
            type="button"
            aria-label={`${order.name} 주문 취소`}
            onClick={onCancel}
            className="shrink-0 rounded-xl bg-canvas px-3.5 py-2 text-sm font-bold text-sub hover:bg-ink hover:text-white"
          >
            취소
          </button>
        )}
      </div>
    </li>
  );
}

/** 미체결 주문의 가격. 국내는 원, 미국은 보유종목과 같은 USD 4자리 표시다. */
export function formatOrderPrice(price: number, market: Market): string {
  return market === "domestic" ? formatKrw(price) : formatForeign(price, "USD", 4);
}

function blockedLabel(order: OpenOrder, market: Market): string | null {
  switch (order.blocked_reason) {
    case "credit":
      return "신용";
    case "reserved":
      return "예약 주문";
    case "exchange":
      return market === "us" ? "거래소 확인 불가" : `${order.exchange} 주문`;
    default:
      return null;
  }
}

function SideBadge({ order }: { order: OpenOrder }) {
  const color =
    order.side === "buy" ? "bg-gain-soft text-gain" : order.side === "sell" ? "bg-loss-soft text-loss" : "bg-canvas text-sub";
  return <span className={`rounded-full px-2 py-0.5 text-[11px] font-bold ${color}`}>{order.side_label}</span>;
}

function OpenOrdersSkeleton() {
  return (
    <div className="animate-pulse space-y-4 py-2" role="status" aria-label="미체결 주문을 불러오는 중">
      {Array.from({ length: 2 }, (_, i) => (
        <div key={i} className="flex items-center gap-3">
          <div className="flex-1 space-y-2">
            <div className="h-4 w-36 rounded-lg bg-canvas" />
            <div className="h-3 w-48 rounded-lg bg-canvas" />
          </div>
          <div className="h-4 w-24 rounded-lg bg-canvas" />
        </div>
      ))}
    </div>
  );
}
