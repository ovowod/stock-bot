import type { RegularHours, TrailingStopSession } from "../../api";
import { nextSessionTimes, rangeMessage, timeError, validPercent } from "../auto-trading/rules";

// Trailing Stop 세션의 기준 칸 규칙(서버 trailing_stop/settings.py와 같다). 시각 규칙은 자동매매 기능들이 함께 쓴다.

// 두 값(%) 모두 양수로 둔다. 고점 대비 하락률 3은 고점보다 3% 낮은 가격을 뜻한다.
export const ACTIVATION_PROFIT_RANGE = [0.01, 1000] as const;
export const DROP_FROM_PEAK_RANGE = [0.01, 99.99] as const;

/** "세션 추가"로 더할 OFF 세션. 두 값은 비어 있다. 더할 수 없으면 null이다. */
export function nextSession(sessions: TrailingStopSession[], hours: RegularHours): TrailingStopSession | null {
  const times = nextSessionTimes(sessions, hours);
  return times && { ...times, enabled: false, activation_profit: null, drop_from_peak: null };
}

/** 한 세션에서 틀린 곳의 안내. 칸마다 따로 두어 그 칸 옆에 보여준다. 맞으면 null이다. */
export type SessionErrors = {
  time: string | null;
  activationProfit: string | null;
  dropFromPeak: string | null;
  /** 켜진 세션에 두 값 가운데 하나라도 없을 때. */
  criteria: string | null;
};

/** 두 값의 크기는 서로 비교하지 않는다. 하락률이 더 크면 손실로 팔릴 수 있지만 받는다. */
export function sessionErrors(sessions: TrailingStopSession[], hours: RegularHours): SessionErrors[] {
  return sessions.map((session, index) => ({
    time: timeError(session, sessions[index - 1]?.end, hours),
    activationProfit: validPercent(session.activation_profit, ACTIVATION_PROFIT_RANGE)
      ? null
      : rangeMessage("활성화 수익률", ACTIVATION_PROFIT_RANGE),
    dropFromPeak: validPercent(session.drop_from_peak, DROP_FROM_PEAK_RANGE)
      ? null
      : rangeMessage("고점 대비 하락률", DROP_FROM_PEAK_RANGE),
    criteria:
      session.enabled && (session.activation_profit === null || session.drop_from_peak === null)
        ? "켜진 세션에는 활성화 수익률과 고점 대비 하락률이 모두 있어야 합니다."
        : null,
  }));
}
