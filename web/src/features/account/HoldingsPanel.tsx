import { useEffect, useState } from "react";
import {
  ApiError,
  fetchTrailingStopStatus,
  type EnvironmentValue,
  type NotWatchingReason,
  type TrailingStopStatus,
  type TrailingStopStock,
} from "../../api";
import { EMPTY, formatRate } from "../../format";
import { Change, HoldingsList, Panel, type HoldingView } from "./parts";

type Detail = { label: string; value: string };

/** 보유종목 한 줄. 기본 보기 그대로의 줄과, Trailing Stop 보기가 쓰는 값. */
export interface HoldingRow {
  item: HoldingView;
  code: string;
  credit: boolean;
  currentPrice: number | null;
  purchasePrice: number | null;
  /** 이 시장의 가격 표기(국내 원, 미국 외화). 고점·트리거 가격에 쓴다. */
  formatPrice: (value: number | null) => string;
  /** Trailing Stop 보기에서도 그대로 두는 정보 줄 앞부분과 상세 칸(종목코드, 거래소). */
  keepInfo: string[];
  keepDetails: Detail[];
  /** 매도가능 수량. 보유 수량보다 적을 때만 담긴다. */
  sellable: Detail[];
}

type ViewMode = "basic" | "trailing_stop";

const VIEWS: { value: ViewMode; label: string }[] = [
  { value: "basic", label: "기본" },
  { value: "trailing_stop", label: "Trailing Stop" },
];
const VIEW_KEY = "stock-bot:holdings-view";
// 화면이 열려 있고 탭이 보이는 동안 상태를 다시 받는 간격.
const POLL_MS = 3000;

/** 저장값이 없거나 저장소를 쓸 수 없으면 기본 보기로 연다. */
function loadView(): ViewMode {
  try {
    return localStorage.getItem(VIEW_KEY) === "trailing_stop" ? "trailing_stop" : "basic";
  } catch {
    return "basic";
  }
}

function saveView(view: ViewMode): void {
  try {
    localStorage.setItem(VIEW_KEY, view);
  } catch {
    // 저장에 실패해도 지금 화면은 고른 보기로 동작한다.
  }
}

/** 보유종목 패널. 목록 위에서 [기본 | Trailing Stop] 보기를 바꾼다. */
export function HoldingsPanel({ environment, rows }: { environment: EnvironmentValue; rows: HoldingRow[] }) {
  const [view, setView] = useState<ViewMode>(loadView);
  const trailing = view === "trailing_stop";
  const { status, error } = useTrailingStopStatus(environment, trailing);
  const choose = (next: ViewMode) => {
    setView(next);
    saveView(next);
  };
  return (
    <Panel title="보유종목" aside={`${rows.length}개`}>
      <ViewToggle value={view} onChange={choose} />
      {trailing && (
        <div className="mb-2 space-y-2">
          {error && (
            <p role="alert" className="rounded-xl bg-real-soft px-3 py-2 text-sm break-keep text-real">
              Trailing Stop 상태를 불러오지 못했습니다.{status && " 마지막으로 받은 상태를 보여줍니다."} {error.message}
            </p>
          )}
          <p className="text-xs break-keep text-muted">
            수익률은 시세 체결가 기준이며 수수료·세금은 빼지 않습니다. 고점은 켜진 세션 동안의 체결가로 정합니다.
          </p>
          {status?.session && <p className="text-xs break-keep text-sub">지금 세션 {criteriaText(status)}</p>}
        </div>
      )}
      <HoldingsList
        items={rows.map((row) => (trailing ? trailingStopItem(row, status) : row.item))}
        expandable={trailing}
      />
    </Panel>
  );
}

