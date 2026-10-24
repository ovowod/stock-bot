import type { SlTpSession } from "../../api";

// 서버(sl_tp.py)와 같은 검사 규칙. 서버가 최종 판정하고, 화면은 저장 전에 미리 알려 준다.

export interface RegularHours {
  start: string;
  end: string;
}

const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;

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
    return null;
  });
}
