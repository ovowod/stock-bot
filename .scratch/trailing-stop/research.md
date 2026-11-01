# 다른 시스템의 Trailing Stop 동작

Trailing Stop 동작 결정(활성화, 고점 기억, 가격 기준, 주문 종류 등)에 참고할 다른 시스템의 공개 자료를 모은다. 2026-11-01에 확인했다. 키움 영웅문은 제외했다.

## 1. Interactive Brokers (IBKR)

- 출처: [IBKR Order Types – Trailing](https://www.interactivebrokers.com/en/trading/orders/trailing-stops.php) (주문 유형 전체 페이지로 열린다)
- 출처: [IBKR Campus – Trailing Stop 강의](https://www.interactivebrokers.com/campus/trading-lessons/trailing-stop/)
- 출처: [TWS API – Adjustable Stops](https://interactivebrokers.github.io/tws-api/adjustable_stops.html) (deprecated 표시가 있다)
- 출처: [TWS API – Trigger Methods](https://www.interactivebrokers.com/docs/tws-api/doc/orders/trigger-methods)
- 출처: [TWS 가이드 – Order Preset](https://ibkrguides.com/tws/usersguidebook/configuretws/modify%20the%20stop%20trigger%20method.htm)

원문:
- "As the market price rises, the stop price rises by the trail amount, but if the stock price falls, the stop loss price doesn't change, and a market order is submitted when the stop price is hit."
- "The trailing price is based on the last traded price at the time of the order and tracks it throughout the lifetime of the order." (Campus)
- "If you leave it blank the Stop or trigger price will automatically be set to the last traded price minus the trail amount." (Campus)
- Trailing Limit: "when the stop price is hit a limit order is submitted at the last calculated limit price."
- Adjustable Stop: "You can attach one-time adjustments to stop, stop limit, trailing stop and trailing stop limit orders. When you attach an adjusted order, you set a trigger price that triggers a modification of the original (or parent) order"
- "if you set up a Stop Limit, you can attach the one-time adjustment to change the order to a Trailing Stop" (주문 유형 페이지)
- Trigger Method: "2 – "Last" function, where stop orders are triggered based on the last price", 표에서 `STK`의 기본값은 Last.
- Outside RTH 설정: "If unchecked, trading hours are determined by the opening/closing times of the order destination, stop orders will not trigger outside of regular trading hours."

요약:
- 기본 Trailing Stop에는 활성화 조건이 없다. 주문을 넣는 순간부터 추적한다.
- 활성화가 필요하면 Adjustable Stop을 쓴다. 일반 Stop으로 두었다가 trigger price를 넘으면 한 번만 Trailing Stop으로 바뀐다.
- 고점은 주문이 살아 있는 동안 IBKR 서버가 기억한다. 주문 수명은 TIF(DAY/GTC)가 정한다.
- 주식은 기본적으로 마지막 체결가로 판단한다. Outside RTH를 켜지 않으면 정규장 밖에서는 trigger되지 않는다.
- trigger되면 시장가 주문을 보낸다. Trailing Limit은 지정가 주문을 보낸다.

## 2. Fidelity

- 출처: [Fidelity – Conditional Orders and Trailing Stop Orders (PDF)](https://www.fidelity.com/bin-public/060_www_fidelity_com/documents/Conditional_Orders.pdf) (© 2012)
- 출처: [Fidelity.com Help – Order Types and Conditions](https://www.fidelity.com/webcontent/ap002390-mlo-content/19.09/help/learn_order_types_conditions.shtml)

원문:
- "Trailing Stop Loss orders trigger a market order while Trailing stop Limit orders trigger a limit order."
- "Trailing Stop orders are monitored between 9:30 AM and 4:00 PM Eastern Standard Time and are held on a separate order file until triggered"
- "Orders can be triggered based on the security's last round lot trade of 100 shares or greater, Bid, or Ask"
- "For listed securities, the trigger is based off the last trade, regardless of whether it is a buy or a sell order." (Help)
- "You can enter trailing stop orders as either day or good 'til canceled." (Help)
- "During periods of market volatility, especially at market open and market close, Conditional Orders, as is the case with Trailing Stop orders, may trigger and/or execute at a price significantly different from your current stop price."
- GTC 기간: "GTC orders are good for 180 days or until cancelled" (같은 PDF의 OTO 항목)

요약:
- 활성화 조건은 없다.
- 감시는 정규장(9:30~16:00 ET)에서만 한다. 상장 종목은 마지막 체결가로 판단한다.
- 주문은 Fidelity 쪽 별도 파일에 보관하고, trigger되면 그때 시장에 보낸다. DAY나 GTC로 넣을 수 있다.
- 장 시작 직후처럼 변동이 클 때는 stop 가격과 크게 다른 가격에 체결될 수 있다고 경고한다.

## 3. Charles Schwab

- 출처: [Schwab – Setting Trailing Stops on thinkorswim desktop](https://www.schwab.com/learn/story/setting-trailing-stops-on-thinkorswim-desktop)
- 출처: [Schwab – Stop Orders: Mastering Order Types](https://www.schwab.com/learn/story/stop-orders-mastering-order-types) (일반 stop 주문 설명)

원문:
- "if the stock moves higher, so does the stop ... whereas if the price drops, the stop order remains set—and if the stock falls to the stop price, it triggers a market sell order"
- trail 기준 가격 선택(Link): "MARK is a common choice because it's the midpoint between the bid and ask prices", 스프레드가 넓은 종목은 "BID"
- "Trailing stops and other conditional orders are held on Schwab's servers and not routed until your order conditions have been met."
- Stop 주문 일반: "Stop orders will only trigger during the standard market session, 9:30 a.m. to 4 p.m. ET."
- "Good-'til-canceled (GTC) stop orders carry over to future standard sessions if they haven't been triggered. At Schwab, GTC orders remain active for up to 180 calendar days"
- Gaps: "Stop orders are vulnerable to pricing gaps, which can sometimes occur between trading sessions ... The execution price can be higher or lower than the stop's trigger price"

요약:
- 활성화 조건은 없다. 기준 가격은 MARK(bid/ask 중간), BID 등에서 고른다.
- 조건은 Schwab 서버가 갖고 있다. GTC면 다음 정규장으로 이어진다.
- trigger되면 시장가 주문을 보낸다. 장 사이 gap에는 stop 가격보다 낮게 체결될 수 있다.
- 정규장에서만 trigger된다는 문장은 일반 stop 주문 페이지에 있다. Trailing Stop 전용 문서(`trailing-stop-orders-mastering-order-types`)는 지금 목록 페이지로 넘어가 원문을 확인하지 못했다.

## 4. Binance USDⓈ-M Futures

- 출처: [Binance FAQ – What Is a Trailing Stop Order?](https://www.binance.com/en/support/faq/what-is-a-trailing-stop-order-360042299292) (Futures 메뉴의 문서)
- 출처: [Binance Developer Docs – USDⓈ-M Futures Trade REST API, New Algo Order](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Algo-Order) (`POST /fapi/v1/algoOrder`)
- 출처: [Binance Spot API – Trailing Stop FAQ](https://developers.binance.com/docs/binance-spot-api-docs/faqs/trailing-stop-faq) (Spot)

원문:
- 발동 조건(API): "SELL: the highest price after order placed >= `activatePrice`, and the latest price <= the highest price * (1 - `callbackRate`)"
- `activatePrice`: "default as the latest price(supporting different `workingType`)"
- `workingType`: `MARK_PRICE`, `CONTRACT_PRICE` 중 하나, 기본값 `CONTRACT_PRICE`. `callbackRate` 범위는 0.1~20.
- FAQ: "You can choose either "Last Price" or "Mark Price" as a trigger."
- FAQ: "This sell order will be executed at the next available market price"
- FAQ 예시 1: 활성화 가격 10,500, callback 5%, 고점 11,000 → 10,450에서 시장가 매도
- Spot FAQ: "If the `stopPrice` parameter is omitted then the order starts tracking price changes from the next trade."

요약:
- 활성화 가격(`activatePrice`)과 callback rate(%)로 동작한다. 활성화 가격을 비우면 현재가로 정해져 바로 추적한다.
- 고점은 "주문을 넣은 뒤의 최고가"다. 주문이 살아 있는 동안 거래소가 기억한다.
- 한 번 활성화되면 활성화 가격 아래로 내려가도 유지된다. 발동 조건에 "현재가 ≥ 활성화 가격"이 없고, 예시 1도 10,500보다 낮은 10,450에서 매도한다.
- 기준 가격은 마지막 체결가(`CONTRACT_PRICE`)와 mark price 중에서 고른다.
- trigger되면 시장가 주문을 보낸다. 24시간 시장이라 장 사이 gap 문제는 없다.

## 5. freqtrade (오픈소스 봇)

- 출처: [freqtrade docs – Stop Loss](https://www.freqtrade.io/en/stable/stoploss/)
- 출처: [freqtrade docs – Strategy Callbacks](https://www.freqtrade.io/en/stable/strategy-callbacks/), [Configuration](https://www.freqtrade.io/en/stable/configuration/)
- 출처: source code, `stable` 브랜치 commit `4cf0351e`
  - [`freqtrade/persistence/trade_model.py`](https://github.com/freqtrade/freqtrade/blob/stable/freqtrade/persistence/trade_model.py)
  - [`freqtrade/strategy/interface.py`](https://github.com/freqtrade/freqtrade/blob/stable/freqtrade/strategy/interface.py)
  - [`freqtrade/freqtradebot.py`](https://github.com/freqtrade/freqtrade/blob/stable/freqtrade/freqtradebot.py)

원문 (docs):
- "If `trailing_only_offset_is_reached = True` then the trailing stoploss is only activated once the offset is reached. Until then, the stoploss remains at the configured `stoploss` and is not trailing."
- 예시: 100$에 매수, offset 3%, positive 2% → 103$에서 stop 100.94$ → "now the asset drops in value to 101$, the stop loss will still be 100.94$"
- "The stoploss price can only ever move upwards"
- "Stoploss values cannot be changed if `trailing_stop` is enabled and the stoploss has already been adjusted."
- DCA: "Entry rates are calculated using weighted averages.", "Exits will not influence the average entry rate."

원문 (code):
- `interface.py`: `if not (self.trailing_only_offset_is_reached and bound_profit < sl_offset):` 일 때만 stop을 갱신한다.
- `trade_model.py`: `# stop losses only walk up, never down!`
- DB 컬럼: `stop_loss`, `initial_stop_loss`, `is_stop_loss_trailing`, `max_rate` ("absolute value of the highest reached price")
- `freqtradebot.py`: 시작할 때 `Trade.stoploss_reinitialization(...)`을 부르지만 `if not trade.is_stop_loss_trailing` 인 거래만 고친다.
- 청산 주문 종류: stop 계열 청산이면 `self.strategy.order_types["stoploss"]`를 쓴다. 기본값은 `"stoploss": "limit"`이다. 문서 예시는 `"stoploss": "market"`을 권한다.

요약:
- 활성화: `trailing_stop_positive_offset`(매수가 대비 수익률)과 `trailing_only_offset_is_reached=True`를 함께 쓴다.
- 고점을 따로 쓰지 않는다. 매 loop마다 "현재가 × (1 − trail%)"를 계산해 기존 stop보다 높을 때만 올린다. 결국 stop 가격 자체가 고점을 기억한다.
- `stop_loss`와 `max_rate`는 DB(SQLite 등)에 저장된다. 재시작해도 이어지고, 거래(trade)가 닫힐 때까지 유지된다.
- 실거래에서 현재가는 `exchange.get_rate(side="exit")`로 받는다. `exit_pricing` 설정에 따라 호가(bid/ask) 또는 ticker 가격이다. 캔들 고가/저가는 backtest에서만 쓴다.
- 수익률이 offset 아래로 내려가면 stop을 더 올리지 않는다. 이미 올라간 stop은 그대로 남는다.
- 설정을 바꿔도 이미 올라간 stop은 내려가지 않는다. 기본 `stoploss` 재설정은 아직 trail하지 않은 거래에만 적용된다.
- trail%를 바꾸면 다음 loop부터 현재가로 다시 계산한다. 그 값이 기존 stop보다 높을 때만 반영된다. 이 동작은 코드를 읽고 추론했다.
- 추가 매수(DCA) 뒤에는 매수가를 가중평균으로 다시 계산하고 stop도 다시 계산한다. 이때도 stop은 내려가지 않는다. 부분 매도는 평균 매수가를 바꾸지 않는다.

## 6. 미래에셋증권 HTS 주식 스탑로스 [0600]

- 출처: [미래에셋증권 HTS 도움말 – [0600] 주식 스탑로스](https://securities.miraeasset.com/kairos/0600.htm)
- 한국투자증권·삼성증권·NH투자증권은 Trailing Stop 공식 안내 페이지를 찾지 못했다.

원문:
- "트레일링스탑 조건은 이익실현 조건 지정 이후에 설정이 가능합니다."
- "이익실현가격을 갱신했을 경우 (고점 갱신) 그 만큼 STOP 가격도 갱신되는 점이 이익확보와 다른 부분입니다."
- 예시: 1,000원 매수, 이익실현 10%, TS 비율 70% → "일단 가격이 1100원 이상으로 상승 후, 1070원 이하로 재 하락할 경우만 매도주문이 나가게 되고", 1,200원까지 오르면 stop은 1,140원
- 이익확보 예시: "조건 설정후 가격이 1090원까지만 상승하고 하락한다면 1070원 이하로 가격이 떨어져도 조건이 실행되지 않습니다."
- "다른 주문화면에서 매수 매도시, 기존에 실행 시켜 놓았던 스탑로스 주문은 실행이 종료되며, 다시 스탑로스 주문을 설정하여야합니다."
- "잔고 변경시 스탑로스 감지가 해지 되므로"
- 주문 종류: "보통, 시장가, 최유리, 최우선, IOC(...) FOK(...)", "매도주문 전송 n 초후 시장가정정/전량취소 주문 전송 설정이 가능합니다."
- "HTS 실행시 스탑로스를 수동 실행하거나 자동으로 실행할 수 있습니다."

요약:
- 활성화 조건은 이익실현률(평균매입가 대비)이다. 이 가격에 닿기 전에는 추적하지 않는다.
- TS 비율은 "고점 대비 하락 %"가 아니라 "이익 중 지킬 비율"이다. 틱 하락분으로도 정할 수 있다.
- 활성화 뒤 활성화 가격 아래로 내려가도 유지된다. 예시에서 1,100원 활성화 뒤 1,070원에서 매도한다.
- 매수·매도로 잔고가 바뀌면 감시가 해지되어 다시 설정해야 한다.
- 주문 종류(시장가·지정가 등)는 사용자가 고른다. 미체결이면 n초 뒤 시장가로 정정할 수 있다.
- HTS를 켤 때 실행하는 방식이다. 서버에서 감시하는지, 고점이 다음 날까지 이어지는지는 적혀 있지 않다.

## 공통점과 차이

| 질문 | IBKR | Fidelity | Schwab | Binance Futures | freqtrade | 미래에셋 HTS |
|---|---|---|---|---|---|---|
| 1. 활성화 조건 | 없음. Adjustable Stop으로 가능 | 없음 | 없음 | `activatePrice`. 비우면 현재가 | `offset` + `only_offset_is_reached` (매수가 대비 %) | 이익실현률 (평균매입가 대비 %) |
| 2. 고점 기억 기간 | 주문 수명(DAY/GTC), 서버 | 주문 수명(DAY/GTC 180일), 서버 | GTC면 다음 정규장으로 이어짐(180일), 서버 | 주문 수명, 거래소 | 거래 종료까지, DB에 저장(재시작 유지) | 잔고가 바뀌면 해지. 날짜를 넘기는지는 미확인 |
| 3. 고점 갱신 가격 | 주식 기본 Last. 정규장 밖에선 trigger 안 됨(Outside RTH 끔) | 정규장 9:30~16:00 ET, last trade(bid/ask 선택 가능) | MARK/BID 등 선택. 정규장에서만 trigger(stop 일반) | Last 또는 Mark 선택 | 실거래는 호가/ticker(`exit_pricing`) | 현재가 |
| 4. 활성화 가격 아래로 되돌아오면 | 해당 없음(Adjust는 한 번) | 해당 없음 | 해당 없음 | 활성 유지 | 올라간 stop 유지, 더 올리지 않음 | 활성 유지 |
| 5. 장 사이 gap | 미확인(시장가라 아래에서 체결될 수 있음) | 장 시작 때 stop과 크게 다를 수 있다고 경고 | gap에 취약, 체결가가 stop보다 낮을 수 있음 | 해당 없음(24시간) | 해당 없음(코인) | 미확인 |
| 6. trail % 변경 | 미확인 | 미확인 | 미확인 | 미확인 | stop은 내려가지 않음. 현재가로 다시 계산해 높을 때만 반영 | 미확인 |
| 7. 부분 매도·추가 매수 | 미확인 | 미확인 | 미확인 | 미확인 | 매수가 가중평균 다시 계산. stop은 내려가지 않음 | 잔고가 바뀌면 감시 해지, 다시 설정 |
| 8. 발동 시 주문 | 시장가(Trailing Limit은 지정가) | 시장가(Trailing Limit은 지정가) | 시장가 | 시장가 | `order_types["stoploss"]`(기본 limit, 문서는 market 권장) | 사용자 선택, n초 뒤 시장가 정정 가능 |

- 증권사 서버형(IBKR·Fidelity·Schwab)은 활성화 조건 없이 바로 추적하고, 정규장에서만 trigger하며, trigger되면 시장가로 보낸다.
- 활성화 조건이 있는 곳(Binance·freqtrade·미래에셋)은 모두 한 번 활성화되면 되돌아와도 유지한다.
- 고점은 대개 주문이나 거래가 끝날 때까지 서버나 DB가 기억한다. 날짜를 넘겨도 이어진다(GTC).

## 확인하지 못한 것

- Schwab Trailing Stop 전용 문서 원문. URL이 목록 페이지로 넘어간다. 정규장 한정 규칙은 일반 stop 주문 페이지에서만 확인했다.
- IBKR·Fidelity·Schwab: 정규장 밖 가격으로 고점을 갱신하는지. trigger가 정규장에만 된다는 것만 확인했다.
- IBKR·Fidelity·Schwab: 열려 있는 주문의 trail 값을 바꿀 때 기존 고점을 유지하는지.
- IBKR·Fidelity·Schwab: 수량이 바뀔 때(부분 체결·추가 매수) 어떻게 되는지.
- 다음 날 시가가 stop보다 낮게 시작할 때의 동작을 trailing stop 전용으로 설명한 문서. Schwab의 gap 설명은 일반 stop 주문 기준이다.
- 미래에셋 [0600]: 고점이 날짜를 넘겨 유지되는지, 서버에서 감시하는지.
- 한국투자증권·삼성증권·NH투자증권의 공식 Trailing Stop 안내 페이지. 찾지 못했다.
- freqtrade: trail% 변경 시 동작은 문서에 없다. 코드(`adjust_stop_loss`)를 읽고 추론했다.
