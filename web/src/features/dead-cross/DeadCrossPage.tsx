import { useState } from "react";
import { fetchDeadCross, saveDeadCross, type DeadCrossSession, type EnvironmentValue } from "../../api";
import type { EnvironmentOption } from "../../environments";
import { AutoTradingSettingsPage, FieldError } from "../auto-trading/AutoTradingSettingsPage";
import type { FeatureApi } from "../auto-trading/useAutoTradingSettings";
import { barPeriods, LONG_PERIOD_RANGE, nextSession, SHORT_PERIOD_RANGE, sessionErrors, validBarCount } from "./rules";

const DEAD_CROSS: FeatureApi<DeadCrossSession> = {
  name: "Dead Cross",
  fetch: fetchDeadCross,
  save: saveDeadCross,
};

/** Dead Cross 설정 화면. 매도 동작은 아직 없고, 상태는 계좌 확인의 Dead Cross 보기에서 본다. */
export function DeadCrossPage({ environment }: { environment: EnvironmentValue }) {
  return (
    <AutoTradingSettingsPage
      environment={environment}
      feature={DEAD_CROSS}
      notice={[
        "봉 주기마다 완료된 봉의 종가로 단기·장기 이동평균(단순 이동평균)을 계산해, 단기 이동평균이 장기 이동평균을 하향 돌파하면 그 종목의 매도 가능 수량 전량을 시장가로 매도합니다. 제외종목은 매도하지 않습니다.",
        "지금은 상태만 계산하고 팔지 않습니다. 계좌 확인 화면의 Dead Cross 보기에서 확인하세요.",
      ]}
      sessionName="Dead Cross 세션"
      idPrefix="dead-cross"
      nextSession={nextSession}
      sessionErrors={sessionErrors}
      renderDetails={({ session, n, error, market, disabled, update }) => {
        const ids = {
          bar: `dead-cross-${n}-bar`,
          short: `dead-cross-${n}-short`,
          long: `dead-cross-${n}-long`,
          order: `dead-cross-${n}-order`,
        };
        const periodsDescribedBy = (own: string, ownError: string | null) =>
          [ownError ? own : null, error.order ? ids.order : null].filter(Boolean).join(" ") || undefined;
        return (
          <>
            {/* 칸 이름이 길어 좁은 화면에서는 값 자리가 모자라므로 한 줄에 하나씩 둔다. */}
            <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
              <div className="min-w-0">
                <label className="flex min-w-0 items-center gap-1 rounded-xl bg-surface px-3 py-2 text-sm">
                  <span className="shrink-0 font-semibold text-sub">봉 주기</span>
                  <select
                    aria-label={`${n}번째 세션 봉 주기`}
                    aria-describedby={error.barPeriod ? ids.bar : undefined}
                    value={session.bar_period ?? ""}
                    disabled={disabled}
                    onChange={(event) => update({ bar_period: event.target.value || null })}
                    className="w-full min-w-0 bg-transparent text-right text-[15px] font-semibold outline-none disabled:cursor-not-allowed disabled:text-muted"
                  >
                    <option value="">선택</option>
                    {barPeriods(market).map((period) => (
                      <option key={period.value} value={period.value}>
                        {period.label}
                      </option>
                    ))}
                  </select>
                </label>
                <FieldError id={ids.bar} message={error.barPeriod} />
              </div>
              <div className="min-w-0">
                <BarCountInput
                  name="단기 이동평균"
                  label={`${n}번째 세션 단기 이동평균 기간(봉)`}
                  value={session.short_period}
                  describedBy={periodsDescribedBy(ids.short, error.shortPeriod)}
                  disabled={disabled}
                  onChange={(short_period) => update({ short_period })}
                />
                <FieldError id={ids.short} message={error.shortPeriod} />
              </div>
              <div className="min-w-0">
                <BarCountInput
                  name="장기 이동평균"
                  label={`${n}번째 세션 장기 이동평균 기간(봉)`}
                  value={session.long_period}
                  describedBy={periodsDescribedBy(ids.long, error.longPeriod)}
                  disabled={disabled}
                  onChange={(long_period) => update({ long_period })}
                />
                <FieldError id={ids.long} message={error.longPeriod} />
              </div>
            </div>
            <FieldError id={ids.order} message={error.order} />
            <FieldError message={error.criteria} />
            <Summary session={session} market={market} />
          </>
        );
      }}
    />
  );
}

/** 비우면 null, 정수가 아닌 글자는 NaN으로 두어 칸 안내가 뜨고 저장이 막히게 한다. */
const parseBarCount = (text: string) => (text === "" ? null : /^\d+$/.test(text) ? Number(text) : NaN);

/**
 * 이동평균 기간 입력. 단위는 봉 개수다. number 입력 칸은 휠로 값이 바뀌고 브라우저에 따라
 * 틀린 글자를 빈 값으로 넘기므로, 글자 칸에 입력한 글자를 그대로 두고 값으로 바꾼다.
 */
function BarCountInput({
  name,
  label,
  value,
  describedBy,
  disabled,
  onChange,
}: {
  name: string;
  label: string;
  value: number | null;
  describedBy: string | undefined;
  disabled: boolean;
  onChange: (value: number | null) => void;
}) {
  const [text, setText] = useState(value === null ? "" : String(value));
  // 저장 응답이나 세션 삭제로 값이 바뀌면 그 값을 보인다. 입력 중인 틀린 글자(NaN)는 그대로 둔다.
  const shown = Object.is(parseBarCount(text), value) ? text : value === null ? "" : String(value);
  return (
    <label className="flex min-w-0 items-center gap-1 rounded-xl bg-surface px-3 py-2 text-sm">
      <span className="shrink-0 font-semibold text-sub">{name}</span>
      <span className="flex min-w-0 flex-1 items-center justify-end gap-1">
        <input
          type="text"
          inputMode="numeric"
          aria-label={label}
          aria-describedby={describedBy}
          value={shown}
          placeholder="없음"
          maxLength={4}
          disabled={disabled}
          onChange={(event) => {
            const next = event.target.value.trim();
            setText(next);
            onChange(parseBarCount(next));
          }}
          className="w-full min-w-0 bg-transparent text-right text-[15px] font-semibold tabular-nums outline-none placeholder:font-normal placeholder:text-muted disabled:cursor-not-allowed disabled:text-muted"
        />
        <span className="shrink-0 text-muted">봉</span>
      </span>
    </label>
  );
}

/** 정한 값을 "5분봉 · 단기 5봉 · 장기 20봉"처럼 한 줄로 보여준다. 맞지 않는 값은 뺀다. */
function Summary({ session, market }: { session: DeadCrossSession; market: EnvironmentOption["market"] }) {
  const period = barPeriods(market).find((option) => option.value === session.bar_period);
  const parts = [
    period?.label,
    session.short_period !== null && validBarCount(session.short_period, SHORT_PERIOD_RANGE)
      ? `단기 ${session.short_period}봉`
      : null,
    session.long_period !== null && validBarCount(session.long_period, LONG_PERIOD_RANGE)
      ? `장기 ${session.long_period}봉`
      : null,
  ].filter(Boolean);
  if (parts.length === 0) return null;
  return <p className="mt-2 text-sm text-sub">{parts.join(" · ")}</p>;
}
