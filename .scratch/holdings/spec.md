# 보유종목 읽기와 미국 거래소 판정 모으기

Status: ready-for-agent

## Problem Statement

같은 잔고 TR(국내 `kt00018`, 미국 `ust21070`)을 `account.py` 안의 세 곳이 각자 부르고 각자 해석한다.
- 계좌 화면(`_domestic`, `_us`)
- 매도 직전 확인(`holding`, `fresh_holding`이 쓰는 `_balance`와 `_holding`)
- SL / TP 대상(`positions`)

미국 거래소를 정하는 표도 5벌이고, 거래소를 모를 때의 정책이 곳마다 다르다.
- 표: `quote.US_EXCHANGE_CODES`, `ranking.US_EXCHANGE_NAMES`, `stock_search.US_EXCHANGE_NAMES`(앞의 것과 같음), `account.US_EXCHANGE_NAMES`(잔고 TR의 한글명), `sl_tp.US_EXCHANGES`
- 미체결 취소와 SL / TP는 종목 목록에서 거래소를 찾지 못하면 null로 두고 막는다.
- 계좌 화면은 잔고 TR의 한글 거래소명(뉴욕·나스닥·아멕스)으로 채우고, 매도 패널도 그 값으로 주문을 보낸다. 잔고 TR은 NYSE Arca ETF(SPY, EWZ 등)를 "아멕스"로 주지만, 모의투자에서 매도가 접수된 거래소는 종목 목록 기준인 NY였다. 그래서 종목 목록을 받지 못하면 이런 종목이 AMEX(NA)로 나간다.

## Solution

- 미국 거래소 표를 `us_exchange.py` 하나로 모은다. 한글명 표는 지운다.
- "주문·취소·SL / TP에 쓰는 미국 거래소는 종목 목록 기준이고, 없으면 null"을 계좌 화면에도 적용한다. 거래소가 null인 보유종목은 "거래소 확인 불가"로 보이고 매도 버튼이 없다.
- 잔고 TR 호출과 한 줄 해석을 새 module `holdings.py`로 모은다. 화면·매도 확인·SL / TP는 그 결과 위에서 각자 엄격함만 달리한다.
- 언제 몇 번 잔고 TR을 부르는지는 바꾸지 않는다.

## User Stories

1. As a 투자자, I want 종목 목록에서 거래소를 찾지 못한 미국 보유종목에 "거래소 확인 불가"가 보이고 매도 버튼이 없기를, so that 잘못된 거래소로 매도가 나가지 않는다.
2. As a 투자자, I want 종목 목록을 받지 못해도 미국 계좌 확인은 보이기를, so that 목록 문제로 계좌를 못 보는 일이 없다.
3. As a 투자자, I want 계좌 화면, 매도, 미체결 취소, SL / TP가 같은 종목에 같은 거래소를 쓰기를, so that 화면마다 판단이 다르지 않다.
4. As a 투자자, I want 화면에만 쓰는 칸 하나가 키움 응답에서 빠져도 자동 매도와 SL / TP가 멈추지 않기를, so that 보유종목 판단에 필요 없는 칸 때문에 자동 매도가 실패하지 않는다.
5. As a 투자자, I want 화면에만 쓰는 칸이 빠지면 계좌 화면이 그 칸만 비워 보여 주기를, so that 칸 하나 때문에 계좌 전체를 못 보지 않는다.
6. As a 투자자, I want 그 밖의 계좌 화면, 매도, SL / TP 동작은 지금과 같기를, so that 구조를 바꾸는 작업이 기능을 바꾸지 않는다.
7. As a 개발자, I want 잔고 TR을 부르고 한 줄을 해석하는 곳이 시장마다 한 곳이기를, so that 응답 형식이 바뀌면 한 곳만 고친다.
8. As a 개발자, I want 미국 거래소 이름과 코드가 한 곳에 있기를, so that 표끼리 어긋나지 않는다.

## Implementation Decisions

**거래소 표 (`us_exchange.py`)**

- 새 module `us_exchange.py`에 둔다.
  - `CODES = {"NYSE": "NY", "NASDAQ": "ND", "AMEX": "NA"}`: 화면 이름 -> 키움 `stex_tp`
  - `NAMES`: `CODES`를 뒤집은 코드 -> 이름
  - `EXCHANGES`: 허용하는 이름 목록(`tuple(CODES)`)
