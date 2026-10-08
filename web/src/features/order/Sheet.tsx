import { ShieldAlert, X, type Info } from "lucide-react";
import { useCallback, useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { EnvironmentOption } from "../../environments";

/**
 * 주문 패널과 주문 취소 최종 확인이 함께 쓰는 시트. 모바일은 아래에서 올라오고, 넓은 화면은 오른쪽에 붙는다.
 * locked인 동안(보내는 중)에는 닫기 버튼·바깥 클릭·Esc로 닫히지 않는다.
 */
export function Sheet({
  label,
  overlayLabel,
  env,
  eyebrow,
  title,
  meta,
  locked,
  onClose,
  children,
}: {
  label: string;
  overlayLabel: string;
  env: EnvironmentOption;
  eyebrow: string;
  title: string;
  meta: string;
  locked: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const lockedRef = useRef(locked);
  useEffect(() => {
    lockedRef.current = locked;
  }, [locked]);
  const requestClose = useCallback(() => {
    if (!lockedRef.current) onClose();
  }, [onClose]);

  // 시트가 열린 동안 뒤쪽 화면을 inert로 막고, 닫으면 시트를 열었던 요소로 초점을 되돌린다.
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
    <div className="fixed inset-0 z-40" role="dialog" aria-modal="true" aria-label={label}>
      <button type="button" className="absolute inset-0 bg-ink/40" aria-label={overlayLabel} onClick={requestClose} />
      <div className="absolute inset-x-0 bottom-0 flex max-h-[90dvh] flex-col rounded-t-3xl bg-surface shadow-2xl md:inset-y-0 md:left-auto md:max-h-none md:w-[420px] md:rounded-none md:rounded-l-3xl">
        <div className="flex items-start gap-3 px-5 pt-5 pb-3">
          <div className="min-w-0 flex-1">
            <p className="flex items-center gap-1.5 text-xs font-semibold text-sub">
              {env.label} {eyebrow}
              {env.isReal && (
                <span className="inline-flex items-center gap-0.5 rounded-full bg-real-soft px-2 py-0.5 text-[11px] font-bold text-real">
                  <ShieldAlert className="size-3" aria-hidden />
                  실전
                </span>
              )}
            </p>
            <h2 className="mt-1 truncate text-xl font-bold">{title}</h2>
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
        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 pb-6">{children}</div>
      </div>
    </div>,
    document.body,
  );
}

export const INPUT =
  "w-full rounded-2xl bg-canvas px-4 py-3 text-[16px] outline-none placeholder:text-muted focus:ring-2 focus:ring-brand-100 aria-invalid:ring-2 aria-invalid:ring-real/40";

export function Field({
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

export function Callout({
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
