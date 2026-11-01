import type { RegularHours, SlTpSession } from "../../api";
import { nextSessionTimes, rangeMessage, timeError, validPercent } from "../auto-trading/rules";

// SL/TP 세션의 기준 칸 규칙(서버 sl_tp.py와 같다). 시각 규칙은 자동매매 기능들이 함께 쓴다.

// 익절·손절 기준(%)의 범위. 손절 기준은 양수로 두고 손실률을 뜻한다.
export const TAKE_PROFIT_RANGE = [0.01, 1000] as const;
export const STOP_LOSS_RANGE = [0.01, 99.99] as const;

/** "세션 추가"로 더할 OFF 세션. 기준은 비어 있다. 더할 수 없으면 null이다. */
export function nextSession(sessions: SlTpSession[], hours: RegularHours): SlTpSession | null {
  const times = nextSessionTimes(sessions, hours);
  return times && { ...times, enabled: false, take_profit: null, stop_loss: null };
}

/** 한 세션에서 틀린 곳의 안내. 칸마다 따로 두어 그 칸 옆에 보여준다. 맞으면 null이다. */
export type SessionErrors = {
  time: string | null;
  takeProfit: string | null;
  stopLoss: string | null;
  /** 켜진 세션에 익절·손절 기준이 하나도 없을 때. */
  criteria: string | null;
};

export function sessionErrors(sessions: SlTpSession[], hours: RegularHours): SessionErrors[] {
  return sessions.map((session, index) => ({
    time: timeError(session, sessions[index - 1]?.end, hours),
    takeProfit: validPercent(session.take_profit, TAKE_PROFIT_RANGE) ? null : rangeMessage("익절 기준", TAKE_PROFIT_RANGE),
    stopLoss: validPercent(session.stop_loss, STOP_LOSS_RANGE) ? null : rangeMessage("손절 기준", STOP_LOSS_RANGE),
    criteria:
      session.enabled && session.take_profit === null && session.stop_loss === null
        ? "켜진 세션에는 익절 기준이나 손절 기준이 하나 이상 있어야 합니다."
        : null,
  }));
}
