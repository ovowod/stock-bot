import type { RegularHours, SlTpSession } from "../../api";

// 서버(sl_tp.py)와 같은 검사 규칙. 서버가 최종 판정하고, 화면은 저장 전에 미리 알려 준다.

const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;
const PERCENT = /^\d+(\.\d{1,2})?$/;
// 익절·손절 기준(%)의 범위. 손절 기준은 양수로 두고 손실률을 뜻한다.
export const TAKE_PROFIT_RANGE = [0.01, 1000] as const;
export const STOP_LOSS_RANGE = [0.01, 99.99] as const;

export const TIMEZONE_NAMES: Record<string, string> = {
  "Asia/Seoul": "한국시간",
  "America/New_York": "뉴욕시간",
};

const KOREA = "Asia/Seoul";

/** timeZone에서 본 그 순간의 날짜·시각 부분(년·월·일·시·분). */
function zonedParts(instant: number, timeZone: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).formatToParts(instant);
  const get = (type: string) => Number(parts.find((part) => part.type === type)?.value);
  return { year: get("year"), month: get("month"), day: get("day"), hour: get("hour"), minute: get("minute") };
}

/**
 * 그 시장 시간대의 오늘 HH:MM이 한국시간으로 몇 시인지. 날짜가 넘어가면 "다음 날"을 붙인다.
 * 서머타임은 now의 날짜로 정해지므로 같은 정규장도 계절에 따라 한국시간이 1시간 달라진다.
 */
export function koreaTime(time: string, timeZone: string, now: Date = new Date()): string {
  const today = zonedParts(now.getTime(), timeZone);
  const wall = Date.UTC(today.year, today.month - 1, today.day, Number(time.slice(0, 2)), Number(time.slice(3)));
  // 그 시간대의 벽시계 시각이 wall이 되는 순간을 찾는다. 시차를 두 번 맞추면 서머타임 경계에서도 맞는다.
  let instant = wall;
  for (let i = 0; i < 2; i++) {
    const seen = zonedParts(instant, timeZone);
    instant += wall - Date.UTC(seen.year, seen.month - 1, seen.day, seen.hour, seen.minute);
  }
  const korea = zonedParts(instant, KOREA);
  const text = `${String(korea.hour).padStart(2, "0")}:${String(korea.minute).padStart(2, "0")}`;
  const nextDay = Date.UTC(korea.year, korea.month - 1, korea.day) > Date.UTC(today.year, today.month - 1, today.day);
  return nextDay ? `다음 날 ${text}` : text;
}

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

/** 한 세션에서 틀린 곳의 안내. 칸마다 따로 두어 그 칸 옆에 보여준다. 맞으면 null이다. */
export interface SessionErrors {
  time: string | null;
  takeProfit: string | null;
  stopLoss: string | null;
  /** 켜진 세션에 익절·손절 기준이 하나도 없을 때. */
  criteria: string | null;
}

/** HH:MM은 문자열 비교가 시각 비교와 같다. */
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

export const hasErrors = (errors: SessionErrors[]) =>
  errors.some((error) => Object.values(error).some((message) => message !== null));

function timeError({ start, end }: SlTpSession, previousEnd: string | undefined, hours: RegularHours) {
  if (!TIME.test(start) || !TIME.test(end)) return "시작·종료 시각을 입력하세요.";
  if (start >= end) return "시작 시각이 종료 시각보다 빨라야 합니다.";
  if (start < hours.start || end > hours.end) return `정규장(${hours.start}–${hours.end}) 안에서만 정할 수 있습니다.`;
  if (previousEnd !== undefined && start < previousEnd) return "시작 시각이 앞 세션의 종료 시각보다 이릅니다.";
  return null;
}

/** 정하지 않은 기준(null)도 맞는 값이다. */
export function validPercent(value: string | null, [low, high]: readonly [number, number]): boolean {
  if (value === null) return true;
  return PERCENT.test(value) && Number(value) >= low && Number(value) <= high;
}

const rangeMessage = (name: string, [low, high]: readonly [number, number]) =>
  `${name}은 ${low}~${high} 사이, 소수점 둘째 자리까지의 숫자여야 합니다.`;
