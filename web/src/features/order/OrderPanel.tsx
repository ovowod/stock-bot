import { CircleAlert, Info, ShieldAlert, X } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ApiError, fetchQuote, ORDER_RESULT_UNKNOWN, placeOrder, type EnvironmentValue } from "../../api";
import { findEnvironment, type EnvironmentOption } from "../../environments";
import { formatCount, formatForeign, formatKrw } from "../../format";
import { useToast } from "../toast/Toasts";
import {
  ORDER_TYPES,
  buyUnavailableReason,
  groupThousands,
  newOrderKey,
  parsePrice,
  parseQuantity,
  quoteToPriceText,
  sellQuantityError,
  stepPrice,
  type OrderTarget,
  type OrderType,
} from "./order";

const OpenOrderPanel = createContext<(target: OrderTarget) => void>(() => {});

/** 어느 화면에서든 종목 정보만 넘겨 주문 패널을 연다. */
export function useOrderPanel() {
  return useContext(OpenOrderPanel);
}

/** 앱에 주문 패널 하나를 둔다. 투자 환경이 바뀌면 key로 다시 만들어 열린 패널을 닫는다. */
export function OrderPanelProvider({ environment, children }: { environment: EnvironmentValue; children: ReactNode }) {
  const [target, setTarget] = useState<OrderTarget | null>(null);
  const close = useCallback(() => setTarget(null), []);
  return (
    <OpenOrderPanel.Provider value={setTarget}>
      {children}
      {target && (
        <OrderPanel
          key={`${target.sell ? "sell" : "buy"}-${target.exchange ?? target.category}-${target.code}`}
          environment={environment}
          target={target}
          onClose={close}
        />
      )}
    </OpenOrderPanel.Provider>
  );
}

