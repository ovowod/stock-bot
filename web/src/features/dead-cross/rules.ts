import type { DeadCrossSession, RegularHours } from "../../api";
import type { EnvironmentOption } from "../../environments";
import { nextSessionTimes, timeError } from "../auto-trading/rules";

// Dead Cross 세션의 기준 칸 규칙(서버 dead_cross/settings.py와 같다). 시각 규칙은 자동매매 기능들이 함께 쓴다.

type Market = EnvironmentOption["market"];

// 키움 차트가 주는 분봉 단위(국내 ka10080, 미국 usa06011)와 일봉. 시장마다 다르다.
const MINUTES: Record<Market, number[]> = {
  domestic: [1, 3, 5, 10, 15, 30, 45, 60],
  us: [1, 3, 5, 10, 15, 30, 40, 60, 120],
};

/** 그 시장에서 고를 수 있는 봉 주기. value는 서버에 저장하는 값이다. */
export const barPeriods = (market: Market) => [
  ...MINUTES[market].map((minutes) => ({ value: `${minutes}m`, label: `${minutes}분봉` })),
  { value: "1d", label: "일봉" },
];

// 봉 개수. 키움 명세에 조회 한도가 없어 앱이 정한 값이다. 매도 단계에서 모의투자로 재서 조정한다.
export const SHORT_PERIOD_RANGE = [2, 119] as const;
export const LONG_PERIOD_RANGE = [3, 120] as const;

/** "세션 추가"로 더할 OFF 세션. 봉 주기와 두 기간은 비어 있다. 더할 수 없으면 null이다. */
export function nextSession(sessions: DeadCrossSession[], hours: RegularHours): DeadCrossSession | null {
  const times = nextSessionTimes(sessions, hours);
  return times && { ...times, enabled: false, bar_period: null, short_period: null, long_period: null };
}

/** 한 세션에서 틀린 곳의 안내. 칸마다 따로 두어 그 칸 옆에 보여준다. 맞으면 null이다. */
export type SessionErrors = {
  time: string | null;
  barPeriod: string | null;
  shortPeriod: string | null;
  longPeriod: string | null;
  /** 단기가 장기 이상일 때. 켜짐 여부와 관계없이 본다. */
  order: string | null;
  /** 켜진 세션에 세 값 가운데 하나라도 없을 때. */
  criteria: string | null;
};

/** 정하지 않은 값(null)도 맞는 값이다. */
export function validBarCount(value: number | null, [low, high]: readonly [number, number]): boolean {
  return value === null || (Number.isInteger(value) && value >= low && value <= high);
}

const countMessage = (name: string, [low, high]: readonly [number, number]) =>
  `${name}은 ${low}~${high} 사이의 정수여야 합니다.`;

export function sessionErrors(sessions: DeadCrossSession[], hours: RegularHours, market: Market): SessionErrors[] {
  const offered = new Set(barPeriods(market).map((period) => period.value));
  return sessions.map((session, index) => {
    const shortValid = validBarCount(session.short_period, SHORT_PERIOD_RANGE);
    const longValid = validBarCount(session.long_period, LONG_PERIOD_RANGE);
    const { short_period: short, long_period: long } = session;
    return {
      time: timeError(session, sessions[index - 1]?.end, hours),
      barPeriod:
        session.bar_period === null || offered.has(session.bar_period)
          ? null
          : "봉 주기가 이 시장에서 고를 수 있는 값이 아닙니다.",
      shortPeriod: shortValid ? null : countMessage("단기 이동평균 기간", SHORT_PERIOD_RANGE),
      longPeriod: longValid ? null : countMessage("장기 이동평균 기간", LONG_PERIOD_RANGE),
      order:
        shortValid && longValid && short !== null && long !== null && short >= long
          ? "단기 이동평균 기간은 장기 이동평균 기간보다 작아야 합니다."
          : null,
      criteria:
        session.enabled && (session.bar_period === null || short === null || long === null)
          ? "켜진 세션에는 봉 주기와 단기·장기 이동평균 기간이 모두 있어야 합니다."
          : null,
    };
  });
}
