# 02: 미국 미체결 주문 취소

**What to build:** 취소할 수 있는 미국 미체결 주문에 **취소** 버튼이 생긴다. 누르면 **최종 확인**이 열리고 미체결을 다시 조회한다. 미국은 일부 취소가 없으므로 수량 칸 대신 "남은 수량 전부"를 보여준다. "주문 취소하기"를 누르면 서버가 한 번 더 확인한 뒤 키움에 한 번만 **주문 취소**를 보낸다. 접수되면 토스트, 계좌·목록 새로고침, Discord **알림**은 국내와 같다.

Spec: `.scratch/us-cancel-order/spec.md` (User Stories 18–25, Implementation Decisions > 주문 취소·화면·Discord 알림)

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] `POST /api/environments/{environment}/cancellations`가 미국 투자 환경을 받는다. 실전은 403이고 키움 요청이 없다.
- [ ] 미국 원주문번호는 1~9자리 숫자이고, 아니면 400 `invalid_request`이며 키움을 부르지 않는다.
- [ ] 주문 키 중복(409), 원주문번호별 잠금, `ust21050` 재조회와 그 실패(502 `open_orders_check_failed`), 주문 없음(400), 취소 불가(400 `cancel_not_supported`)는 국내와 같다.
- [ ] 요청 수량이 재조회한 미체결 수량보다 많으면 400 `cancel_quantity_exceeded`, 적으면 400 `partial_cancel_unsupported`이고 `ust20003` 요청이 없다.
- [ ] `ust20003`이 `/api/us/ordr`에 `orig_ord_no`, `stex_tp`(NYSE→NY, NASDAQ→ND, AMEX→NA), `stk_cd`로 재시도 없이 한 번 나간다.
- [ ] 연결 실패와 `ord_no` 없는 응답은 502 `order_result_unknown`이다. 접수 응답의 `cancel_quantity`는 `cncl_ord_qty`이고, 0이거나 읽지 못하면 null이다.
- [ ] 접수되면 Discord에 "주문 취소 접수" embed가 가고, 종목은 "이름 (티커)"다. 거부·확인 불가·차단은 보내지 않는다.
- [ ] 화면: 미국 취소 가능 줄에 취소 버튼이 있다. 최종 확인에는 거래소와 USD 주문가격이 보이고, 수량 칸과 "전부" 버튼 대신 "취소 수량: 남은 수량 전부"가 보인다.
- [ ] 요청 `quantity`는 가장 최근 재조회의 미체결 수량이다. 재조회할 때마다 최신 값으로 바뀐다.
- [ ] 3주로 보낸 취소가 `cancel_quantity_exceeded`로 거부되고 재조회 결과가 1주면, "미체결 수량이 1주로 줄었습니다" 안내가 보이고 "주문 취소하기"가 풀리며, 다시 보내면 새 키와 `quantity=1`로 나간다.
- [ ] `partial_cancel_unsupported`·`cancel_quantity_exceeded` 거부 뒤에는 국내와 같이 재조회한다.
- [ ] 접수, 확실한 실패(새 키로 재전송), 확인 불가(닫고 다시 불러오기)의 화면 처리가 국내와 같다(대표 경우를 검증).
- [ ] 지금 있는 "미국 취소는 400" pytest를 새 동작에 맞게 바꾼다.
- [ ] 로그에 미국 취소 요청·재확인·차단·접수·거부·확인 불가가 주문 키·원주문번호와 함께 남는다.
- [ ] pytest와 Playwright로 위 동작을 검증한다. 기존 테스트, ruff, mypy, 빌드가 모두 통과한다.
