# 02: Trailing Stop 트리거 때 자동 매도 (국내·미국 모의)

**What to build:** 모의투자 환경의 켜진 Trailing Stop 세션에서 Trailing Stop 트리거가 일어나면, 그 종목의 매도 가능 수량 전량을 시장가로 파는 자동 매도를 낸다. 공용 자동 매도를 그대로 써서 같은 종목의 SL / TP 자동 매도와 겹치지 않고, 체결 알림에 "Trailing Stop 자동 매도"가 붙는다. Discord로 접수·실패·결과 확인 불가를 알린다. 계좌 확인의 Trailing Stop 보기에서 매도 조건 충족 종목의 자동 매도가 보내는 중인지, 접수됐는지, 실패했는지 보인다. 실전투자에서는 주문이 나가지 않는다. 이 티켓에서는 한 번 시도한 종목을 다시 시도하지 않는다(03).

Spec: `.scratch/trailing-stop-auto-sell/spec.md` (자동 매도 연결, 언제 파는가, 결과별 처리, 자체 서버 API, 화면, Discord 알림, 로그)

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] 트리거가 일어난 시세 체결에서 공용 자동 매도를 이름 "Trailing Stop"으로 부른다. 미국은 트리거 때 읽은 거래소로 보낸다. 시세 처리는 주문 결과를 기다리지 않는다. 트리거 때 만든 요청 ID를 주문 키, 로그, 체결 알림에 이어 쓴다.
- [ ] 세션 실행마다 자동 매도를 시도한 종목을 두고, 같은 실행에서는 다시 내지 않는다.
- [ ] 제외종목과 대상 아님 종목은 지금처럼 판정하지 않으므로 팔지 않는다.
- [ ] SL / TP 자동 매도가 같은 종목에서 진행 중이면 보내지 않는다(`in_progress`). SL / TP가 다 팔았으면 매도 가능 수량 0으로 보내지 않는다(`skipped`). 둘 다 알림 없이 로그만 남긴다.
- [ ] 상태 API의 `stocks` 각 항목에 `auto_sell`(`state`: `sending`·`accepted`·`failed`, `reason`, `quantity`, `order_no`, `at`)을 더한다. 시도하지 않았으면 `null`이다.
- [ ] Discord: "Trailing Stop 자동 매도 접수 / 실패 / 결과 확인 불가"를 spec의 칸과 색으로 보낸다. 실패 설명은 "다음 Trailing Stop 세션에 다시 시도합니다."이다. 트리거만으로는 알리지 않는다. SL / TP 알림과 함께 쓸 수 있는 작은 부분만 함께 쓴다.
- [ ] 화면: 매도 조건 충족 종목의 상세에 "자동 매도" 칸을 두고 "보내는 중", "접수 · 주문번호 · 수량 · 시각", "실패 · 다음 세션에 다시 시도 · <이유>"를 보인다. 좁은 화면 배지는 상태 이름만이다. Trailing Stop 설정 화면의 안내와 상태 보기 위의 안내를 spec 문구로 바꾼다.
- [ ] 로그: `trailing_stop_auto_sell_started`(attempt=`trigger`), `trailing_stop_auto_sell_finished`, `trailing_stop_notified`에 투자 환경, 요청 ID, 종목코드, 결과, 수량, 주문번호, 원인을 담는다.
- [ ] 테스트(pytest, 앱 전체를 HTTP로): Trailing Stop 감시 테스트의 "모든 테스트에서 주문 TR 0건" 자동 확인을 빼고, 테스트마다 주문 TR의 횟수와 내용을 확인한다. 국내·미국 트리거 매도(수량, 시장가, 미국 거래소), 같은 실행에서 한 번만, 결과별(접수·거부·결과 모름·매도 가능 수량 0·SL / TP 진행 중) 알림과 `auto_sell`, 체결 알림의 "Trailing Stop 자동 매도", 실전 환경 주문 0건, 로그 칸을 확인한다.
- [ ] 테스트(Playwright): 자동 매도 값 세 가지와 실패 이유 문구, 좁은 화면 배지, 바뀐 안내 문구를 확인한다.
- [ ] 기존 SL / TP 테스트를 포함해 기존 테스트, lint, 타입 검사, 화면 빌드, e2e가 모두 통과한다.
