import { createContext, createElement, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
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

const AccountSnapshots = createContext<Map<EnvironmentValue, Account> | null>(null);

/**
 * 투자 환경별로 마지막에 받은 계좌 확인 데이터를 기억한다. 로그인 화면 안쪽에 두어,
 * 로그인 세션이 끝나면 기억한 데이터도 함께 사라지게 한다.
 */
export function AccountSnapshotProvider({ children }: { children: ReactNode }) {
  const [snapshots] = useState(() => new Map<EnvironmentValue, Account>());
  return createElement(AccountSnapshots.Provider, { value: snapshots }, children);
}

/**
 * 계좌 확인 데이터. 한 투자 환경에만 묶인다(환경이 바뀌면 컴포넌트가 새로 만들어진다).
 * 다른 화면에 갔다 돌아오면 마지막에 받은 데이터를 바로 보여주고 뒤에서 다시 불러온다.
 */
export function useAccount(environment: EnvironmentValue) {
  const snapshots = useContext(AccountSnapshots);
  return useRemote<Account>(
    useCallback((signal: AbortSignal) => fetchAccount(environment, signal), [environment]),
    snapshots ? { store: snapshots, key: environment } : undefined,
  );
}

/** 그 투자 환경의 미체결 주문. */
export function useOpenOrders(environment: EnvironmentValue) {
  return useRemote<OpenOrders>(
    useCallback((signal: AbortSignal) => fetchOpenOrders(environment, signal), [environment]),
  );
}

/**
 * 처음에는 불러오는 동안 loading, 실패하면 error다.
 * 한 번 받은 뒤 refresh는 기존 데이터를 둔 채 다시 불러오고, 실패하면 refreshError에 담는다.
 */
function useRemote<T, K = unknown>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  snapshot?: { store: Map<K, T>; key: K },
) {
  const [state, setState] = useState<RemoteState<T>>(() => {
    const data = snapshot?.store.get(snapshot.key);
    return data === undefined ? { status: "loading" } : { status: "ready", data, refreshing: true, refreshError: null };
  });
  const controller = useRef<AbortController | null>(null);

  const load = useCallback(
    async (mode: "initial" | "refresh") => {
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
          snapshot?.store.set(snapshot.key, data);
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
    [fetcher, snapshot?.store, snapshot?.key],
  );

  useEffect(() => {
    void load(snapshot?.store.has(snapshot.key) ? "refresh" : "initial");
    return () => controller.current?.abort();
  }, [load]);

  return {
    state,
    refresh: () => load("refresh"),
    retry: () => load("initial"),
  };
}
