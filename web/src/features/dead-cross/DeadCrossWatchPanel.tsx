import { fetchDeadCrossStatus, type DeadCrossRecentSell, type DeadCrossStatus, type EnvironmentValue } from "../../api";
import { EMPTY } from "../../format";
import { AUTO_SELL_FAILED, CROSS_REASON, stampText, usePolledStatus } from "../account/HoldingsPanel";
import { Panel } from "../account/parts";
import { basisText } from "./rules";

function watchText(status: DeadCrossStatus | null): string {
  if (status === null) return EMPTY;
  if (status.watching) return "감시 중";
  const reason = status.not_watching_reason;
  return ["감시 안 함", ...(reason ? [CROSS_REASON[reason]] : [])].join(" · ");
}

function resultText(sell: DeadCrossRecentSell): string {
  if (sell.state === "sending") return "보내는 중";
  if (sell.state === "accepted") return "접수";
  return ["실패", ...(sell.reason ? [AUTO_SELL_FAILED[sell.reason]] : [])].join(" · ");
}

function detailText(sell: DeadCrossRecentSell, timeZone: string): string {
  return [
    stampText(sell.at, timeZone),
    ...(sell.quantity === null ? [] : [`${sell.quantity.toLocaleString("ko-KR")}주`]),
    ...(sell.order_no === null ? [] : [`주문번호 ${sell.order_no}`]),
    ...(sell.retry ? ["다시 시도"] : []),
  ].join(" · ");
}

/** Dead Cross 설정 화면의 감시 상태와 최근 자동 매도. 전량 체결로 종목이 계좌에서 사라져도 결과가 남는다. */
export function DeadCrossWatchPanel({ environment }: { environment: EnvironmentValue }) {
  const { status, error } = usePolledStatus(fetchDeadCrossStatus, environment, true);
  const session = status?.session ?? null;
  const basis = status && session ? basisText(status.market, session) : null;
  const sells = status?.recent_sells ?? [];
  return (
    <Panel title="감시 상태" label="Dead Cross 감시 상태">
      <div className="space-y-3">
        {error && (
          <p role="alert" className="rounded-xl bg-real-soft px-3 py-2 text-sm break-keep text-real">
            Dead Cross 상태를 불러오지 못했습니다.{status && " 마지막으로 받은 상태를 보여줍니다."} {error.message}
          </p>
        )}
        <div>
          <p className="text-[15px] font-semibold break-keep">{watchText(status)}</p>
          {session && (
            <p className="mt-1 text-xs break-keep text-sub">
              지금 세션 {session.start}–{session.end}
              {session.enabled ? "" : " (꺼짐)"}
              {basis && ` · ${basis}`}
            </p>
          )}
        </div>
        <div>
          <h4 id={`dead-cross-recent-${environment}`} className="text-sm font-semibold text-sub">
            최근 자동 매도
          </h4>
          {status && sells.length === 0 && <p className="mt-1 text-sm text-muted">최근 자동 매도가 없습니다</p>}
          {status && sells.length > 0 && (
            <ul aria-labelledby={`dead-cross-recent-${environment}`} className="mt-2 space-y-2">
              {sells.map((sell) => (
                <li key={`${sell.code}-${sell.at}-${sell.crossed_bar}`} className="rounded-xl bg-canvas px-3 py-2">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
                    <span className="min-w-0 text-sm font-semibold break-keep">
                      {sell.name} ({sell.code})
                    </span>
                    <span className="text-sm break-keep text-sub">{resultText(sell)}</span>
                  </div>
                  <p className="mt-0.5 text-xs break-keep text-muted">{detailText(sell, status.timezone)}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Panel>
  );
}