function OrderPanel({
  environment,
  target,
  onClose,
}: {
  environment: EnvironmentValue;
  target: OrderTarget;
  onClose: () => void;
}) {
  const env = findEnvironment(environment);
  const closeRef = useRef<HTMLButtonElement>(null);
  // 주문을 보내는 동안에는 결과를 확인하기 전에 닫지 못하게 한다.
  const [locked, setLocked] = useState(false);
  const lockedRef = useRef(false);
  useEffect(() => {
    lockedRef.current = locked;
  }, [locked]);
  const requestClose = useCallback(() => {
    if (!lockedRef.current) onClose();
  }, [onClose]);
  const side = target.sell ? "매도" : "매수";
  const unavailable = buyUnavailableReason(env.market, env.isReal, target.exchange);
  const meta = [target.code, target.exchange ?? target.category].filter(Boolean).join(" · ");

  // 패널이 열린 동안 뒤쪽 화면을 inert로 막고, 닫으면 패널을 열었던 요소로 초점을 되돌린다.
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const root = document.getElementById("root");
    root?.setAttribute("inert", "");
    closeRef.current?.focus();
    const close = (event: KeyboardEvent) => event.key === "Escape" && requestClose();
    window.addEventListener("keydown", close);
    return () => {
      window.removeEventListener("keydown", close);
      root?.removeAttribute("inert");
      opener?.focus();
    };
  }, [requestClose]);

  return createPortal(
    <div className="fixed inset-0 z-40" role="dialog" aria-modal="true" aria-label={`${target.name} 주문`}>
      <button type="button" className="absolute inset-0 bg-ink/40" aria-label="주문 패널 닫기" onClick={requestClose} />
      <div className="absolute inset-x-0 bottom-0 flex max-h-[90dvh] flex-col rounded-t-3xl bg-surface shadow-2xl md:inset-y-0 md:left-auto md:max-h-none md:w-[420px] md:rounded-none md:rounded-l-3xl">
        <div className="flex items-start gap-3 px-5 pt-5 pb-3">
          <div className="min-w-0 flex-1">
            <p className="flex items-center gap-1.5 text-xs font-semibold text-sub">
              {env.label} {side}
              {env.isReal && (
                <span className="inline-flex items-center gap-0.5 rounded-full bg-real-soft px-2 py-0.5 text-[11px] font-bold text-real">
                  <ShieldAlert className="size-3" aria-hidden />
                  실전
                </span>
              )}
            </p>
            <h2 className="mt-1 truncate text-xl font-bold">{target.name}</h2>
            <p className="truncate text-sm text-muted">{meta}</p>
          </div>
          <button
            ref={closeRef}
            type="button"
            className="-mr-1.5 rounded-xl p-2 text-sub hover:bg-canvas disabled:opacity-40"
            aria-label="닫기"
            disabled={locked}
            onClick={requestClose}
          >
            <X className="size-5" />
          </button>
        </div>
        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 pb-6">
          {target.status && (
            <Callout tone="real" icon={CircleAlert}>
              이 종목은 "{target.status}" 상태입니다. 주문 전에 종목 상태를 확인하세요.
            </Callout>
          )}
          {unavailable ? (
            <Callout tone="muted" icon={Info} title={`${side}할 수 없는 종목입니다`}>
              {unavailable}
            </Callout>
          ) : (
            <OrderForm env={env} target={target} onAccepted={onClose} onSendingChange={setLocked} />
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

type QuoteStatus = "loading" | "ready" | "error";

/**
 * 패널이 열릴 때 현재가를 한 번 조회해 가격 칸을 채운다.
 * 사용자가 가격 칸을 이미 건드렸으면 성공·실패 모두 그 값을 그대로 둔다.
 */
function usePriceWithQuote(env: EnvironmentOption, target: OrderTarget) {
  const [priceText, setPriceText] = useState("");
  const [status, setStatus] = useState<QuoteStatus>("loading");
  // 현재가 버튼이 되돌릴 값. 조회하지 못했으면 null이다.
  const [quoteText, setQuoteText] = useState<string | null>(null);
  const touched = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    fetchQuote(env.value, target.code, target.exchange, controller.signal).then(
      (quote) => {
        const text = quote.price === null ? null : quoteToPriceText(quote.price, env.market);
        if (!touched.current && text !== null) setPriceText(text);
        setQuoteText(text);
        setStatus("ready");
      },
      () => {
        if (!controller.signal.aborted) setStatus("error");
      },
    );
    return () => controller.abort();
  }, [env.value, env.market, target.code, target.exchange]);

  const change = (value: string) => {
    touched.current = true;
    setPriceText(value);
  };
  return { priceText, change, status, quoteText };
}

type Step = "input" | "confirm" | "sending";

function OrderForm({
  env,
  target,
  onAccepted,
  onSendingChange,
}: {
  env: EnvironmentOption;
  target: OrderTarget;
  onAccepted: () => void;
  onSendingChange: (sending: boolean) => void;
}) {
  const market = env.market;
  const showToast = useToast();
  const sell = target.sell;
  const side = sell ? "매도" : "매수";
  const [type, setType] = useState<OrderType>("limit");
  // 매수는 1주, 매도는 매도 가능 수량(전량)으로 시작한다. 매도 가능 수량이 없거나 모르면 비워 둔다.
  const [quantityText, setQuantityText] = useState(() =>
    !sell ? "1" : sell.sellableQuantity ? String(sell.sellableQuantity) : "",
  );
  const { priceText, change: setPriceText, status: quoteStatus, quoteText } = usePriceWithQuote(env, target);
  const priceDown = stepPrice(priceText, -1, market, target.category);
  const priceUp = stepPrice(priceText, 1, market, target.category);
  const [step, setStep] = useState<Step>("input");
  const orderKey = useRef("");
  const quantity = parseQuantity(quantityText);
  const quantityError = quantity.error ?? (sell ? sellQuantityError(quantity.quantity, sell.sellableQuantity) : null);
  const nothingToSell = sell?.sellableQuantity === 0;
  const price = parsePrice(priceText, market);
  const unit = market === "domestic" ? "원" : "USD";
  const formatMoney = (value: number) => (market === "domestic" ? formatKrw(value) : formatForeign(value, "USD", 4));
  const amount =
    type === "limit" && quantity.quantity !== null && price.price !== null
      ? formatMoney(quantity.quantity * price.price)
      : null;
  const ready =
    quantity.text !== null &&
    quantityError === null &&
    !nothingToSell &&
    (type === "market" || price.text !== null);

  const openConfirm = () => {
    if (!ready || env.isReal) return;
    // 최종 확인을 열 때마다 새 주문 키를 만든다. 이 확인에서 나가는 요청은 이 키를 쓴다.
    orderKey.current = newOrderKey();
    setStep("confirm");
  };

  const submit = async () => {
    if (step !== "confirm" || quantity.text === null) return;
    setStep("sending");
    onSendingChange(true);
    const key = orderKey.current;
    try {
      const { result, requestId } = await placeOrder(env.value, {
        order_key: key,
        side: sell ? "sell" : "buy",
        code: target.code,
        ...(market === "us" && target.exchange ? { exchange: target.exchange } : {}),
        order_type: type,
        quantity: quantity.text,
        ...(type === "limit" && price.text !== null ? { price: price.text } : {}),
      });
      showToast({
        tone: "success",
        title: `${side} 주문이 접수되었습니다`,
        body: `${target.name} · 주문번호 ${result.order_no}`,
        meta: [`주문 키 ${key}`, ...(requestId ? [`요청 ID ${requestId}`] : [])],
      });
      onSendingChange(false);
      sell?.onAccepted();
      onAccepted();
    } catch (error) {
      const apiError = error instanceof ApiError ? error : new ApiError("unknown", "알 수 없는 오류입니다.", null);
      const meta = [`주문 키 ${key}`, ...(apiError.requestId ? [`요청 ID ${apiError.requestId}`] : [])];
      showToast(
        apiError.kind === ORDER_RESULT_UNKNOWN
          ? {
              tone: "unknown",
              title: "접수 여부를 확인할 수 없습니다",
              body: "키움에서 주문 내역을 확인한 뒤 다시 주문하세요.",
              meta,
            }
          : { tone: "error", title: "주문하지 못했습니다", body: apiError.message, meta },
      );
      onSendingChange(false);
      setStep("input");
    }
  };

  if (step !== "input") {
    const rows: [string, string][] = [
      ["투자 환경", env.label],
      ["주문", side],
      ["종목", target.name],
      ["종목코드", target.code],
      ["거래소", market === "domestic" ? "KRX" : (target.exchange ?? "")],
      ["주문 유형", ORDER_TYPES.find((option) => option.value === type)?.label ?? type],
      ["가격", type === "limit" && price.price !== null ? formatMoney(price.price) : "시장가"],
      ["수량", `${quantity.quantity?.toLocaleString("ko-KR")}주`],
      ...(amount ? ([["예상 주문금액", amount]] as [string, string][]) : []),
    ];
    return (
      <section aria-label="최종 확인" className="flex flex-1 flex-col gap-4">
        <div className="flex items-center justify-between">
          <h3 className="text-[17px] font-bold">최종 확인</h3>
          <span className="rounded-full bg-brand-50 px-2.5 py-1 text-xs font-bold text-brand-700">모의투자 주문</span>
        </div>
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
        <p className="text-xs text-muted">주문하기를 누르면 이 내용으로 {side} 주문을 보냅니다.</p>
        <div className="sticky bottom-0 mt-auto grid grid-cols-2 gap-2 bg-surface pt-2">
          <button
            type="button"
            disabled={step === "sending"}
            onClick={() => setStep("input")}
            className="rounded-2xl bg-canvas py-3.5 text-[15px] font-bold text-sub disabled:opacity-40"
          >
            취소
          </button>
          <button
            type="button"
            disabled={step === "sending"}
            onClick={() => void submit()}
            className={`rounded-2xl py-3.5 text-[15px] font-bold text-white disabled:opacity-60 ${sell ? "bg-loss" : "bg-gain"}`}
          >
            {step === "sending" ? "주문 중…" : "주문하기"}
          </button>
        </div>
      </section>
    );
  }

  return (
    <form
      className="flex flex-1 flex-col gap-4"
      aria-label={`${side} 주문`}
      onSubmit={(event) => {
        event.preventDefault();
        openConfirm();
      }}
    >
      {sell && (
        <dl className="grid grid-cols-2 gap-2">
          {(
            [
              ["보유 수량", sell.quantity],
              ["매도 가능 수량", sell.sellableQuantity],
            ] as [string, number | null][]
          ).map(([term, value]) => (
            <div key={term} className="rounded-2xl bg-canvas px-4 py-3">
              <dt className="text-xs text-muted">{term}</dt>
              <dd aria-label={term} className="text-[15px] font-bold">
                {value === null ? "확인 불가" : formatCount(value)}
              </dd>
            </div>
          ))}
        </dl>
      )}
      {nothingToSell && (
        <Callout tone="muted" icon={Info} title="매도 가능 수량이 없습니다">
          체결되지 않은 매도 주문에 묶였거나 팔 수 있는 수량이 없습니다.
        </Callout>
      )}

      <div role="radiogroup" aria-label="주문 유형" className="grid grid-cols-2 gap-1 rounded-2xl bg-canvas p-1">
        {ORDER_TYPES.map((option) => {
          const active = option.value === type;
          return (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => setType(option.value)}
              className={`rounded-xl py-2 text-sm font-semibold transition ${
                active ? "bg-surface text-brand-700 shadow-[0_1px_3px_rgba(0,0,0,0.08)]" : "text-muted hover:text-sub"
              }`}
            >
              {option.label}
            </button>
          );
        })}
      </div>

      {type === "limit" && (
        <div className="space-y-2">
          <Field
            label={`가격 (${unit})`}
            error={price.error}
            hint={
              quoteStatus === "loading"
                ? "현재가를 불러오는 중입니다."
                : quoteStatus === "error"
                  ? "현재가를 불러오지 못했습니다. 가격을 직접 입력하세요."
                  : null
            }
          >
            <input
              value={groupThousands(priceText)}
              onChange={(event) => setPriceText(event.target.value.replaceAll(",", ""))}
              inputMode={market === "domestic" ? "numeric" : "decimal"}
              placeholder={market === "domestic" ? "예: 70000" : "예: 213.04"}
              aria-invalid={price.error !== null}
              className={INPUT}
            />
          </Field>
          <div className="grid grid-cols-3 gap-2">
            {(
              [
                ["-1호가", priceDown],
                ["현재가", quoteText],
                ["+1호가", priceUp],
              ] as [string, string | null][]
            ).map(([label, value]) => (
              <button
                key={label}
                type="button"
                disabled={value === null}
                onClick={() => value !== null && setPriceText(value)}
                className="rounded-xl bg-canvas py-2 text-sm font-semibold text-sub hover:text-ink disabled:opacity-40"
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      )}
      {type === "market" && (
        <p className="rounded-2xl bg-canvas px-4 py-3 text-sm text-sub">
          시장가는 가격을 정하지 않고 지금 시장 가격으로 바로 체결됩니다.
        </p>
      )}

      <Field label="수량 (주)" error={quantityError}>
        <input
          value={quantityText}
          onChange={(event) => setQuantityText(event.target.value)}
          inputMode="numeric"
          placeholder="예: 10"
          aria-invalid={quantityError !== null}
          className={INPUT}
        />
      </Field>

      {type === "limit" && (
        <div className="flex items-center justify-between rounded-2xl bg-canvas px-4 py-3">
          <span className="text-sm text-sub">예상 주문금액</span>
          <span className="text-[15px] font-bold" aria-label="예상 주문금액">
            {amount ?? "–"}
          </span>
        </div>
      )}

      {/* 패널이 화면 높이를 채우는 태블릿·데스크톱에서는 주문 버튼을 패널 맨 아래에 둔다. */}
      <div className="sticky bottom-0 mt-auto space-y-2 bg-surface pt-2">
        {env.isReal ? (
          <p className="flex items-center justify-center gap-1.5 rounded-2xl bg-real-soft py-3.5 text-sm font-bold text-real">
            <ShieldAlert className="size-4" aria-hidden />
            실전투자에서는 주문할 수 없습니다.
          </p>
        ) : (
          <button
            type="submit"
            disabled={!ready}
            className={`w-full rounded-2xl py-3.5 text-[15px] font-bold text-white disabled:opacity-40 ${sell ? "bg-loss" : "bg-gain"}`}
          >
            {side}
          </button>
        )}
      </div>
    </form>
  );
}

const INPUT =
  "w-full rounded-2xl bg-canvas px-4 py-3 text-[16px] outline-none placeholder:text-muted focus:ring-2 focus:ring-brand-100 aria-invalid:ring-2 aria-invalid:ring-real/40";

function Field({
  label,
  error,
  hint = null,
  children,
}: {
  label: string;
  error: string | null;
  hint?: string | null;
  children: ReactNode;
}) {
  return (
    <label className="block space-y-1.5">
      <span className="text-sm font-semibold text-sub">{label}</span>
      {children}
      {error && <span className="block text-xs font-semibold text-real">{error}</span>}
      {hint && <span className="block text-xs text-muted">{hint}</span>}
    </label>
  );
}

function Callout({
  tone,
  icon: Icon,
  title,
  children,
}: {
  tone: "real" | "muted";
  icon: typeof Info;
  title?: string;
  children: ReactNode;
}) {
  const color = tone === "real" ? "bg-real-soft text-real" : "bg-canvas text-sub";
  return (
    <div role="note" className={`flex gap-2.5 rounded-2xl px-4 py-3 text-sm ${color}`}>
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div className="space-y-0.5">
        {title && <p className="font-bold text-ink">{title}</p>}
        <p>{children}</p>
      </div>
    </div>
  );
}
