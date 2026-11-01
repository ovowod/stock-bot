import { fetchTrailingStop, saveTrailingStop, type EnvironmentValue, type TrailingStopSession } from "../../api";
import { AutoTradingSettingsPage, FieldError, PercentInput } from "../auto-trading/AutoTradingSettingsPage";
import { validPercent } from "../auto-trading/rules";
import type { FeatureApi } from "../auto-trading/useAutoTradingSettings";
import { ACTIVATION_PROFIT_RANGE, DROP_FROM_PEAK_RANGE, nextSession, sessionErrors } from "./rules";

const TRAILING_STOP: FeatureApi<TrailingStopSession> = {
  name: "Trailing Stop",
  fetch: fetchTrailingStop,
  save: saveTrailingStop,
};

/** Trailing Stop 설정 화면. 매도 동작은 아직 없고 설정만 저장한다. */
export function TrailingStopPage({ environment }: { environment: EnvironmentValue }) {
  return (
    <AutoTradingSettingsPage
      environment={environment}
      feature={TRAILING_STOP}
      notice={[
        "평균 매입가 대비 수익률이 활성화 수익률 이상이 되면 그때부터 고점을 따라가고, 시세 체결가가 고점 대비 하락률만큼 떨어지면 그 종목의 매도 가능 수량 전량을 시장가로 매도합니다. 제외종목은 매도하지 않습니다.",
        "Trailing Stop 자동 매도는 아직 동작하지 않습니다. 지금은 설정만 저장됩니다.",
      ]}
      sessionName="Trailing Stop 세션"
      idPrefix="trailing-stop"
      nextSession={nextSession}
      sessionErrors={sessionErrors}
      renderDetails={({ session, n, error, disabled, update }) => {
        const ids = { activation: `trailing-stop-${n}-activation`, drop: `trailing-stop-${n}-drop` };
        return (
          <>
            {/* 칸 이름이 길어 좁은 화면에서는 값 자리가 모자라므로 한 줄에 하나씩 둔다. */}
            <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
              <div className="min-w-0">
                <PercentInput
                  name="활성화 수익률"
                  sign="+"
                  label={`${n}번째 세션 활성화 수익률(%)`}
                  value={session.activation_profit}
                  describedBy={error.activationProfit ? ids.activation : undefined}
                  disabled={disabled}
                  onChange={(activation_profit) => update({ activation_profit })}
                />
                <FieldError id={ids.activation} message={error.activationProfit} />
              </div>
              <div className="min-w-0">
                <PercentInput
                  name="하락률"
                  sign="−"
                  label={`${n}번째 세션 고점 대비 하락률(%)`}
                  value={session.drop_from_peak}
                  describedBy={error.dropFromPeak ? ids.drop : undefined}
                  disabled={disabled}
                  onChange={(drop_from_peak) => update({ drop_from_peak })}
                />
                <FieldError id={ids.drop} message={error.dropFromPeak} />
              </div>
            </div>
            <FieldError message={error.criteria} />
            <Summary session={session} />
          </>
        );
      }}
    />
  );
}

/** 정한 값을 부호와 함께 한 줄로 보여준다. 고점 대비 하락률 3은 고점 대비 −3%다. */
function Summary({ session }: { session: TrailingStopSession }) {
  const parts = [
    session.activation_profit !== null && validPercent(session.activation_profit, ACTIVATION_PROFIT_RANGE)
      ? `+${session.activation_profit}%부터 고점을 따라감`
      : null,
    session.drop_from_peak !== null && validPercent(session.drop_from_peak, DROP_FROM_PEAK_RANGE)
      ? `고점 대비 −${session.drop_from_peak}%에서 매도`
      : null,
  ].filter(Boolean);
  if (parts.length === 0) return null;
  return <p className="mt-2 text-sm text-sub">{parts.join(" · ")}</p>;
}
