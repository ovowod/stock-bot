# 01: SL / TP 감시에서 공통 판정 부품 빼기

**What to build:** Trailing Stop 감시가 SL / TP와 같은 규칙을 그대로 쓸 수 있도록, SL / TP 감시 안에 있는 판정 부품을 공통으로 꺼낸다. 사용자에게 보이는 동작은 바뀌지 않는다.

Spec: `.scratch/trailing-stop-status/spec.md` (Trailing Stop 감시)

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 보유종목의 대상 아님 분류(신용 보유분, 매입가 없음, 같은 종목 여러 줄, 미국 종목의 거래소 확인 불가)를 한곳에서 정하고 SL / TP 감시가 그것을 쓴다. 분류 결과에 이유(`credit`, `no_purchase_price`, `duplicate`, `unknown_exchange`)가 담긴다.
- [ ] 지금 시각을 포함하는 세션 찾기(시작 포함, 종료 제외, 맞닿은 세션은 뒤 세션, 시장 시간대)를 SL/TP 세션과 Trailing Stop 세션이 함께 쓸 수 있게 한다. 켜진 세션만 찾는 경우와 꺼진 세션도 찾는 경우를 모두 다룬다.
- [ ] 시세 체결 메시지에서 가격 읽기(부호 처리, 판정할 수 없는 가격)와 정규장 장구분 판단을 공통으로 쓴다.
- [ ] SL / TP의 로그 이벤트 이름과 칸은 그대로다.
- [ ] 기존 SL / TP 테스트가 고치지 않고 모두 통과한다. 기존 테스트, lint, 타입 검사가 모두 통과한다.
