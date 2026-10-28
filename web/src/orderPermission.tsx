import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { fetchOrdersAllowed, type EnvironmentValue } from "./api";

/** 이 투자 환경에서 주문·취소를 보낼 수 있는지. 서버의 주문 허용 스위치를 따른다. */
export type OrderPermission = "loading" | "failed" | "allowed" | "blocked";

const FAILED = "주문 가능 여부를 불러오지 못했습니다. 새로고침하세요.";

type State =
  | { status: "loading" }
  | { status: "failed" }
  | { status: "ready"; allowed: Partial<Record<EnvironmentValue, boolean>> };

const Permissions = createContext<State>({ status: "loading" });

/** 로그인 뒤 서버에서 투자 환경별 주문 허용을 한 번 받는다. 받기 전이나 실패하면 주문할 수 없다. */
export function OrderPermissionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>({ status: "loading" });
  useEffect(() => {
    const controller = new AbortController();
    fetchOrdersAllowed(controller.signal).then(
      (allowed) => setState({ status: "ready", allowed }),
      () => !controller.signal.aborted && setState({ status: "failed" }),
    );
    return () => controller.abort();
  }, []);
  return <Permissions.Provider value={state}>{children}</Permissions.Provider>;
}

export function useOrderPermission(environment: EnvironmentValue): OrderPermission {
  const state = useContext(Permissions);
  if (state.status !== "ready") return state.status;
  return state.allowed[environment] === true ? "allowed" : "blocked";
}

/** 주문·취소 버튼 대신 보여줄 안내. 막혔으면 realMessage, 받지 못했으면 새로고침 안내, 그 밖은 null이다. */
export function blockedMessage(permission: OrderPermission, realMessage: string): string | null {
  if (permission === "blocked") return realMessage;
  if (permission === "failed") return FAILED;
  return null;
}
