# 주문 허용 스위치

Status: ready-for-agent

## Problem Statement

실전투자에서 주문이 나가지 않게 막는 검사가 여러 곳에 흩어져 있다. 모두 `spec.is_real`을 직접 본다.
- `order.py`: 수동 주문(`place`), 취소(`cancel`), 자동 매도(`sell_all_at_market`)
- `sl_tp_watch.py`: 자동 매도를 보내기 직전(`_may_send`)
- `sl_tp.py`: 실전에서 SL / TP를 켜 두지 못하게 하는 규칙(`can_enable`)
- `open_order.py`: 미체결 화면의 취소 불가 표시(`blocked_reason="real"`)
- 화면: 주문 패널과 미체결의 취소 버튼이 `environments.ts`에 적힌 `isReal`을 본다.

키움에 요청을 보내는 `KiwoomClient`에는 아무 검사가 없다. 새 주문 기능에서 검사 하나를 빠뜨리면 실전 주문이 그대로 나간다. 또 나중에 실전 주문을 열려면 이 모든 곳을 찾아 고쳐야 한다.

## Solution

"이 투자 환경에서 키움에 주문을 보내도 되는가"를 **주문 허용** 스위치 하나로 정의한다.

- 스위치는 `config.py`의 환경 표에 둔다. 실전 두 환경은 `False`, 모의 두 환경은 `True`로 고정한다.
- 실제로 막는 곳은 `KiwoomClient`의 관문 하나다. 스위치가 꺼진 환경에서 돈이 움직이는 TR은 키움에 보내지 않는다.
- 보내기를 막던 위쪽 검사 4곳은 지우고 관문에 맡긴다.
- 화면 규칙(SL / TP 켜기, 미체결 취소 표시, 주문 패널, 미체결 취소 버튼)은 `is_real` 대신 스위치를 본다. 화면은 스위치를 서버에서 받는다.
- `is_real`은 "실전 환경인가"라는 사실로만 남는다(실전 배지, 색, 호출 한도, 실시간 주소).

사용자가 화면에서 보는 동작은 바뀌지 않는다.

## User Stories

1. As a 투자자, I want 어떤 코드 경로로 오든 실전에서 돈이 움직이는 요청이 키움에 나가지 않기를, so that 새 기능에서 검사를 빠뜨려도 실전 계좌가 움직이지 않는다.
2. As a 투자자, I want 주문·정정·취소뿐 아니라 신용주문, 금현물, 미국 예약주문, 환전 신청도 막히기를, so that 이 앱이 쓰지 않는 TR도 실수로 나가지 않는다.
3. As a 투자자, I want 실전에서도 조회는 지금처럼 되기를, so that 계좌 확인·미체결·순위·검색을 그대로 쓴다.
4. As a 투자자, I want 모의투자의 주문·취소·자동 매도는 지금처럼 되기를, so that 개발과 확인을 계속할 수 있다.
5. As a 투자자, I want 실전 화면의 주문 패널, 미체결, SL / TP가 지금처럼 "할 수 없음"을 보여주기를, so that 눌러도 실패하는 버튼이 없다.
6. As a 투자자, I want 나중에 실전 주문을 열 때 스위치 한 곳만 바꾸면 되기를, so that 빠뜨린 곳 때문에 일부만 열리는 일이 없다.
7. As a 투자자, I want 관문에 막힌 요청이 로그에 남기를, so that 어느 TR이 어느 환경에서 막혔는지 알 수 있다.

## Implementation Decisions

**스위치**

- `EnvironmentSpec`에 `orders_allowed: bool`을 더한다. 국내 실전·미국 실전은 `False`, 국내 모의·미국 모의는 `True`다.
- 지금은 코드 값으로 고정한다. 실전을 열 때 `.env`에서 읽도록 바꾼다. 그때 정확히 `true`일 때만 켜고, 서버 시작 때 상태를 로그로 남기고, `.env.example`에 더한다.
- 수동 주문과 SL / TP 자동 매도는 같은 스위치를 쓴다. 스위치가 켜져도 SL / TP는 사용자가 세션을 켜야 동작한다.

**관문**

- `kiwoom.py`에 `MONEY_MOVING_TR_IDS`를 둔다. 키움 문서(kra-docs)에서 돈이나 주문 상태를 바꾸는 TR 20개다.
  - 국내 주문 `/api/dostk/ordr`: `kt10000` 매수, `kt10001` 매도, `kt10002` 정정, `kt10003` 취소, `kt50000`~`kt50003` 금현물 매수·매도·정정·취소
  - 국내 신용주문 `/api/dostk/crdordr`: `kt10006`~`kt10009` 신용 매수·매도·정정·취소
  - 미국 주문 `/api/us/ordr`: `ust20000`~`ust20003` 매수·매도·정정·취소, `ust21200`·`ust21201` 예약 매수·매도, `ust21203` 예약 취소
  - 미국 환전 `/api/us/exchange`: `ust31302` 환전 신청