- 쓰는 곳을 바꾼다.
  - `quote`, `order`, `sl_tp_watch`: `quote.US_EXCHANGE_CODES` -> `us_exchange.CODES`
  - `ranking`, `stock_search`: 각자의 `US_EXCHANGE_NAMES` -> `us_exchange.NAMES`. 순위가 모르는 코드를 받은 그대로 보여 주는 동작은 둔다.
  - `sl_tp`: `US_EXCHANGES` -> `us_exchange.EXCHANGES`
- 순위 화면의 거래소 필터 코드(`ranking.EXCHANGES`의 1/2/3)는 순위 TR만 쓰는 다른 번호 체계라 그대로 둔다.
- `account.US_EXCHANGE_NAMES`(한글명 표)는 지운다.

**보유종목 읽기 (`holdings.py`)**

- 새 module `holdings.py`로 옮긴다.
  - `DOMESTIC_ACCOUNT_PATH`, `US_ACCOUNT_PATH`, `StockListings`, `_HoldingReader`, `_strip_prefix`
  - `account.py`, `open_order.py`, `fill_watch.py`는 여기서 가져온다.
- 한 줄 모양 `Holding`(frozen dataclass). 모든 쓰는 곳이 필요로 하는 칸을 다 넣는다.
  - 공통: `code`, `name`, `quantity`, `sellable_quantity`, `purchase_price`, `current_price`, `purchase_amount`, `evaluation_amount`, `profit_loss`, `return_rate`
  - 국내: `credit`(`crd_tp != "00"`), `weight`. `exchange`는 None이다.
  - 미국: `exchange`(종목 목록 기준, 없으면 None), `currency`, `evaluation_amount_krw`, `profit_loss_krw`. `credit`은 False다.
  - 숫자는 `Decimal | None`로 둔다. 화면이 지금처럼 정수·실수로 바꾼다.
- 칸이 빠졌을 때(Q10):
  - 꼭 필요한 칸이 빠지면 응답 형식 오류다. 종목코드(`stk_cd`), 수량(국내 `rmnd_qty`, 미국 `poss_qty`), 매도 가능 수량(국내 `trde_able_qty`, 미국 `sell_alowq`), 국내 신용구분(`crd_tp`)이 그렇다.
  - 나머지 칸이 빠지면 None이다.
  - 이름은 늘 값이 있다. 국내는 `stk_nm` -> 종목코드, 미국은 종목 목록 이름 -> `frgn_stk_nm` -> 종목코드 순서로 쓴다. 화면이 이름을 글자로 가정하기 때문이다.
  - 미국 `currency`(`crnc_code`)는 빠지면 None이다. 화면은 이 칸을 쓰지 않는다.
  - 숫자가 깨진 값이면 None으로 두고 `holding_value_unreadable`을 남긴다(지금 `_HoldingReader`와 같다).
- 결과 묶음 `HoldingsSnapshot`(frozen dataclass)
  - `rows: list[Holding]`
  - `listings_ok: bool`: 미국에서 종목 목록을 받았는지. 국내와 목록을 읽지 않은 조회는 True다.
  - `response: dict`: 원래 잔고 응답. 계좌 요약은 화면이 여기서 지금처럼 직접 읽는다(Q11). 요약 칸이 빠지면 계좌 화면만 실패한다.
- `Holdings` 서비스(`kiwoom`, `listings`를 받는다)
  - `read(spec, *, patient=False, with_listings=True) -> HoldingsSnapshot`: 잔고 TR을 부르는 유일한 곳이다. 미국에서 `with_listings`면 종목 목록을 함께 받는다. 목록 실패는 `stock_list_unavailable` WARNING을 남기고 `listings_ok=False`로 둔다(지금 `_stock_listings`와 같다).
  - `holding(spec, code)`: 매도 직전 확인. 목록 없이 읽는다.
  - `fresh_holding(spec, code)`: 자동 매도용. 지금의 묶음 읽기 장치(`_next_balance` 등)를 그대로 옮긴다. 묶음이 나눠 쓰는 값은 원래 응답 대신 `HoldingsSnapshot`이다. `patient=True`, 목록 없이 읽는다.
  - `positions(spec)`: SL / TP 대상. 돌려주는 dict 모양(`code`, `name`, `purchase_price`, `credit`, `exchange`)은 그대로다.
- 매도 확인의 엄격함(지금과 같다)
  - 국내는 `credit`이 False인 줄만 센다.
  - 같은 종목이 여러 줄이면 `unsupported_holding`(502)이다.
  - 수량이나 매도 가능 수량이 None이면 응답 형식 오류다. 지금은 깨진 숫자일 때 "숫자가 아닙니다"였는데, 이제는 "값이 비어 있습니다"와 같은 오류로 묶인다. 오류 종류(`response_format_error`)는 같다.
  - 로그 `holding_checked`는 그대로다.
