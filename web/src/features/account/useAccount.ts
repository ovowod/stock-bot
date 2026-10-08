import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  fetchAccount,
  fetchOpenOrders,
  type Account,
  type EnvironmentValue,
  type OpenOrders,
} from "../../api";

export type RemoteState<T> =
  | { status: "loading" }
  | { status: "error"; error: ApiError }
  | { status: "ready"; data: T; refreshing: boolean; refreshError: ApiError | null };

/** 계좌 확인 데이터. 한 투자 환경에만 묶인다(환경이 바뀌면 컴포넌트가 새로 만들어진다). */
export function useAccount(environment: EnvironmentValue) {
  return useRemote<Account>(useCallback((signal: AbortSignal) => fetchAccount(environment, signal), [environment]));
}

/** 국내 투자 환경의 미체결 주문. enabled가 false면(미국) 요청하지 않는다. */
export function useOpenOrders(environment: EnvironmentValue, enabled: boolean) {
  return useRemote<OpenOrders>(
    useCallback((signal: AbortSignal) => fetchOpenOrders(environment, signal), [environment]),
    enabled,
  );
}

/**
 * 처음에는 불러오는 동안 loading, 실패하면 error다.
 * 한 번 받은 뒤 refresh는 기존 데이터를 둔 채 다시 불러오고, 실패하면 refreshError에 담는다.
 */
function useRemote<T>(fetcher: (signal: AbortSignal) => Promise<T>, enabled = true) {
  const [state, setState] = useState<RemoteState<T>>({ status: "loading" });
  const controller = useRef<AbortController | null>(null);

  const load = useCallback(
    async (mode: "initial" | "refresh") => {
      if (!enabled) return;
      controller.current?.abort();
      const current = new AbortController();
      controller.current = current;
      setState((prev) =>
        mode === "refresh" && prev.status === "ready"
          ? { ...prev, refreshing: true, refreshError: null }
          : { status: "loading" },
      );
      try {
        const data = await fetcher(current.signal);
        if (!current.signal.aborted) {
          setState({ status: "ready", data, refreshing: false, refreshError: null });
        }
      } catch (error) {
        if (current.signal.aborted) return;
        const apiError =
          error instanceof ApiError ? error : new ApiError("unknown", "알 수 없는 오류입니다.", null);
        setState((prev) =>
          prev.status === "ready"
            ? { ...prev, refreshing: false, refreshError: apiError }
            : { status: "error", error: apiError },
        );
      }
    },
    [fetcher, enabled],
  );

  useEffect(() => {
    void load("initial");
    return () => controller.current?.abort();
  }, [load]);

  return {
    state,
    refresh: () => load("refresh"),
    retry: () => load("initial"),
  };
}
