# 01: 실전 주문·조회 묶음 한도

**What to build:** 실전투자에서는 같은 TR 1회씩 규칙 대신, 국내·미국 각각 주문·조회 요청을 묶어 최근 기준 시간 동안 공식 한도까지만 보낸다(국내 주문 5·조회 5, 미국 주문 10·조회 5). 모의투자와 토큰 발급은 지금처럼 같은 TR 1회씩이다. 기준 시간은 모두 1초에서 1.2초로 바꾼다.

Spec: `.scratch/real-trading-call-limits/spec.md` (User Stories 1–3, 5–6, Implementation Decisions > 간격 단위와 한도·세는 방식·로그)

**Blocked by:** 없음

**Status:** ready-for-agent

- [ ] 기준 시간을 모의·실전·토큰 발급 모두 1.2초로 바꾼다(`CALL_INTERVAL_SECONDS`).
- [ ] 모의투자의 단위·한도는 그대로다. 기존 모의 간격 테스트는 기준 시간만 1.2초로 바뀐 채 통과한다. 실전을 다룬 기존 테스트(`test_domestic_real_and_us_real_share_one_unit`)는 새 규칙에 맞게 바꾼다. 국내·미국 실전 `ka00001` 두 건은 함께 바로 나가고, 둘을 합쳐 6번째 국내 조회는 기다린다.
- [ ] 실전 단위는 (인증정보 묶음, TR의 시장, 요청 구분)이다. TR의 시장은 TR 접두어(`ka`·`kt` 국내, `ust`·`usa` 미국)로 정한다.
- [ ] 주문 TR 목록(`kt10000`, `kt10001`, `kt10003`, `ust20000`, `ust20001`, `ust20003`)에 있으면 주문, 없으면 조회다.
- [ ] 한도: 국내 주문 5, 국내 조회 5, 미국 주문 10, 미국 조회 5(1초당). 최근 1.2초 동안 보낸(예약 포함) 요청이 한도에 차 있으면 가장 오래된 요청 1.2초 뒤에 보낸다.
- [ ] 토큰 발급(`au10001`)은 모든 투자 환경에서 (인증정보 묶음, `au10001`) 1.2초에 1회다.
- [ ] 먼저 온 요청이 먼저 나가고, 5초를 넘게 기다려야 하면 보내지 않고 `rate_limited`를 돌려주며 자리를 차지하지 않는다. 연속조회 다음 페이지도 한 번으로 센다.
- [ ] `kiwoom_pacing_wait`/`kiwoom_pacing_rejected`에 `pacing_unit`(`domestic_order`, `domestic_inquiry`, `us_order`, `us_inquiry`, `tr`)이 남는다.
- [ ] 주문 기능이 쓰는 TR(`ORDER_API_IDS`, `CANCEL_API_IDS`)이 모두 주문 TR 목록에 있는지 테스트로 확인한다.
- [ ] spec Testing Decisions의 피크타임을 뺀 항목을 가짜 시계 테스트로 검증한다. 기존 테스트, ruff, mypy가 모두 통과한다.
