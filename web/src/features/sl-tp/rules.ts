import type { SlTpSession } from "../../api";

// 서버(sl_tp.py)와 같은 검사 규칙. 서버가 최종 판정하고, 화면은 저장 전에 미리 알려 준다.

export interface RegularHours {
  start: string;
  end: string;
}

const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;
const PERCENT = /^\d+(\.\d{1,2})?$/;
// 익절·손절 기준(%)의 범위. 손절 기준은 양수로 두고 손실률을 뜻한다.
export const TAKE_PROFIT_RANGE = [0.01, 1000] as const;
export const STOP_LOSS_RANGE = [0.01, 99.99] as const;

export const TIMEZONE_NAMES: Record<string, string> = {
  "Asia/Seoul": "한국시간",
  "America/New_York": "뉴욕시간",
};

const toMinutes = (time: string) => Number(time.slice(0, 2)) * 60 + Number(time.slice(3));
const fromMinutes = (minutes: number) =>
  `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;

/**
 * "세션 추가"로 더할 세션. 마지막 세션의 종료 시각(없으면 정규장 시작)에서 1시간,
 * 정규장 끝을 넘지 않는다. 마지막 세션이 정규장 끝까지 차 있으면 null이다.
 */
export function nextSession(sessions: SlTpSession[], hours: RegularHours): SlTpSession | null {
  const last = sessions.at(-1)?.end;
  const start = last && TIME.test(last) ? last : hours.start;
  if (start >= hours.end) return null;
  const end = fromMinutes(Math.min(toMinutes(start) + 60, toMinutes(hours.end)));
  return { start, end, enabled: false, take_profit: null, stop_loss: null };
}

/** 세션마다 틀린 곳의 안내. 맞으면 null이다. HH:MM은 문자열 비교가 시각 비교와 같다. */
export function sessionErrors(sessions: SlTpSession[], hours: RegularHours): (string | null)[] {
  return sessions.map((session, index) => {
    const { start, end } = session;
    if (!TIME.test(start) || !TIME.test(end)) return "시작·종료 시각을 입력하세요.";
    if (start >= end) return "시작 시각이 종료 시각보다 빨라야 합니다.";
    if (start < hours.start || end > hours.end)
      return `정규장(${hours.start}–${hours.end}) 안에서만 정할 수 있습니다.`;
    const previous = sessions[index - 1]?.end;
    if (previous !== undefined && start < previous) return "시작 시각이 앞 세션의 종료 시각보다 이릅니다.";
    if (!validPercent(session.take_profit, TAKE_PROFIT_RANGE)) return rangeMessage("익절 기준", TAKE_PROFIT_RANGE);
    if (!validPercent(session.stop_loss, STOP_LOSS_RANGE)) return rangeMessage("손절 기준", STOP_LOSS_RANGE);
    if (session.enabled && session.take_profit === null && session.stop_loss === null)
      return "켜진 세션에는 익절 기준이나 손절 기준이 하나 이상 있어야 합니다.";
    return null;
  });
}

/** 정하지 않은 기준(null)도 맞는 값이다. */
export function validPercent(value: string | null, [low, high]: readonly [number, number]): boolean {
  if (value === null) return true;
  return PERCENT.test(value) && Number(value) >= low && Number(value) <= high;
}

const rangeMessage = (name: string, [low, high]: readonly [number, number]) =>
  `${name}은 ${low}~${high} 사이, 소수점 둘째 자리까지의 숫자여야 합니다.`;
