import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, fetchRanking, type EnvironmentValue, type Ranking, type RankingKind } from "../../api";

export type CardState =
  | { status: "loading" }
  | { status: "error"; error: ApiError }
  | { status: "ready"; data: Ranking; refreshing: boolean; refreshError: ApiError | null };

/** 화면에 위에서부터 놓이는 순서이자 조회 순서. */
export const KINDS: RankingKind[] = ["trading_value", "gainers", "volume"];

type Cards = Record<RankingKind, CardState>;

const allLoading = (): Cards =>
  Object.fromEntries(KINDS.map((kind) => [kind, { status: "loading" }])) as Cards;

/**
 * 순위 카드들의 데이터. 한 투자 환경에만 묶인다(환경이 바뀌면 컴포넌트가 새로 만들어진다).
 *
 * 카드는 한 번에 하나씩 순서대로 조회한다. 조회를 시작할 때 카드마다 번호표를 새로 받고,
 * 응답이 왔을 때 번호표가 바뀌었으면(조건 변경·다시 시도로 새 조회가 시작됨) 버린다.
 */
export function useRankings(environment: EnvironmentValue) {
  const [cards, setCards] = useState<Cards>(allLoading);
  const [exchange, setExchangeState] = useState("all");
  const ticket = useRef(0);
  const tickets = useRef<Partial<Record<RankingKind, number>>>({});
  const controllers = useRef<Partial<Record<RankingKind, AbortController>>>({});

  const update = (kind: RankingKind, next: (prev: CardState) => CardState) =>
    setCards((prev) => ({ ...prev, [kind]: next(prev[kind]) }));

  const run = useCallback(
    async (kinds: RankingKind[], mode: "initial" | "refresh", conditions: { exchange: string }) => {
      const claimed = kinds.map((kind) => {
        controllers.current[kind]?.abort();
        tickets.current[kind] = ++ticket.current;
        return [kind, ticket.current] as const;
      });
      setCards((prev) => {
        const next = { ...prev };
        for (const kind of kinds) {
          const card = prev[kind];
          // 기존 목록 유지는 같은 조건의 새로고침에만 적용한다.
          next[kind] =
            mode === "refresh" && card.status === "ready"
              ? { ...card, refreshing: true, refreshError: null }
              : { status: "loading" };
        }
        return next;
      });

      for (const [kind, mine] of claimed) {
        if (tickets.current[kind] !== mine) continue;
        const controller = new AbortController();
        controllers.current[kind] = controller;
        try {
          const data = await fetchRanking(environment, kind, conditions.exchange, controller.signal);
          if (tickets.current[kind] !== mine) continue;
          update(kind, () => ({ status: "ready", data, refreshing: false, refreshError: null }));
        } catch (error) {
          if (tickets.current[kind] !== mine) continue;
          const apiError =
            error instanceof ApiError ? error : new ApiError("unknown", "알 수 없는 오류입니다.", null);
          update(kind, (prev) =>
            prev.status === "ready"
              ? { ...prev, refreshing: false, refreshError: apiError }
              : { status: "error", error: apiError },
          );
        }
      }
    },
    [environment],
  );

  useEffect(() => {
    void run(KINDS, "initial", { exchange: "all" });
    const owned = controllers.current;
    const owner = tickets.current;
    return () => {
      for (const kind of KINDS) {
        owned[kind]?.abort();
        owner[kind] = -1;
      }
    };
  }, [run]);

  const busy = KINDS.some((kind) => {
    const card = cards[kind];
    return card.status === "loading" || (card.status === "ready" && card.refreshing);
  });
  const fetchedAt = KINDS.map((kind) => cards[kind])
    .flatMap((card) => (card.status === "ready" ? [card.data.fetched_at] : []))
    .sort()
    .at(-1);

  return {
    cards,
    exchange,
    busy,
    fetchedAt,
    setExchange: (value: string) => {
      setExchangeState(value);
      void run(KINDS, "initial", { exchange: value });
    },
    refresh: () => void run(KINDS, "refresh", { exchange }),
    // 같은 조건으로 다시 받으므로, 목록이 있던 카드(새로고침 실패)는 목록을 유지한다.
    retry: (kind: RankingKind) => void run([kind], "refresh", { exchange }),
  };
}