function ViewToggle({ value, onChange }: { value: ViewMode; onChange: (value: ViewMode) => void }) {
  return (
    <div role="radiogroup" aria-label="보유종목 보기" className="mb-3 inline-flex gap-1 rounded-2xl bg-canvas p-1">
      {VIEWS.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => !active && onChange(option.value)}
            className={`rounded-xl px-3 py-1.5 text-[13px] font-semibold whitespace-nowrap transition ${
              active ? "bg-surface text-brand-700 shadow-[0_1px_3px_rgba(0,0,0,0.08)]" : "text-muted hover:text-sub"
            }`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/**
 * active인 동안 Trailing Stop 상태를 받는다. 처음과 탭이 다시 보일 때는 바로, 그 뒤로는 3초마다 받는다.
 * 탭이 숨으면 멈춘다. 실패하면 마지막으로 받은 상태를 두고 error에 담는다.
 */
function useTrailingStopStatus(environment: EnvironmentValue, active: boolean) {
  const [status, setStatus] = useState<TrailingStopStatus | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    if (!active) return;
    let timer: number | undefined;
    let controller: AbortController | null = null;
    const stop = () => {
      window.clearTimeout(timer);
      controller?.abort();
    };
    const load = async () => {
      stop();
      const current = new AbortController();
      controller = current;
      try {
        const data = await fetchTrailingStopStatus(environment, current.signal);
        if (current.signal.aborted) return;
        setStatus(data);
        setError(null);
      } catch (caught) {
        if (current.signal.aborted) return;
        setError(caught instanceof ApiError ? caught : new ApiError("unknown", "알 수 없는 오류입니다.", null));
      }
      // 받는 동안 탭이 숨었으면 visibilitychange가 이미 멈췄다. 다시 걸지 않는다.
      if (document.visibilityState === "visible") timer = window.setTimeout(load, POLL_MS);
    };
    const onVisibility = () => (document.visibilityState === "visible" ? void load() : stop());
    if (document.visibilityState === "visible") void load();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      stop();
    };
  }, [environment, active]);

  return { status, error };
}

const REASON: Record<NotWatchingReason, string> = {
  no_session: "켜진 세션 없음",
  real: "실전투자",
  holdings_pending: "보유종목 확인 중",
  settings_unreadable: "설정 파일을 읽지 못함",
  state_unreadable: "고점 기록을 읽지 못함",
  no_credentials: "인증정보 없음",
  connection_replaced: "실시간 연결이 밀려남",
  fills_rejected: "체결 등록 거부",
  quotes_rejected: "시세 등록 거부",
};

const INELIGIBLE: Record<NonNullable<TrailingStopStock["ineligible_reason"]>, string> = {
  no_purchase_price: "매입가 없음",
  duplicate: "같은 종목 여러 줄",
  unknown_exchange: "거래소 확인 불가",
};

type Tone = "muted" | "waiting" | "tracking" | "triggered";

const TONE: Record<Tone, string> = {
  muted: "bg-canvas text-muted",
  waiting: "bg-canvas text-sub",
  tracking: "bg-brand-50 text-brand-700",
  triggered: "bg-loss-soft text-loss",
};

/** Trailing Stop 상태. 이름과 이유를 가운뎃점으로 잇는다(예: 감시 안 함 · 켜진 세션 없음). */
interface StatusLabel {
  parts: string[];
  tone: Tone;
}

/** 서버의 상태와 계좌 줄을 합쳐 상태를 정한다. 순서가 우선순위다. 신용 줄은 stocks와 합치지 않는다. */
function statusLabel(row: HoldingRow, status: TrailingStopStatus, stock: TrailingStopStock | undefined): StatusLabel {
  const reason = status.not_watching_reason;
  const reasonText = reason ? [REASON[reason]] : [];
  if (reason === "real") return { parts: ["감시 안 함", REASON.real], tone: "muted" };
  if (status.excluded.includes(row.code)) return { parts: ["제외종목"], tone: "muted" };
  if (row.credit) return { parts: ["대상 아님", "신용"], tone: "muted" };
  if (stock?.status === "ineligible") {
    const why = stock.ineligible_reason ? [INELIGIBLE[stock.ineligible_reason]] : [];
    return { parts: ["대상 아님", ...why], tone: "muted" };
  }
  if (stock?.status === "tracking" || stock?.status === "triggered") {
    const name = stock.status === "tracking" ? "고점 추적 중" : "매도 조건 충족";
    // 기억은 남아 있지만 갱신이 멈췄다. 세션 밖인지 장애인지 구분한다.
    const paused = status.watching ? [] : reason === "no_session" ? ["세션 밖, 갱신 멈춤"] : reasonText;
    return { parts: [name, ...paused], tone: stock.status };
  }
  if (!status.watching) return { parts: ["감시 안 함", ...reasonText], tone: "muted" };
  if (stock?.status === "waiting") return { parts: ["활성화 대기"], tone: "waiting" };
  return { parts: ["감시 안 함", REASON.holdings_pending], tone: "muted" };
}

