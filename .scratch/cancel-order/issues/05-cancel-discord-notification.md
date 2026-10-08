# 05: 취소 접수 Discord 알림

**What to build:** 주문 취소가 접수되면 Discord로 **알림**이 온다. 대시보드를 보고 있지 않아도 어떤 주문이 얼마나 취소됐는지 알림만으로 안다. 거부되거나 접수 여부를 모를 때는 보내지 않는다.

Spec: `.scratch/cancel-order/spec.md` (User Stories 46–49, Implementation Decisions > Discord 알림)

**Blocked by:** 03

**Status:** ready-for-agent

- [x] 접수되면 "주문 취소 접수" embed가 한 번 간다. 필드는 투자 환경, 종목(종목명과 코드), 원래 주문(`side_label`), 취소 수량(`null`이면 "남은 수량 전부"), 원주문번호 → 새 주문번호, 시각(KST)이다.
- [x] 거부·확인 불가·실전 차단·재확인 실패·중복은 Discord 요청이 없다.
- [x] 알림은 응답을 기다리지 않는 별도 작업이다. Discord가 실패하거나 꺼져 있어도 취소 응답은 200이다.
- [x] 가짜 Discord 응답을 붙잡아 둔 동안에도 취소 API 응답이 먼저 끝난다.
- [x] 알림 전송과 실패가 로그에 남고 토큰은 가려진다.
- [x] pytest(가짜 Discord로 embed 내용, 보내지 않는 경우들, Discord 실패 시 200, Discord를 붙잡아 둔 동안 응답이 먼저 끝남)로 검증한다. 기존 테스트, ruff, mypy가 모두 통과한다.
