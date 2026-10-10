import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  fetchRanking,
  type EnvironmentValue,
  type Ranking,
  type RankingConditions,
  type RankingKind,
} from "../../api";

export type CardState =
  | { status: "loading" }
  | { status: "error"; error: ApiError }
  | { status: "ready"; data: Ranking; refreshing: boolean; refreshError: ApiError | null };

/** 화면에 위에서부터 놓이는 순서. */
export const KINDS: RankingKind[] = ["trading_value", "gainers", "volume", "popular"];
/** 거래소 선택이 적용되는 카드. 인기 종목은 집계 구간만 따른다. */
const EXCHANGE_KINDS = KINDS.filter((kind) => kind !== "popular");
const INITIAL: RankingConditions = { exchange: "all", period: "1h" };

type Cards = Record<RankingKind, CardState>;

const allLoading = (): Cards =>
  Object.fromEntries(KINDS.map((kind) => [kind, { status: "loading" }])) as Cards;

/**
 * 순위 카드들의 데이터. 한 투자 환경에만 묶인다(환경이 바뀌면 컴포넌트가 새로 만들어진다).
 *
 * 카드는 동시에 조회한다. 같은 TR의 호출 간격은 서버가 맞춘다. 조회를 시작할 때 카드마다 번호표를 새로 받고,
 * 응답이 왔을 때 번호표가 바뀌었으면(조건 변경·다시 시도로 새 조회가 시작됨) 버린다.
 */
export function useRankings(environment: EnvironmentValue) {
  const [cards, setCards] = useState<Cards>(allLoading);
  const [conditions, setConditions] = useState<RankingConditions>(INITIAL);
  const ticket = useRef(0);
  const tickets = useRef<Partial<Record<RankingKind, number>>>({});
  const controllers = useRef<Partial<Record<RankingKind, AbortController>>>({});

  const update = (kind: RankingKind, next: (prev: CardState) => CardState) =>
    setCards((prev) => ({ ...prev, [kind]: next(prev[kind]) }));

  const run = useCallback(
    async (kinds: RankingKind[], mode: "initial" | "refresh", conditions: RankingConditions) => {
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

      await Promise.all(
        claimed.map(async ([kind, mine]) => {
          const controller = new AbortController();
          controllers.current[kind] = controller;
          try {
            const data = await fetchRanking(environment, kind, conditions, controller.signal);
            if (tickets.current[kind] !== mine) return;
            update(kind, () => ({ status: "ready", data, refreshing: false, refreshError: null }));
          } catch (error) {
            if (tickets.current[kind] !== mine) return;
            const apiError =
              error instanceof ApiError ? error : new ApiError("unknown", "알 수 없는 오류입니다.", null);
            update(kind, (prev) =>
              prev.status === "ready"
                ? { ...prev, refreshing: false, refreshError: apiError }
                : { status: "error", error: apiError },
            );
          }
        }),
      );
    },
    [environment],
  );

  useEffect(() => {
    void run(KINDS, "initial", INITIAL);
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
    conditions,
    busy,
    fetchedAt,
    setExchange: (exchange: string) => {
      const next = { ...conditions, exchange };
      setConditions(next);
      void run(EXCHANGE_KINDS, "initial", next);
    },
    setPeriod: (period: string) => {
      const next = { ...conditions, period };
      setConditions(next);
      void run(["popular"], "initial", next);
    },
    refresh: () => void run(KINDS, "refresh", conditions),
    // 같은 조건으로 다시 받으므로, 목록이 있던 카드(새로고침 실패)는 목록을 유지한다.
    retry: (kind: RankingKind) => void run([kind], "refresh", conditions),
  };
}
