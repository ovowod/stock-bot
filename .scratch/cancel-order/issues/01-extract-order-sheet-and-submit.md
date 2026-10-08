# 01: 주문 시트와 주문 전송 분리 (prefactor)

**What to build:** 화면 동작은 그대로 두고, 주문 패널이 쓰는 시트 껍데기(모바일 아래 시트·넓은 화면 오른쪽 패널, 전송 중 닫기·Esc·바깥 클릭 잠금), 안내 상자(Callout), 주문류 POST(30초 시간 초과, 409·502 `order_result_unknown`·연결 끊김·형식이 다른 응답을 "접수 여부 확인 불가"로 분류)를 주문 패널 밖에서도 쓸 수 있게 꺼낸다. 이후 취소 최종 확인(03)이 이것을 그대로 쓴다.

Spec: `.scratch/cancel-order/spec.md` (Implementation Decisions > 화면)

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 시트 껍데기를 주문 패널과 다른 화면이 함께 쓸 수 있고, 주문 패널은 이것을 쓴다.
- [ ] Callout을 주문 패널 밖에서 쓸 수 있다.
- [ ] 주문류 POST 함수가 경로와 본문을 받아 매수·매도 주문과 같은 시간 초과·결과 분류를 한다. 매수·매도 주문은 이 함수를 쓴다.
- [ ] 화면에서 보이는 동작과 문구는 바뀌지 않는다.
- [ ] 기존 Playwright 테스트, 타입 검사, 빌드가 모두 통과한다.
