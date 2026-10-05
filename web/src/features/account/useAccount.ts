import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, fetchAccount, type Account, type EnvironmentValue } from "../../api";

export type AccountState =
  | { status: "loading" }
  | { status: "error"; error: ApiError }
  | { status: "ready"; data: Account; refreshing: boolean; refreshError: ApiError | null };

/** 계좌 확인 데이터. 한 투자 환경에만 묶인다(환경이 바뀌면 컴포넌트가 새로 만들어진다). */
export function useAccount(environment: EnvironmentValue) {
  const [state, setState] = useState<AccountState>({ status: "loading" });
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
        const data = await fetchAccount(environment, current.signal);
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
    [environment],
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