- SL / TP 대상의 엄격함
  - 미국에서 `listings`가 없거나 `listings_ok`가 False면 `stock_list_unavailable`(503)이다. 지금은 목록 조회의 원래 오류가 그대로 올라갔다. SL / TP는 어느 쪽이든 읽기 실패로 다시 시도하므로 동작은 같다.
  - 신용, 매입가 없음, 거래소 모름, 중복 처리는 `sl_tp_watch`에 그대로 둔다.
  - 로그 `positions_fetched`는 그대로다.
- 부르는 횟수는 바꾸지 않는다(Q6).
  - 계좌 화면: 국내 `kt00018`+`kt00001`, 미국 `ust21070`+`ust21110`+종목 목록을 지금처럼 함께 보낸다.
  - 매도 확인과 자동 매도: 목록 없이 잔고만 읽는다.
  - 자동 매도의 묶음 읽기만 여러 요청이 한 조회를 나눠 쓴다.
- `AccountService`는 계좌 화면만 맡는다. 계좌번호 확인(`ka00001`), 예수금, 요약을 읽고, 보유종목은 `Holdings.read`의 결과를 화면 모양으로 바꾼다.
  - 국내 화면은 신용 잔고 줄도 지금처럼 보여 주고 매도 버튼도 둔다(Q3, 매도 spec의 결정).
- `app.py`: `Holdings`를 만들어 `OrderService`(`holding`, `fresh_holding`), `SlTpWatcher`(`positions`), `AccountService`에 넘긴다. `/holdings/{code}`는 `Holdings.holding`을 부른다.

**거래소를 모르는 미국 보유종목**

- 계좌 화면 응답의 `exchange`는 종목 목록의 거래소이고, 없으면 `null`이다. 종목 목록을 받지 못하면 모든 줄이 `null`이다. 화면 응답은 200이다.
- 화면(`AccountPage.tsx`)
  - `exchange`가 `null`이면 거래소 자리에 "거래소 확인 불가"를 미체결 배지와 같은 모양으로 보여 준다.
  - 그 줄에는 매도 버튼을 두지 않는다(`onSell` 없음).
  - 목록 실패를 따로 알리는 문구는 두지 않는다. 미체결 패널과 같다.
- `api.ts`의 미국 보유종목 `exchange`와 `currency` 타입을 `string | null`로 바꾼다.

## Testing Decisions

- **좋은 테스트는 바깥에서 보이는 결과만 본다.** 가짜 키움 서버에 나간 요청, HTTP 응답, 로그 이벤트가 그 결과다. 테스트에서 module 안쪽 함수를 patch하지 않는다.
- **동작이 그대로인지**: 계좌 화면, 매도 확인(`test_holdings.py`, `test_sell_orders.py`), 자동 매도(`test_auto_sell.py`, `test_sl_tp_auto_sell.py`), SL / TP(`test_sl_tp_*.py`), 순위·검색·시세·주문 테스트는 기대값을 바꾸지 않고 통과해야 한다. import 위치만 고친다.
- **고치는 기존 테스트**(`test_us_account.py`):
  - "잔고 TR의 한글 거래소명을 영문으로 바꾼다" -> 지운다.
  - "목록에 없는 종목은 잔고 TR의 거래소를 쓴다" -> 이름은 `frgn_stk_nm`, 거래소는 `null`이다.
- **새 테스트**
  - 미국 계좌 화면: 종목 목록 조회가 실패하면 200이고 모든 보유종목의 `exchange`가 `null`이며 `stock_list_unavailable` 로그가 남는다.
  - 화면 전용 칸(예: 국내 `poss_rt`, 미국 `pl_amt_krw`)이 빠지면: 계좌 화면은 200이고 그 칸이 `null`이다. 매도 확인과 SL / TP 대상 읽기는 성공한다.
  - 꼭 필요한 칸(예: 수량)이 빠지면: 계좌 화면, 매도 확인, SL / TP 대상 읽기 모두 응답 형식 오류다.
  - 국내 `crd_tp`가 빠지면 계좌 화면도 응답 형식 오류다(새 동작).
  - 이름: 국내 `stk_nm`이 빠지거나, 미국에서 종목 목록 이름과 `frgn_stk_nm`이 모두 없으면 계좌 화면의 이름이 종목코드다.
  - 미국 SL / TP 대상: 종목 목록 조회가 실패하면 `stock_list_unavailable`이다.
  - 자동 매도 묶음 읽기는 `test_sl_tp_burst.py`가 이미 확인하므로 그대로 둔다.