function price(value: string | null | undefined): number | null {
  return value === null || value === undefined ? null : Number(value);
}

/** Trailing Stop 보기의 줄. 오른쪽 값, 정보 줄, 상세 칸만 바꾸고 매도 버튼과 안내는 그대로 둔다. */
function trailingStopItem(row: HoldingRow, status: TrailingStopStatus | null): HoldingView {
  const stock = row.credit ? undefined : status?.stocks.find((s) => s.code === row.code);
  // 서버가 판정에 쓰는 값이 있으면 그것을 쓴다. 추가 매수 뒤 계좌를 새로고침하지 않아도 같은 수익률이 보인다.
  const current = price(stock?.last_price) ?? row.currentPrice;
  const purchase = price(stock?.purchase_price) ?? row.purchasePrice;
  const known = current !== null && purchase !== null && current !== 0 && purchase !== 0;
  const rate = known ? (current / purchase - 1) * 100 : null;
  const reach = !status
    ? EMPTY
    : stock?.activated_at
      ? `도달 · ${stampText(stock.activated_at, status.timezone)}`
      : known
        ? reachText(current, purchase, status)
        : EMPTY;
  const peak = price(stock?.peak);
  // 고점 대비 하락률. 고점이 없거나 가격을 모르면 계산하지 않는다.
  const drop = peak !== null && peak !== 0 && current !== null && current !== 0 ? (current / peak - 1) * 100 : null;
  const dropText = drop === null ? EMPTY : formatDrop(drop);
  const excluded = status ? status.excluded.includes(row.code) : null;
  const label = status ? statusLabel(row, status, stock) : null;
  const stamp = (iso: string | null | undefined) => (status && iso ? stampText(iso, status.timezone) : EMPTY);
  const triggered = stock?.triggered_at
    ? `${stamp(stock.triggered_at)} · ${row.formatPrice(price(stock.triggered_price))}`
    : EMPTY;
  return {
    ...row.item,
    value: <StatusBadge label={label} />,
    valueSub: undefined,
    change: <Change value={rate} format={formatRate} />,
    info: [
      ...row.keepInfo,
      // 정보 줄에는 핵심 하나만 둔다. 활성화됐으면 고점 대비 하락률, 아니면 도달 여부다.
      // 나머지는 줄을 눌러 펼친 상세에서 본다. 가격을 몰라 알 수 없으면 정보 줄에서 뺀다.
      ...(peak !== null ? [`고점 대비 ${dropText}`] : reach === EMPTY ? [] : [reach]),
      ...row.sellable.map((d) => `${d.label} ${d.value}`),
    ],
    details: [
      { label: "상태", value: label ? label.parts.join(" · ") : EMPTY },
      ...row.keepDetails,
      { label: "도달 여부", value: reach },
      { label: "고점 대비", value: dropText },
      { label: "고점", value: peak === null ? EMPTY : row.formatPrice(peak) },
      { label: "고점 시각", value: stamp(stock?.peak_at) },
      { label: "활성화 시각", value: stamp(stock?.activated_at) },
      { label: "매도 조건 충족", value: triggered },
      { label: "지금 세션 기준", value: status ? criteriaText(status) : EMPTY },
      { label: "제외종목", value: excluded === null ? EMPTY : excluded ? "예" : "아니요" },
      ...row.sellable,
    ],
  };
}

