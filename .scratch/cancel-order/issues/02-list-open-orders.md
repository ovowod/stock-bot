# 02: 미체결 주문 목록 보기

**What to build:** 국내 투자 환경의 계좌 확인 맨 아래에 **미체결 주문** 패널이 생긴다. 앱 밖에서 낸 주문을 포함해 그 계좌의 미체결 주문을 모두 보여준다. 패널은 계좌와 따로 불러오므로 미체결 조회가 실패해도 계좌는 보인다. 취소할 수 없는 주문과 실전투자는 이유를 보여준다. 취소 버튼은 03에서 붙인다.

Spec: `.scratch/cancel-order/spec.md` (User Stories 1–23, Implementation Decisions > 미체결 주문 조회·화면)

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [x] 자체 서버에 `GET /api/environments/{environment}/open-orders`가 있다. `ka10075`를 `all_stk_tp=0`, `trde_tp=0`, `stk_cd` 빈 값, `stex_tp=0`으로 그 환경의 도메인에 부르고, 연속조회를 모두 이어 받는다.
- [x] 응답은 주문 목록과 조회 시각이다. 주문마다 `order_no`, `code`, `name`, `side`(buy·sell), `side_label`(+·- 제거), `order_type`(보통→지정가), `price`(절댓값, 0이면 null), `ordered_quantity`, `remaining_quantity`, `time`(HH:MM:SS), `exchange`, `cancelable`, `blocked_reason`(real·credit·exchange·null, 이 순서로 하나)을 담는다.
- [x] KRX 현금 주문(`stex_tp=1`, `sor_yn`≠Y, 신용 아님)만 `cancelable`이다. 실전투자는 모든 줄이 `real`이다.
- [x] 주문번호·종목코드·미체결 수량이 비었거나 숫자가 아니면 응답 형식 오류다. 미국 환경은 400이고 키움을 부르지 않는다.
- [x] 국내 계좌 확인 맨 아래(넓은 화면은 전체 너비, 모바일은 보유종목 다음)에 패널과 개수가 보인다. 줄마다 종목명·코드, 매수/매도 색 배지, 주문 유형, 가격(없으면 "시장가"), "미체결 N / 주문 M주", 시각, 거래소가 보인다.
- [x] 비면 "미체결 주문이 없습니다", 불러오는 동안 로딩 모양, 실패면 패널에만 오류와 "다시 시도"가 보이고 계좌는 그대로 보인다.
- [x] 신용·NXT·통합 줄에는 이유 배지와 "키움 앱에서 취소하세요" 안내가 있다. 실전투자는 패널에 "실전투자에서는 주문 취소를 할 수 없습니다" 안내가 한 번 보인다.
- [x] 상단 "새로고침"과 매도 접수가 account와 open-orders를 모두 다시 요청한다.
- [x] 미국 환경에는 패널이 없고 open-orders 요청이 나가지 않는다.
- [x] 모바일 폭에서 가로 스크롤이 생기지 않는다.
- [x] 로그에 미체결 조회 결과(건수)와 실패가 남고 비밀값은 가려진다.
- [x] pytest(응답 변환, 여러 페이지, 빈 목록, 이유별 분류, 형식 오류, 미국 거부)와 Playwright(위 화면 동작)로 검증한다. 기존 테스트, ruff, mypy, 빌드가 모두 통과한다.