- 주소로 막지 않는다. `/api/us/ordr`에 조회 TR(`ust21204`, `ust21205`, `ust31490`)이, `/api/us/exchange`에 조회 TR(`ust31300`, `ust31301`)이 섞여 있다.
- 호출 한도에 쓰는 `ORDER_TR_IDS`(6개)와 따로 둔다. 호출 한도에서 환전은 주문과 다른 묶음이라 합치면 뜻이 섞인다.
- 관문은 `_post` 맨 앞, 호출 한도 대기보다 먼저 둔다. `call`, `call_first_page`, `call_once`가 모두 지나는 곳이다.
  - 접근 토큰은 관문보다 먼저 발급될 수 있다. 토큰 발급은 조회에도 쓰는 요청이라 받아들인다.
- 막히면 WARNING `kiwoom_order_blocked`(`api_id`, `target`)를 남기고 `AppError("order_not_allowed", "실전투자에서는 주문할 수 없습니다.", 403, {"api_id": ...})`를 낸다.
- `_call_order`는 고치지 않는다. 연결 오류와 응답 형식 오류만 "결과 모름"으로 바꾸므로 관문 오류는 403 그대로 나간다. `order_rejected` 로그가 한 줄 더 남는다.

**지우는 검사**

- `OrderService.place`의 실전 검사와 `order_blocked_real` 로그
- `OrderService.cancel`의 실전 검사와 `cancel_blocked_real` 로그
- `OrderService.sell_all_at_market`의 실전 검사
- `sl_tp_watch`의 `_may_send`에서 `real_trading` 이유

지운 뒤 실전에서 API를 직접 부르면 순서가 바뀐다.
- 입력이 틀리면 400(입력 오류)이 먼저 나온다.
- 주문 키는 관문에 막혀도 쓴 것으로 남는다.
- 매도는 잔고 재확인(조회)을 거친 뒤 관문에서 막힌다.
- 취소는 미체결 재확인(조회)에서 400 `cancel_not_supported`(이유 `real`)로 끝나 관문까지 가지 않는다. 미체결의 `blocked_reason="real"`도 같은 스위치를 읽으므로, 스위치를 켜면 함께 풀린다.
- 실전 자동 매도는 SL / TP가 실전을 지켜보지 않아 일어나지 않는다. 일어나도 관문에 막혀 "매도 실패"로 끝난다.

**스위치를 읽는 규칙**

- `sl_tp_watch.py`: 지켜볼 투자 환경은 국내·미국 모의로 고정한 `WATCHED` 대신 주문 허용이 켜진 환경이다. 스위치를 켜면 실전 SL / TP도 동작한다.
  - 국내 실전과 미국 실전은 실시간 연결 하나를 함께 쓰고, 두 SL / TP 모두 시세 그룹 번호 `2`를 쓴다. SL / TP는 켜진 세션 동안만 시세를 등록하고 두 시장의 정규장은 겹치지 않으므로, 두 등록이 동시에 있지 않다.
- `sl_tp.py`: `can_enable`은 `spec.orders_allowed`다. 저장·읽기에서 켜진 세션을 거부하는 조건도 `not spec.orders_allowed`다.
- `open_order.py`: 국내·미국 모두 `not spec.orders_allowed`면 `blocked_reason="real"`이다. 이유 코드는 그대로 둔다. 스위치가 꺼질 수 있는 환경은 실전뿐이다.
- `/api/environments`에 `orders_allowed`를 더한다.

**화면**

- 로그인 뒤 `/api/environments`를 한 번 불러 환경별 `orders_allowed`를 받는다. 라벨·시장·`isReal`은 `environments.ts`에 그대로 둔다.
- 받기 전이나 받기에 실패했을 때는 주문 불가로 둔다.
  - 받는 중: 주문 버튼과 취소 버튼을 비활성으로 둔다.
  - 실패: "주문 가능 여부를 불러오지 못했습니다. 새로고침하세요."를 보여준다.
  - 꺼짐: 지금 문구("실전투자에서는 주문할 수 없습니다.", "실전투자에서는 주문 취소를 할 수 없습니다.")를 그대로 보여준다.
- 주문 패널(`OrderPanel.tsx`)과 미체결(`OpenOrders.tsx`)의 차단 조건을 `isReal`에서 스위치로 바꾼다.
- 실전 배지(`Sheet.tsx`), 환경 색(`App.tsx`), 미국 거래소 안내 문구(`order.ts`)는 `isReal`을 그대로 쓴다.
- SL / TP 화면은 이미 서버의 `can_enable`을 보므로 고치지 않는다.

## Testing Decisions

