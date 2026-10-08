# 01: 미국 미체결 주문 목록 보기

**What to build:** 미국 투자 환경의 계좌 확인 맨 아래에도 **미체결 주문** 패널이 생긴다. 앱 밖에서 낸 주문을 포함해 그 계좌의 미국 미체결 주문을 보여준다. 이름과 **거래소**는 보유종목처럼 **종목 목록**에서 찾는다. 취소할 수 없는 주문(실전투자, 예약주문, 거래소를 찾지 못한 주문)은 이유를 보여준다. 취소 버튼은 02에서 동작하게 한다.

Spec: `.scratch/us-cancel-order/spec.md` (User Stories 1–17, Implementation Decisions > 미체결 주문 조회·화면)

**Blocked by:** None (can start immediately). 국내 PR(ovowod/stock-bot#7)이 머지된 코드 위에서 시작한다.

**Status:** ready-for-agent

- [ ] `GET /api/environments/{environment}/open-orders`가 미국 투자 환경에서 `ust21050`을 `ord_dt` 빈 값, `slby_tp=0`, `stex_tp`·`stk_cd` 빈 값으로 그 환경의 도메인(`/api/us/acnt`)에 부른다. 연속조회는 최대 10페이지다.
- [ ] `ord_cntr_tp=12`(취소주문) 줄은 빠지고, 정정주문(11)의 `side_label`은 "매수정정"·"매도정정"이다.
- [ ] 응답 모양은 국내와 같다. `order_no`(9자리), `code`(티커), `side`(slby_tp 1→sell, 2→buy), `order_type`(`frgn_trde_nm`), `price`(`ord_uv` 소수, 0이면 null), `ordered_quantity`, `remaining_quantity`(`ord_remnq`), `time`(`ord_time`)이다.
- [ ] 이름·거래소는 종목 목록에서 티커로 찾는다. 없거나 종목 목록 조회가 실패하면 이름은 `frgn_stk_nm`, 거래소는 null이고, 목록 응답은 200이다.
- [ ] `blocked_reason`은 실전 `real`, 예약주문(`rsrv_tp`가 "예약" 또는 "1") `reserved`, 거래소 null이면 `exchange`이고, 이 순서로 하나만 준다.
- [ ] 필수 칸(주문번호·티커·미체결 수량)이 깨지면 응답 형식 오류이고, 가격·주문 수량이 깨지면 그 칸만 비운다.
- [ ] 화면: 미국 계좌 확인 맨 아래에 패널이 보이고, USD 가격(보유종목과 같은 4자리 표시), 티커, 거래소, "미체결 N / 주문 M주", 시각이 보인다.
- [ ] "예약 주문"·"거래소 확인 불가" 배지 줄에는 "키움 앱에서 취소하세요" 안내만 있다. 미국 실전에서는 "실전투자에서는 주문 취소를 할 수 없습니다" 안내가 한 번 보인다.
- [ ] 이 티켓에서는 미국 줄에 취소 버튼이 보이지 않는다(02에서 연다).
- [ ] 상단 새로고침과 매도 접수가 미국에서도 account와 open-orders를 함께 다시 요청한다.
- [ ] 지금 있는 "미국 open-orders는 400" pytest와 "미국 계좌 확인에는 패널이 없다" e2e를 새 동작에 맞게 바꾼다.
- [ ] 모바일 폭에서 가로 스크롤이 생기지 않는다.
- [ ] 로그에 미국 미체결 조회 결과(건수)와 종목 목록 조회 실패가 남는다.
- [ ] pytest와 Playwright로 위 동작을 검증한다. 기존 테스트, ruff, mypy, 빌드가 모두 통과한다.
