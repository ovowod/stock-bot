import { CircleAlert, Info, ShieldAlert, X } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { EnvironmentValue } from "../../api";
import { findEnvironment } from "../../environments";
import { formatForeign, formatKrw } from "../../format";
import {
  ORDER_TYPES,
  buyUnavailableReason,
  groupThousands,
  parsePrice,
  parseQuantity,
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
          key={`${target.exchange ?? target.category}-${target.code}`}
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
  const unavailable = buyUnavailableReason(env.market, env.isReal, target.exchange);
  const meta = [target.code, target.exchange ?? target.category].filter(Boolean).join(" · ");

  // 패널이 열린 동안 뒤쪽 화면을 inert로 막고, 닫으면 패널을 열었던 요소로 초점을 되돌린다.
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const root = document.getElementById("root");
    root?.setAttribute("inert", "");
    closeRef.current?.focus();
    const close = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", close);
    return () => {
      window.removeEventListener("keydown", close);
      root?.removeAttribute("inert");
      opener?.focus();
    };
  }, [onClose]);

  return createPortal(
    <div className="fixed inset-0 z-40" role="dialog" aria-modal="true" aria-label={`${target.name} 주문`}>
      <button type="button" className="absolute inset-0 bg-ink/40" aria-label="주문 패널 닫기" onClick={onClose} />
      <div className="absolute inset-x-0 bottom-0 flex max-h-[90dvh] flex-col rounded-t-3xl bg-surface shadow-2xl md:inset-y-0 md:left-auto md:max-h-none md:w-[420px] md:rounded-none md:rounded-l-3xl">
        <div className="flex items-start gap-3 px-5 pt-5 pb-3">
          <div className="min-w-0 flex-1">
            <p className="flex items-center gap-1.5 text-xs font-semibold text-sub">
              {env.label} 매수
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
            className="-mr-1.5 rounded-xl p-2 text-sub hover:bg-canvas"
            aria-label="닫기"
            onClick={onClose}
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
            <Callout tone="muted" icon={Info} title="매수할 수 없는 종목입니다">
              {unavailable}
            </Callout>
          ) : (
            <BuyForm market={env.market} target={target} />
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

function BuyForm({ market, target }: { market: "domestic" | "us"; target: OrderTarget }) {
  const [type, setType] = useState<OrderType>("limit");
  const [quantityText, setQuantityText] = useState("");
  const [priceText, setPriceText] = useState(target.price === null ? "" : String(target.price));
  const { quantity, error: quantityError } = parseQuantity(quantityText);
  const { price, error: priceError } = parsePrice(priceText, market);
  const unit = market === "domestic" ? "원" : "USD";
  const amount =
    type === "limit" && quantity !== null && price !== null
      ? market === "domestic"
        ? formatKrw(quantity * price)
        : formatForeign(quantity * price, "USD", 4)
      : null;

  return (
    <form className="flex flex-1 flex-col gap-4" aria-label="매수 주문" onSubmit={(event) => event.preventDefault()}>
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
        <Field label={`가격 (${unit})`} error={priceError}>
          <input
            value={groupThousands(priceText)}
            onChange={(event) => setPriceText(event.target.value.replaceAll(",", ""))}
            inputMode={market === "domestic" ? "numeric" : "decimal"}
            placeholder={market === "domestic" ? "예: 70000" : "예: 213.04"}
            aria-invalid={priceError !== null}
            className={INPUT}
          />
        </Field>
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

      {/* 패널이 화면 높이를 채우는 태블릿·데스크톱에서는 매수 버튼을 패널 맨 아래에 둔다. */}
      <div className="sticky bottom-0 mt-auto space-y-2 bg-surface pt-2">
        <button
          type="submit"
          disabled
          className="w-full rounded-2xl bg-gain py-3.5 text-[15px] font-bold text-white disabled:opacity-40"
        >
          매수
        </button>
        <p className="text-center text-xs text-muted">주문 전송은 아직 지원하지 않습니다.</p>
      </div>
    </form>
  );
}

const INPUT =
  "w-full rounded-2xl bg-canvas px-4 py-3 text-[16px] outline-none placeholder:text-muted focus:ring-2 focus:ring-brand-100 aria-invalid:ring-2 aria-invalid:ring-real/40";

function Field({ label, error, children }: { label: string; error: string | null; children: ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-sm font-semibold text-sub">{label}</span>
      {children}
      {error && <span className="block text-xs font-semibold text-real">{error}</span>}
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
