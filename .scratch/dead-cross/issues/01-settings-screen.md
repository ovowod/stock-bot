# 01: Dead Cross 메뉴와 설정 화면, 설정 저장

**What to build:** 자동매매 묶음의 Trailing Stop 아래에 Dead Cross 메뉴와 화면을 더한다. 투자 환경마다 제외종목과 Dead Cross 세션(시각, ON/OFF, 봉 주기, 단기·장기 이동평균 기간)을 정해 자동매매 설정 파일의 `dead_cross` 칸에 저장한다. 실전투자에서는 세션을 켤 수 없다. 차트 조회, 이동평균 계산, 하향 돌파 판정, 매도는 하지 않는다.

Spec: `.scratch/dead-cross/spec.md`
ADR: `docs/adr/0004-one-auto-trading-settings-file-per-environment.md`

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [x] 먼저 공용 세션 검사가 기능의 기준 칸 검사에 시장을 넘기게 하고, 공용 설정 화면의 기준 칸 영역도 시장을 알게 한다. SL / TP와 Trailing Stop의 동작은 바뀌지 않으며 기존 테스트로 확인한다.
- [x] `GET`/`PUT /api/environments/{environment}/dead-cross`가 spec의 응답 모양, 오류 종류·메시지, 처리 순서를 따른다. 설정 조회·저장은 키움을 부르지 않는다.
- [x] 저장은 파일의 `dead_cross` 칸과 그 `updated_at`만 바꾸고, `sl_tp`·`trailing_stop`과 모르는 칸은 그대로 다시 쓴다. 어느 칸이든 깨져 있으면 Dead Cross 조회·저장도 500이다.
- [x] 봉 주기는 시장별 선택지만 받는다. 국내는 1·3·5·10·15·30·45·60분봉과 일봉, 미국은 1·3·5·10·15·30·40·60·120분봉과 일봉이다.
- [x] 단기 이동평균 기간은 2~119, 장기는 3~120의 정수만 받는다(true/false·소수 거부). 두 값이 있으면 켜짐 여부와 관계없이 단기 < 장기여야 한다.
- [x] 켜진 세션은 봉 주기와 두 기간이 모두 있어야 한다. 꺼진 세션은 비어 있어도 받는다.
- [x] 실전 환경에 켜진 세션을 PUT하면 403이고 파일은 그대로다. 파일의 `dead_cross` 칸에 실전 켜진 세션이 있으면 모든 기능의 조회가 500이다.
- [x] 화면에는 다음이 보인다.
  - 데스크톱 메뉴와 모바일 서랍의 "Dead Cross"
  - 매도 방식 안내와 준비 중 안내
  - 공용 제외종목·세션 영역
  - 시장별 봉 주기 선택, "봉" 단위가 붙은 기간 입력, 칸별 안내
  - "5분봉 · 단기 5봉 · 장기 20봉" 꼴의 요약 줄
  - 실전 환경의 스위치 막힘과 안내
- [x] 조회·저장·거부·읽기 실패가 공용 저장소의 `dead_cross_*` 로그 이벤트로 남는다.
- [x] pytest로 spec의 서버 테스트 항목을 확인한다. 가짜 키움은 "부르면 실패"로 둔다.
- [x] Playwright e2e로 spec의 화면 테스트 항목을 확인한다.
- [x] 기존 테스트, ruff(check·format), 타입 검사, 웹 빌드가 모두 통과한다.