- **좋은 테스트는 바깥에서 보이는 결과만 본다.** 가짜 키움 서버에 나간 요청, HTTP 응답, 로그 이벤트가 그 결과다.
- **관문 테스트(새로)**: 진짜 `KiwoomClient`에 가짜 키움 서버를 붙인다.
  - 실전 두 환경에서 `MONEY_MOVING_TR_IDS`의 TR은 모두 가짜 서버에 닿지 않고 `order_not_allowed`가 난다.
  - 모의 두 환경에서 같은 TR은 나간다.
  - 실전에서 조회 TR(`/api/us/ordr`의 조회 TR 포함)은 나간다.
  - `call`, `call_first_page`, `call_once` 세 경로 모두 막힌다.
  - 막힐 때 `kiwoom_order_blocked` 로그가 남는다.
- **고치는 기존 테스트**:
  - 주문(`test_orders.py`): "입력이 틀려도 실전 차단이 먼저다" → "실전에서 바른 주문은 403이고 주문 TR이 나가지 않는다". 로그 기대값을 `kiwoom_order_blocked`로 바꾼다.
  - 취소(`test_cancel_orders.py`): 실전에서 바른 취소는 400 `cancel_not_supported`이고 취소 TR이 나가지 않는다. 미체결 재확인 응답을 가짜 서버에 넣는다.
  - 자동 매도(`test_sl_tp_auto_sell.py`): 실전 자동 매도는 `rejected`로 끝나고 주문 TR이 나가지 않는다.
  - 환경 목록(`test_environments.py`): `orders_allowed`를 더한다.
  - SL / TP 설정, 미체결 테스트: 기대값은 그대로다(스위치가 `is_real`과 같은 값이다).
- **스위치가 판단 기준인지(새로)**: 실전 환경의 `orders_allowed`를 `True`로 바꾼 테스트를 둔다. 기본 설정만 보면 `is_real`로 잘못 구현해도 통과하기 때문이다. 가짜 키움 서버를 쓰므로 실제 서버로 나가지 않는다.
  - 관문: 주문 TR이 나간다.
  - 수동 주문 API: 주문 TR이 나간다.
  - SL / TP: `can_enable`이 `True`이고 켠 세션을 저장할 수 있다.
  - SL / TP 감시: 실전 보유종목을 실전 도메인에서 읽고 실전 실시간 주소로 시세를 등록한다.
  - 미체결: `cancelable`이 `True`다.
  - `/api/environments`: `orders_allowed`가 `true`다.
  - 자동 매도와 취소의 전체 흐름은 시험하지 않는다. 관문과 미체결 표시를 위에서 따로 확인한다.
- **화면 e2e**: 실전에서 주문 패널·미체결 취소가 막히고, 모의에서 열린다. `/api/environments`가 실패하면 주문 버튼이 막힌다. 가짜 응답이 실전에 `orders_allowed: true`를 주면 실전에서도 주문 버튼과 취소 버튼이 열린다.
- **모의투자 확인은 하지 않는다.** 모의에서 키움에 나가는 요청은 바뀌지 않는다.
- 전체 테스트, lint, format 검사, type check, 화면 build·e2e를 돌린다.

## Out of Scope

- 실전 주문 열기와 스위치를 `.env`로 옮기는 일. 열 때는 실전에서 주문·취소·SL / TP를 직접 확인한다. 실전 SL / TP의 장구분(290)·체결가 형식, 실전 실시간 종목 등록 한도(계정당 200개)는 모의투자에서만 확인됐다.
- 수동 주문과 자동 매도의 스위치 나누기.
- WebSocket. 주문을 보내지 않는다.
- 호출 한도의 `ORDER_TR_IDS` 분류 바꾸기.
- 이 앱이 쓰지 않는 TR의 기능 추가.
- 폴더 정리(아키텍처 점검 06).

## Further Notes

- 아키텍처 점검(2026-10-27)의 후보 05다. 04 보유종목·거래소 판정보다 먼저 한다(2026-10-28 사용자 결정).
- 결정 기록: `docs/adr/0003-orders-blocked-at-kiwoom-client.md`.
- 작업 순서: branch `refactor/order-switch` 하나에 ① 스위치와 관문 ② 위쪽 검사 정리 ③ 화면을 커밋별로 두고 PR 하나로 올린다.

## Implementation Notes

- 2026-10-28 구현 중 `sl_tp_watch.py`의 `WATCHED`가 실전을 따로 빼고 있음을 찾았다. 사용자와 확인해 감시 대상도 스위치를 따르게 했다(Q5 "스위치 하나"를 그대로 지킨다).

## Review Log

### 2026-10-28: Codex review
- ✏️ 실전 취소는 관문에 닿기 전에 400으로 끝남: 코드는 그대로 두고 spec과 테스트 기대값을 400 `cancel_not_supported`로 고쳤다. `blocked_reason="real"`도 같은 스위치를 읽어 함께 풀리므로, 취소 가능 여부를 둘로 나눌 이득이 없다.
- ✏️ 스위치가 판단 기준인지 확인하는 테스트가 없음: 실전에 `orders_allowed=True`를 넣은 테스트를 더했다. 스위치를 읽는 곳마다 한 번씩만 확인하고, 자동 매도·취소의 전체 흐름은 무거워서 뺐다.
