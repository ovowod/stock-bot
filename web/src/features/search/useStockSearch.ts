import { useEffect, useRef, useState } from "react";
import { ApiError, searchStocks, type EnvironmentValue, type StockSearchResult } from "../../api";

/** 입력을 멈추고 이만큼 지나면 검색한다. */
export const SEARCH_DELAY_MS = 300;

export type SearchState =
  | { status: "idle" }
  | { status: "searching"; previous: StockSearchResult | null }
  | { status: "ready"; result: StockSearchResult }
  | { status: "error"; error: ApiError };

/**
 * 종목 검색. 한 투자 환경에만 묶인다(환경이 바뀌면 컴포넌트가 새로 만들어진다).
 *
 * 입력이 바뀌면 그 즉시 진행 중인 요청을 취소해 응답이 반영되지 않게 하고,
 * 새 요청은 입력을 멈춘 뒤에만 보낸다. 검색어를 지운 경우도 같다.
 */
export function useStockSearch(environment: EnvironmentValue) {
  const [query, setQuery] = useState("");
  const [state, setState] = useState<SearchState>({ status: "idle" });
  const controller = useRef<AbortController | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cancel = () => {
    controller.current?.abort();
    controller.current = null;
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  };

  const run = async (value: string) => {
    cancel();
    const current = new AbortController();
    controller.current = current;
    try {
      const result = await searchStocks(environment, value.trim(), current.signal);
      if (!current.signal.aborted) setState({ status: "ready", result });
    } catch (error) {
      if (current.signal.aborted) return;
      const apiError =
        error instanceof ApiError ? error : new ApiError("unknown", "알 수 없는 오류입니다.", null);
      setState({ status: "error", error: apiError });
    }
  };

  useEffect(() => cancel, []);

  return {
    query,
    state,
    change: (value: string) => {
      setQuery(value);
      cancel();
      if (!value.trim()) {
        setState({ status: "idle" });
        return;
      }
      // 이전 결과가 있으면 검색하는 동안 유지한다.
      setState((prev) => ({
        status: "searching",
        previous: prev.status === "ready" ? prev.result : prev.status === "searching" ? prev.previous : null,
      }));
      timer.current = setTimeout(() => void run(value), SEARCH_DELAY_MS);
    },
    retry: () => {
      if (!query.trim()) return;
      setState({ status: "searching", previous: null });
      void run(query);
    },
  };
}
