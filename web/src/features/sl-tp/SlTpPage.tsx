import { fetchSlTp, saveSlTp, type EnvironmentValue, type SlTpSession } from "../../api";
import { AutoTradingSettingsPage, FieldError, PercentInput } from "../auto-trading/AutoTradingSettingsPage";
import { validPercent } from "../auto-trading/rules";
import type { FeatureApi } from "../auto-trading/useAutoTradingSettings";
import { nextSession, sessionErrors, STOP_LOSS_RANGE, TAKE_PROFIT_RANGE } from "./rules";

const SL_TP: FeatureApi<SlTpSession> = { name: "SL / TP", fetch: fetchSlTp, save: saveSlTp };

export function SlTpPage({ environment }: { environment: EnvironmentValue }) {
  return (
    <AutoTradingSettingsPage
      environment={environment}
      feature={SL_TP}
      notice={[
        "평균 매입가 대비 수익률이 익절 기준 이상이거나 손절 기준 이하가 되면, 그 종목의 매도 가능 수량 전량을 시장가로 매도합니다. 제외종목은 매도하지 않습니다.",
        "이 화면을 닫아도 서버가 켜져 있으면 동작합니다. 서버와 키움의 연결이 끊긴 동안에는 판정하지 않습니다.",
      ]}
      sessionName="SL/TP 세션"
      idPrefix="sl-tp"
      nextSession={nextSession}
      sessionErrors={sessionErrors}
      renderDetails={({ session, n, error, disabled, update }) => {
        const ids = { takeProfit: `sl-tp-${n}-take-profit`, stopLoss: `sl-tp-${n}-stop-loss` };
        return (
          <>
            <div className="mt-2 grid grid-cols-2 gap-2">
              <div className="min-w-0">
                <PercentInput
                  name="익절"
                  sign="+"
                  label={`${n}번째 세션 익절 기준(%)`}
                  value={session.take_profit}
                  describedBy={error.takeProfit ? ids.takeProfit : undefined}
                  disabled={disabled}
                  onChange={(take_profit) => update({ take_profit })}
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
                  onChange={(stop_loss) => update({ stop_loss })}
                />
                <FieldError id={ids.stopLoss} message={error.stopLoss} />
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