/**
 * 아직 활성화되지 않은 종목의 활성화 수익률 도달 여부. 지금 시각을 포함하는 세션의 기준과 비교한다.
 * 세션이 없거나 그 세션에 활성화 수익률이 없으면(꺼진 세션은 비어 있을 수 있다) 비교하지 않는다.
 * 감시 중이 아니면 활성화되지 않는다. 켜진 세션이 없으면 "세션 밖", 장애·실전투자로 멈췄으면
 * "감시 안 함"이다(이유는 상태 칸에 있다). 감시 중인데 활성화 전이면 서버가 그 가격의
 * 시세 체결을 아직 판정하지 않은 것이다(계좌 현재가로 계산한 경우).
 */
function reachText(
  current: number,
  purchase: number,
  status: TrailingStopStatus,
): string {
  // 비교할 기준이 없으면 –보다 왜 없는지를 보인다.
  if (!status.session) return "기준 없음 (세션 없음)";
  const activation = status.session.activation_profit;
  if (!activation) return "기준 없음";
  // 서버 판정(가격 ≥ 평균 매입가 × (1 + 활성화 수익률/100))과 같은 식을 나눗셈 없이 비교해 경계에서 어긋나지 않게 한다.
  const reached = (current - purchase) * 100 >= purchase * Number(activation);
  if (!reached) return `미도달 (기준 ${activation}%)`;
  if (status.watching) return "도달 (판정 전)";
  return status.not_watching_reason === "no_session" ? "도달 (세션 밖)" : "도달 (감시 안 함)";
}

/** 고점 대비 하락률. 소수 첫째 자리까지, 내려가면 −를 붙인다(예: −1.0%). */
function formatDrop(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  if (rounded === 0) return "0.0%";
  return `${rounded < 0 ? "−" : "+"}${Math.abs(rounded).toFixed(1)}%`;
}

/** 지금 세션의 기준. 꺼진 세션은 기준이 비어 있을 수 있다. */
function criteriaText(status: TrailingStopStatus): string {
  const session = status.session;
  if (!session) return EMPTY;
  const parts = [
    session.activation_profit ? `활성화 +${session.activation_profit}%` : null,
    session.drop_from_peak ? `고점 대비 −${session.drop_from_peak}%` : null,
  ].filter((part) => part !== null);
  const span = `${session.start}–${session.end}${session.enabled ? "" : " (꺼짐)"}`;
  return parts.length ? `${span} · ${parts.join(" · ")}` : span;
}

/** 투자 환경 시간대의 시각. 그 시간대에서 오늘이 아니면 날짜를 붙인다(예: 11/01 10:03). */
function stampText(iso: string, timeZone: string): string {
  const parts = (date: Date) =>
    Object.fromEntries(
      new Intl.DateTimeFormat("en-CA", {
        timeZone,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
      })
        .formatToParts(date)
        .map((part) => [part.type, part.value]),
    );
  const at = parts(new Date(iso));
  const today = parts(new Date());
  const time = `${at.hour}:${at.minute}`;
  const sameDay = at.year === today.year && at.month === today.month && at.day === today.day;
  return sameDay ? time : `${at.month}/${at.day} ${time}`;
}

function StatusBadge({ label }: { label: StatusLabel | null }) {
  if (!label) return <span className="text-muted">{EMPTY}</span>;
  return (
    // 좁은 화면은 상태 이름만 한 줄로 두고, 이유는 줄을 눌러 펼친 상세의 "상태"에 둔다.
    // 이유까지 넣으면 오른쪽 칸에서 두 줄로 꺾여 목록 줄 높이가 들쭉날쭉해진다.
    <span
      className={`inline-block rounded-lg px-2 py-0.5 text-xs leading-snug font-bold whitespace-nowrap md:max-w-56 md:whitespace-normal md:break-keep ${TONE[label.tone]}`}
    >
      {label.parts[0]}
      {label.parts.length > 1 && <span className="hidden md:inline"> · {label.parts.slice(1).join(" · ")}</span>}
    </span>
  );
}
