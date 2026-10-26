import { Info, LoaderCircle, Plus, Search, Trash2, X } from "lucide-react";
import type { EnvironmentValue, ExcludedStock, RegularHours, SlTpSession, SlTpSettings, StockSearchItem } from "../../api";
import { findEnvironment } from "../../environments";
import { ErrorNotice, Panel } from "../account/parts";
import { useStockSearch } from "../search/useStockSearch";
import {
  hasErrors,
  koreaTime,
  nextSession,
  sessionErrors,
  STOP_LOSS_RANGE,
  TAKE_PROFIT_RANGE,
  TIMEZONE_NAMES,
  validPercent,
  type SessionErrors,
} from "./rules";
import { useSlTp } from "./useSlTp";

export function SlTpPage({ environment }: { environment: EnvironmentValue }) {
  const env = findEnvironment(environment);
  const { state, draft, setDraft, dirty, saving, save, retry } = useSlTp(environment);
  const errors = state.status === "ready" ? sessionErrors(draft.sessions, state.saved.regular_hours) : [];
  const invalid = hasErrors(errors);

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <div className="min-w-0">
        <h2 className="text-2xl font-bold md:text-[28px]">SL / TP</h2>
        <p className="mt-1.5 text-sm font-semibold text-sub">{env.label}</p>
      </div>

      <div className="space-y-2 rounded-3xl bg-brand-50 p-5 text-[15px] text-brand-700">
        <p className="flex gap-2">
          <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span>
            평균 매입가 대비 수익률이 익절 기준 이상이거나 손절 기준 이하가 되면, 그 종목의 매도 가능 수량
            전량을 시장가로 매도합니다. 제외종목은 매도하지 않습니다.
          </span>
        </p>
        <p className="pl-6 font-semibold">
          이 화면을 닫아도 서버가 켜져 있으면 동작합니다. 서버와 키움의 연결이 끊긴 동안에는 판정하지 않습니다.
        </p>
      </div>

      {state.status === "loading" && (
        <div role="status" className="flex items-center gap-2 rounded-3xl bg-surface p-6 text-sub">
          <LoaderCircle className="size-5 animate-spin text-brand-600" aria-hidden />
          설정을 불러오는 중입니다
        </div>
      )}
      {state.status === "error" && (
        <ErrorNotice error={state.error} onRetry={retry} fallbackTitle="SL / TP 설정을 불러오지 못했습니다" />
      )}
      {state.status === "ready" && (
        <>
          <ExcludedSection
            environment={environment}
            excluded={draft.excluded}
            disabled={saving}
            onChange={(excluded) => setDraft((current) => ({ ...current, excluded }))}
          />
          <SessionsSection
            settings={state.saved}
            sessions={draft.sessions}
            errors={errors}
            disabled={saving}
            onChange={(sessions) => setDraft((current) => ({ ...current, sessions }))}
          />
          <div className="sticky bottom-4 flex items-center justify-end gap-3 rounded-3xl bg-surface p-4 shadow-[0_2px_12px_rgba(0,0,0,0.08)]">
            {dirty && <p className="mr-auto text-sm font-semibold text-real">저장하지 않은 변경</p>}
            <button
              type="button"
              onClick={() => void save()}
              disabled={!dirty || invalid || saving}
              className="rounded-2xl bg-brand-600 px-6 py-3 text-[15px] font-bold text-white transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-line disabled:text-muted"
            >
              {saving ? "저장 중" : "저장"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function ExcludedSection({
  environment,
  excluded,
  disabled,
  onChange,
}: {
  environment: EnvironmentValue;
  excluded: ExcludedStock[];
  disabled: boolean;
  onChange: (excluded: ExcludedStock[]) => void;
}) {
  const env = findEnvironment(environment);
  const { query, state, change, retry } = useStockSearch(environment);
  const result = state.status === "ready" ? state.result : state.status === "searching" ? state.previous : null;
  const codes = new Set(excluded.map((stock) => stock.code));

  const add = (item: StockSearchItem) => {
    onChange([...excluded, { code: item.code, name: item.name, exchange: env.market === "us" ? item.exchange : null }]);
    change("");
  };

  return (
    <Panel title="제외종목" label="제외종목">
      <p className="-mt-1 mb-3 text-sm text-muted">
        보유하고 있어도 매도하지 않을 종목입니다. 이 환경의 모든 SL/TP 세션에 똑같이 적용됩니다.
      </p>
      <label className="flex items-center gap-2.5 rounded-2xl bg-canvas px-4 py-3 focus-within:ring-2 focus-within:ring-brand-100">
        <Search className="size-5 shrink-0 text-muted" aria-hidden />
        <input
          type="search"
          value={query}
          onChange={(event) => change(event.target.value)}
          placeholder={env.market === "us" ? "종목명(한글·영문) 또는 티커" : "종목명 또는 종목코드"}
          aria-label="제외종목 검색어"
          maxLength={50}
          disabled={disabled}
          className="min-w-0 flex-1 bg-transparent text-[16px] outline-none placeholder:text-muted disabled:cursor-not-allowed"
        />
        {state.status === "searching" && (
          <LoaderCircle className="size-5 shrink-0 animate-spin text-brand-600" aria-label="검색 중" role="status" />
        )}
      </label>

      {state.status === "error" && (
        <div className="mt-2">
          <ErrorNotice error={state.error} onRetry={retry} compact fallbackTitle="종목을 검색하지 못했습니다" />
        </div>
      )}
      {query.trim() && result && (
        <div aria-label="제외종목 검색 결과" role="region" className="mt-2 max-h-72 overflow-y-auto rounded-2xl border border-line">
          {result.items.length === 0 ? (
            <p className="px-4 py-3 text-sm text-muted">검색 결과가 없습니다</p>
          ) : (
            <ul>
              {result.items.map((item) => {
                const added = codes.has(item.code);
                return (
                  <li key={`${item.exchange ?? item.category}-${item.code}`}>
                    <button
                      type="button"
                      onClick={() => add(item)}
                      disabled={added || disabled}
                      className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-canvas/70 disabled:cursor-not-allowed"
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[15px] font-semibold">{item.name}</span>
                        <span className="block truncate text-xs text-muted">
                          {[item.code, item.exchange ?? item.category].filter(Boolean).join(" · ")}
                        </span>
                      </span>
                      {added && <span className="shrink-0 text-xs font-semibold text-muted">추가됨</span>}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}

      {excluded.length === 0 ? (
        <p className="mt-4 text-center text-sm text-muted">제외종목이 없습니다</p>
      ) : (
        <ul aria-label="제외종목 목록" className="mt-3 flex flex-wrap gap-2">
          {excluded.map((stock) => (
            <li key={stock.code} className="flex min-w-0 items-center gap-1 rounded-full bg-canvas py-1.5 pr-1.5 pl-3.5">
              <span className="min-w-0 truncate text-sm">
                <span className="font-semibold">{stock.name}</span>{" "}
                <span className="text-muted">{[stock.code, stock.exchange].filter(Boolean).join(" · ")}</span>
              </span>
              <button
                type="button"
                aria-label={`${stock.name} 삭제`}
                onClick={() => onChange(excluded.filter((other) => other.code !== stock.code))}
                disabled={disabled}
                className="rounded-full p-1 text-muted hover:bg-line hover:text-ink disabled:cursor-not-allowed"
              >
                <X className="size-4" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function SessionsSection({
  settings,
  sessions,
  errors,
  disabled,
  onChange,
}: {
  settings: SlTpSettings;
  sessions: SlTpSession[];
  errors: SessionErrors[];
  disabled: boolean;
  onChange: (sessions: SlTpSession[]) => void;
}) {
  const hours = settings.regular_hours;
  const next = nextSession(sessions, hours);
  const update = (index: number, change: Partial<SlTpSession>) =>
    onChange(sessions.map((session, i) => (i === index ? { ...session, ...change } : session)));

  return (
    <Panel title="SL/TP 세션" label="SL/TP 세션">
      <p className="-mt-1 mb-3 text-sm text-muted">
        정규장 {hours.start}–{hours.end} · {TIMEZONE_NAMES[settings.timezone] ?? settings.timezone}(
        {settings.timezone})
        {/* 미국 정규장은 한국시간으로 바꿔 함께 보여준다. 서머타임에 따라 1시간 달라져 오늘 기준이다. */}
        {settings.timezone !== "Asia/Seoul" &&
          ` · 한국시간 ${koreaTime(hours.start, settings.timezone)}–${koreaTime(hours.end, settings.timezone)} (오늘 기준)`}
      </p>
      {!settings.can_enable && (
        <p className="mb-3 rounded-2xl bg-real-soft px-4 py-3 text-sm font-semibold text-real">
          실전투자에서는 SL/TP 세션을 켤 수 없습니다
        </p>
      )}
      {sessions.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted">SL/TP 세션이 없습니다</p>
      ) : (
        <ul aria-label="SL/TP 세션 목록" className="space-y-3">
          {sessions.map((session, index) => {
            const n = index + 1;
            const error = errors[index];
            // 안내를 그 칸의 설명(aria-describedby)으로 이어, 화면 낭독기도 칸과 함께 읽는다.
            const ids = { time: `sl-tp-${n}-time`, takeProfit: `sl-tp-${n}-take-profit`, stopLoss: `sl-tp-${n}-stop-loss` };
            return (
              <li key={index} className="rounded-2xl bg-canvas p-4">
                {/* 좁은 화면에서도 줄이 어긋나지 않게 제목·스위치, 시각, 기준을 한 줄씩 둔다. */}
                <div className="flex items-center gap-3">
                  <span className="text-[15px] font-bold">세션 {n}</span>
                  <Switch
                    label={`${n}번째 세션 켜기`}
                    checked={session.enabled}
                    disabled={disabled || !settings.can_enable}
                    onChange={(enabled) => update(index, { enabled })}
                  />
                  <button
                    type="button"
                    aria-label={`${n}번째 세션 삭제`}
                    onClick={() => onChange(sessions.filter((_, i) => i !== index))}
                    disabled={disabled}
                    className="ml-auto rounded-xl p-2 text-muted hover:bg-line hover:text-ink disabled:cursor-not-allowed"
                  >
                    <Trash2 className="size-4" aria-hidden />
                  </button>
                </div>
                <div className="mt-3 flex items-center gap-2">
                  <TimeInput
                    label={`${n}번째 세션 시작 시각`}
                    value={session.start}
                    hours={hours}
                    describedBy={error.time ? ids.time : undefined}
                    disabled={disabled}
                    onChange={(start) => update(index, { start })}
                  />
                  <span className="text-muted">~</span>
                  <TimeInput
                    label={`${n}번째 세션 종료 시각`}
                    value={session.end}
                    hours={hours}
                    describedBy={error.time ? ids.time : undefined}
                    disabled={disabled}
                    onChange={(end) => update(index, { end })}
                  />
                </div>
                <FieldError id={ids.time} message={error.time} />
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <div className="min-w-0">
                    <PercentInput
                      name="익절"
                      sign="+"
                      label={`${n}번째 세션 익절 기준(%)`}
                      value={session.take_profit}
                      describedBy={error.takeProfit ? ids.takeProfit : undefined}
                      disabled={disabled}
                      onChange={(take_profit) => update(index, { take_profit })}
                    />
                    <FieldError id={ids.takeProfit} message={error.takeProfit} />
                  </div>
                  <div className="min-w-0">
                    <PercentInput
                      name="손절"
                      sign="−"
                      label={`${n}번째 세션 손절 기준(%)`}
                      value={session.stop_loss}
                      describedBy={error.stopLoss ? ids.stopLoss : undefined}
                      disabled={disabled}
                      onChange={(stop_loss) => update(index, { stop_loss })}
                    />
                    <FieldError id={ids.stopLoss} message={error.stopLoss} />
                  </div>
                </div>
                <FieldError message={error.criteria} />
                <Summary session={session} />
              </li>
            );
          })}
        </ul>
      )}
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => next && onChange([...sessions, next])}
          disabled={!next || disabled}
          className="inline-flex items-center gap-1.5 rounded-xl bg-brand-50 px-4 py-2.5 text-sm font-semibold text-brand-700 hover:bg-brand-100 disabled:cursor-not-allowed disabled:bg-canvas disabled:text-muted"
        >
          <Plus className="size-4" aria-hidden />
          세션 추가
        </button>
        {!next && <p className="text-sm text-muted">정규장 끝까지 SL/TP 세션이 있습니다</p>}
      </div>
    </Panel>
  );
}

function TimeInput({
  label,
  value,
  hours,
  describedBy,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  hours: RegularHours;
  describedBy: string | undefined;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <input
      type="time"
      aria-label={label}
      aria-describedby={describedBy}
      value={value}
      min={hours.start}
      max={hours.end}
      step={60}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      className="min-w-0 flex-1 rounded-xl bg-surface px-3 py-2 text-[15px] font-semibold tabular-nums disabled:cursor-not-allowed disabled:text-muted"
    />
  );
}

function Switch({
  label,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  checked: boolean;
  disabled: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <span className="flex items-center gap-2">
      <button
        type="button"
        role="switch"
        aria-label={label}
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`relative h-7 w-12 shrink-0 rounded-full transition disabled:cursor-not-allowed disabled:opacity-50 ${
          checked ? "bg-brand-600" : "bg-line"
        }`}
      >
        <span
          className={`absolute top-1 size-5 rounded-full bg-white shadow transition-all ${checked ? "left-6" : "left-1"}`}
          aria-hidden
        />
      </button>
      <span className={`text-sm font-semibold ${checked ? "text-brand-700" : "text-muted"}`}>
        {checked ? "ON" : "OFF"}
      </span>
    </span>
  );
}

function PercentInput({
  name,
  sign,
  label,
  value,
  describedBy,
  disabled,
  onChange,
}: {
  name: string;
  sign: string;
  label: string;
  value: string | null;
  describedBy: string | undefined;
  disabled: boolean;
  onChange: (value: string | null) => void;
}) {
  return (
    <label className="flex min-w-0 items-center gap-1 rounded-xl bg-surface px-3 py-2 text-sm">
      <span className="shrink-0 font-semibold text-sub">{name}</span>
      <span className="flex min-w-0 flex-1 items-center justify-end gap-1">
        <span className="text-muted" aria-hidden>
          {sign}
        </span>
        <input
          type="text"
          inputMode="decimal"
          aria-label={label}
          aria-describedby={describedBy}
          value={value ?? ""}
          placeholder="없음"
          maxLength={8}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value.trim() === "" ? null : event.target.value.trim())}
          className="w-full min-w-0 bg-transparent text-right text-[15px] font-semibold tabular-nums outline-none placeholder:font-normal placeholder:text-muted disabled:cursor-not-allowed disabled:text-muted"
        />
        <span className="text-muted" aria-hidden>
          %
        </span>
      </span>
    </label>
  );
}

function FieldError({ id, message }: { id?: string; message: string | null }) {
  if (!message) return null;
  return (
    <p id={id} className="mt-1.5 text-sm font-semibold text-real">
      {message}
    </p>
  );
}

/** 정한 기준을 부호와 함께 한 줄로 보여준다. 손절 기준 3은 −3%다. */
function Summary({ session }: { session: SlTpSession }) {
  const parts = [
    session.take_profit !== null && validPercent(session.take_profit, TAKE_PROFIT_RANGE)
      ? `익절 +${session.take_profit}% 이상`
      : null,
    session.stop_loss !== null && validPercent(session.stop_loss, STOP_LOSS_RANGE)
      ? `손절 −${session.stop_loss}% 이하`
      : null,
  ].filter(Boolean);
  if (parts.length === 0) return null;
  return <p className="mt-2 text-sm text-sub">{parts.join(" · ")}</p>;
}
