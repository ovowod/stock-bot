import { CircleAlert, CircleCheck, CircleHelp, X, type LucideIcon } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

export type ToastTone = "success" | "error" | "unknown";

export interface ToastInput {
  tone: ToastTone;
  title: string;
  body?: string;
  /** 로그에서 찾을 때 쓰는 값들(주문 키, 요청 ID). */
  meta?: string[];
}

interface Toast extends ToastInput {
  id: number;
}

const SUCCESS_DURATION_MS = 5000;

const ShowToast = createContext<(toast: ToastInput) => void>(() => {});

/** 어느 화면에서든 화면 알림을 띄운다. */
export function useToast() {
  return useContext(ShowToast);
}

/**
 * 앱 전체에 알림 영역 하나를 둔다. 주문 패널이 #root를 inert로 막아도 알림은 동작하도록
 * body에 직접 그린다. 성공 알림만 저절로 사라지고, 실패·확인 불가 알림은 닫을 때까지 남는다.
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => setToasts((list) => list.filter((toast) => toast.id !== id)), []);
  const show = useCallback((toast: ToastInput) => {
    const id = nextId.current++;
    setToasts((list) => [{ ...toast, id }, ...list]);
  }, []);

  return (
    <ShowToast.Provider value={show}>
      {children}
      {createPortal(
        <div className="pointer-events-none fixed inset-x-0 top-0 z-50 flex flex-col items-center gap-2 p-4 md:items-end md:p-6">
          {toasts.map((toast) => (
            <ToastItem key={toast.id} toast={toast} onDismiss={dismiss} />
          ))}
        </div>,
        document.body,
      )}
    </ShowToast.Provider>
  );
}

const TONES: Record<ToastTone, { icon: LucideIcon; color: string }> = {
  success: { icon: CircleCheck, color: "text-brand-600" },
  error: { icon: CircleAlert, color: "text-real" },
  unknown: { icon: CircleHelp, color: "text-amber-600" },
};

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: (id: number) => void }) {
  const { icon: Icon, color } = TONES[toast.tone];

  useEffect(() => {
    if (toast.tone !== "success") return;
    const timer = setTimeout(() => onDismiss(toast.id), SUCCESS_DURATION_MS);
    return () => clearTimeout(timer);
  }, [toast, onDismiss]);

  return (
    <div
      role={toast.tone === "success" ? "status" : "alert"}
      className="pointer-events-auto flex w-full max-w-sm gap-3 rounded-2xl bg-surface p-4 shadow-[0_8px_24px_rgba(0,0,0,0.12)]"
    >
      <Icon className={`mt-0.5 size-5 shrink-0 ${color}`} aria-hidden />
      <div className="min-w-0 flex-1 space-y-0.5">
        <p className="text-[15px] font-bold">{toast.title}</p>
        {toast.body && <p className="text-sm break-words text-sub">{toast.body}</p>}
        {toast.meta?.map((line) => (
          <p key={line} className="text-xs break-all text-muted">
            {line}
          </p>
        ))}
      </div>
      <button
        type="button"
        className="-m-1 self-start rounded-lg p-1 text-muted hover:bg-canvas"
        aria-label="알림 닫기"
        onClick={() => onDismiss(toast.id)}
      >
        <X className="size-4" />
      </button>
    </div>
  );
}
