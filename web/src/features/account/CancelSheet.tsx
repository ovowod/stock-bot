import { CircleAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ApiError, cancelOrder, fetchOpenOrders, ORDER_RESULT_UNKNOWN, type OpenOrder } from "../../api";
import type { EnvironmentOption } from "../../environments";
import { formatCount, formatKrw } from "../../format";
import { newOrderKey } from "../order/order";
import { Callout, Sheet } from "../order/Sheet";
import { useToast } from "../toast/Toasts";

type Check = { status: "loading" } | { status: "ready"; order: OpenOrder | null } | { status: "error"; message: string };

// 이 응답을 받으면 미체결을 다시 확인해 최신 상태를 보여준다.
const RECHECK_KINDS = new Set(["open_order_not_found"]);

/**
 * 시트가 열려 있는 동안 미체결을 다시 조회해 그 주문의 최신 상태를 찾는다. retry를 부르면 다시 조회한다.
 * 시트를 닫으면 진행 중인 조회를 취소하고 그 응답은 반영하지 않는다.
 */
function useOpenOrderCheck(env: EnvironmentOption, orderNo: string) {
  const [check, setCheck] = useState<Check>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setCheck({ status: "loading" });
    fetchOpenOrders(env.value, controller.signal).then(
      (data) => {
        if (controller.signal.aborted) return;
        setCheck({ status: "ready", order: data.orders.find((order) => order.order_no === orderNo) ?? null });
      },
      (error) => {
        if (controller.signal.aborted) return;
        setCheck({ status: "error", message: error instanceof ApiError ? error.message : "알 수 없는 오류입니다." });
      },
    );
    return () => controller.abort();
  }, [env.value, orderNo, attempt]);

  return { check, retry: () => setAttempt((value) => value + 1) };
}

/** 미체결 주문 하나의 주문 취소 최종 확인. 접수되거나 접수 여부를 모르면 닫고 onChanged로 다시 불러오게 한다. */
export function CancelSheet({
  env,
  target,
  onClose,
  onChanged,
}: {
  env: EnvironmentOption;
  target: OpenOrder;
  onClose: () => void;
  onChanged: () => void;
}) {
  const showToast = useToast();
  const [sending, setSending] = useState(false);
  // 최종 확인을 열 때 주문 키를 만든다. 확실히 거부된 뒤 다시 보낼 때는 새 키를 쓴다.
  const orderKey = useRef(newOrderKey());
  const { check, retry } = useOpenOrderCheck(env, target.order_no);
  const latest = check.status === "ready" ? check.order : null;
  const canSubmit = !sending && latest !== null && latest.cancelable;

  const submit = async () => {
    if (!canSubmit || latest === null) return;
    setSending(true);
    const key = orderKey.current;
    try {
      const { result, requestId } = await cancelOrder(env.value, {
        order_key: key,
        order_no: target.order_no,
        quantity: String(latest.remaining_quantity),
      });
      const quantity = result.cancel_quantity === null ? "남은 수량 전부" : formatCount(result.cancel_quantity);
      showToast({
        tone: "success",
        title: "주문 취소가 접수되었습니다",
        body: `${target.name} · ${quantity} · 주문번호 ${result.order_no}`,
        meta: [`주문 키 ${key}`, ...(requestId ? [`요청 ID ${requestId}`] : [])],
      });
      setSending(false);
      onChanged();
      onClose();
    } catch (error) {
      const apiError = error instanceof ApiError ? error : new ApiError("unknown", "알 수 없는 오류입니다.", null);
      const meta = [`주문 키 ${key}`, ...(apiError.requestId ? [`요청 ID ${apiError.requestId}`] : [])];
      setSending(false);
      if (apiError.kind === ORDER_RESULT_UNKNOWN) {
        showToast({
          tone: "unknown",
          title: "접수 여부를 확인할 수 없습니다",
          body: "키움에서 주문 내역을 확인한 뒤 다시 시도하세요.",
          meta,
        });
        // 취소됐을 수 있으므로 같은 창에서 바로 다시 보내지 않게 닫고, 다시 불러와 보여준다.
        onChanged();
        onClose();
        return;
      }
      showToast({ tone: "error", title: "주문을 취소하지 못했습니다", body: apiError.message, meta });
      // 서버가 키움에 보내지 않고 거부했다. 다음 전송은 새 키로 한다.
      orderKey.current = newOrderKey();
      if (RECHECK_KINDS.has(apiError.kind)) retry();
    }
  };

  const order = latest ?? target;
  const rows: [string, string][] = [
    ["투자 환경", env.label],
    ["주문", order.side_label],
    ["종목", order.name],
    ["종목코드", order.code],
    ["거래소", order.exchange],
    ["주문 유형", order.order_type],
    ["주문가격", order.price === null ? order.order_type : formatKrw(order.price)],
    ["주문 수량", formatCount(order.ordered_quantity)],
    ["원주문번호", order.order_no],
    ["미체결 수량", latest ? formatCount(latest.remaining_quantity) : "확인 중"],
  ];

  return (
    <Sheet
      label={`${target.name} 주문 취소`}
      overlayLabel="주문 취소 닫기"
      env={env}
      eyebrow="주문 취소"
      title={target.name}
      meta={`${target.code} · ${target.exchange}`}
      locked={sending}
      onClose={onClose}
    >
      <section aria-label="최종 확인" className="flex flex-1 flex-col gap-4">
        <h3 className="text-[17px] font-bold">최종 확인</h3>
        <dl className="divide-y divide-line rounded-2xl bg-canvas px-4">
          {rows.map(([term, value]) => (
            <div key={term} className="flex items-center justify-between gap-3 py-2.5">
              <dt className="text-sm text-sub">{term}</dt>
              <dd data-term={term} className="text-right text-[15px] font-semibold">
                {value}
              </dd>
            </div>
          ))}
        </dl>
        {check.status === "loading" && <p className="text-sm text-sub">미체결을 확인하는 중입니다.</p>}
        {check.status === "error" && (
          <div className="space-y-2">
            <Callout tone="real" icon={CircleAlert} title="미체결을 확인하지 못했습니다">
              {check.message}
            </Callout>
            <button
              type="button"
              onClick={retry}
              className="w-full rounded-2xl bg-canvas py-3 text-sm font-bold text-sub hover:text-ink"
            >
              다시 확인
            </button>
          </div>
        )}
        {check.status === "ready" && latest === null && (
          <Callout tone="real" icon={CircleAlert} title="이미 체결되었거나 취소된 주문입니다">
            미체결 주문에 이 주문이 없어 취소할 수 없습니다.
          </Callout>
        )}
        <p className="text-xs text-muted">주문 취소하기를 누르면 이 주문의 남은 수량 전부를 취소합니다.</p>
        <div className="sticky bottom-0 mt-auto grid grid-cols-2 gap-2 bg-surface pt-2">
          <button
            type="button"
            disabled={sending}
            onClick={onClose}
            className="rounded-2xl bg-canvas py-3.5 text-[15px] font-bold text-sub disabled:opacity-40"
          >
            닫기
          </button>
          <button
            type="button"
            disabled={!canSubmit}
            onClick={() => void submit()}
            className="rounded-2xl bg-ink py-3.5 text-[15px] font-bold text-white disabled:opacity-40"
          >
            {sending ? "취소 중…" : "주문 취소하기"}
          </button>
        </div>
      </section>
    </Sheet>
  );
}