- **화면 e2e**
  - 미국 보유종목의 `exchange`가 `null`이면 "거래소 확인 불가"가 보이고 매도 버튼이 없다.
  - 거래소가 있는 줄은 지금처럼 매도 패널이 열린다.
- **모의투자 확인은 하지 않는다.** 종목 목록 실패는 모의 서버에서 일부러 만들 수 없고, 그 밖에 키움에 나가는 요청은 바뀌지 않는다.
- 전체 테스트, lint, format 검사, type check, 화면 build·e2e를 돌린다.

## Out of Scope

- 국내 신용 잔고 줄의 매도 버튼 숨기기(Q3). 매도 spec에서 "계좌 화면은 바꾸지 않는다"로 정했다.
- 잔고 조회를 나눠 쓰는 범위 넓히기(Q6). 화면·수동 매도·SL / TP는 지금처럼 각자 읽는다.
- 계좌 요약 칸 정리(Q11).
- 순위 거래소 필터 코드(`ranking.EXCHANGES`).
- 서버에서 미국 주문의 거래소가 종목 목록과 맞는지 확인하는 일. 매수와 같이 화면이 보낸 거래소를 믿는다.
- 국내 NXT·SOR 거래소.
- `app.py` 정리와 폴더 정리(아키텍처 점검 06).

## Further Notes

- 아키텍처 점검(2026-10-27)의 후보 04다. 05(PR #17) 다음, 06 앞에 한다.
- 쉽게 되돌릴 수 있는 결정이라 ADR은 쓰지 않는다.
- 작업 순서: branch `refactor/holdings` 하나에 커밋 세 개를 두고 PR 하나로 올린다.
  1. 거래소 표를 `us_exchange.py`로 모으기. 동작 변화 없음.
  2. `holdings.py`로 보유종목 읽기 모으기. 동작 변화는 칸이 빠졌을 때(Q10)와 국내 화면의 `crd_tp` 필수뿐이다.
  3. 거래소를 모르는 미국 보유종목은 `null`, 화면에 "거래소 확인 불가", 매도 버튼 없음. 한글명 표를 지운다.
- 결정 기록(grilling, 2026-10-28)
  - Q1 범위: 거래소 판정과 보유종목 읽기 통합까지
  - Q2 목록에 없는 미국 보유종목: 미체결 취소와 같이 null, 매도 막기
  - Q3 국내 신용 잔고 매도 버튼: 그대로
  - Q4 한 줄 모양: 모든 쓰는 곳의 칸을 다 넣고, 엄격함은 쓰는 곳이 판단
  - Q5 종목 목록 실패: 결과에 `listings_ok`를 담고 화면은 계속, SL / TP는 오류
  - Q6 조회 나눠 쓰기: 지금 범위 그대로
  - Q7 거래소 표 위치: 새 module `us_exchange.py`
  - Q8 화면: 미체결과 같은 "거래소 확인 불가" 배지, 매도 버튼 없음
  - Q9 위치: 새 module `holdings.py`, `AccountService`는 화면만
  - Q10 빠진 칸: 꼭 필요한 칸만 오류, 나머지는 None
  - Q11 계좌 요약: 원래 응답을 넘겨 화면이 지금처럼 읽음
  - Q12 진행: spec 하나, 티켓 없음, 커밋 세 개, PR 하나

## Review Log

### 2026-10-28: Codex review
- ✏️ 이름이 비면 계좌 화면이 깨짐: 화면에 null 처리를 넣는 대신 서버가 이름을 늘 채운다(목록 이름 -> 잔고 TR 이름 -> 종목코드). 화면을 고치지 않아도 되고 API 테스트로 확인된다. 미국 `currency`는 None일 수 있다고 적고 `api.ts` 타입을 바로잡는다.
- ✏️ 거래소를 모르는 종목의 매도를 서버가 막지 않음: 서버 검사는 더하지 않고 범위 밖에 적었다. 매수도 화면이 보낸 거래소를 믿고, 화면으로는 그 종목의 매도 패널을 열 수 없으며, 실전 주문은 이미 막혀 있다. 검사하려면 매도 경로에 종목 목록 조회가 더해져 Q6과도 부딪친다.
