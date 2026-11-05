# 01: Dead Cross 보기와 상태 조회의 뼈대

**What to build:** 계좌 확인의 보기 전환이 [기본 | Trailing Stop | Dead Cross]가 되고, Dead Cross 보기에서 보유종목마다 지금 세션 설정과 계산 대기 이유를 본다. 서버는 모의투자 환경마다 Dead Cross 감시를 두고, 켜진 Dead Cross 세션 동안 보유종목만 읽는다. 아직 차트는 조회하지 않는다.

Spec: `.scratch/dead-cross-status/spec.md`

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [x] `GET /api/environments/{environment}/dead-cross/status`가 spec의 응답 모양(`watching`, `not_watching_reason`, `session`, `excluded`, `stocks`)으로 답하고, 키움을 부르지 않는다. 로그인 세션이 없거나 환경 값이 잘못되면 기존 규칙대로 거부한다.
- [x] `session`은 지금 시각을 포함하는 Dead Cross 세션(꺼져 있어도 담음, 없으면 `null`)이고 실전 환경에서도 담긴다.
- [x] 감시는 국내·미국 모의투자 환경에만 생기고, 켜진 세션이 시작할 때와 내 계좌 체결이 들어올 때, 체결 등록이 다시 성공했을 때 보유종목을 다시 읽는다. 읽기 실패 재시도는 Trailing Stop과 같다.
- [x] 계산 대기 이유가 맞게 나온다: 켜진 세션 없음, 실전투자(`stocks=[]`), 제외종목, 대상 아님(신용 줄은 `stocks`에서 빠짐, 같은 종목 여러 줄, 미국 거래소 확인 불가), 보유종목 확인 중, 체결 등록 거부, 공유 실시간 연결이 밀려남(ADR 0001대로 감시를 멈춤. 체결 피드가 멈춤을 알리거나 공유 연결 상태를 읽는 경로를 더함), 설정 파일을 읽지 못함, 인증정보 없음. 매입가 없음은 대상 아님이 아니다.
- [x] Dead Cross 설정을 저장하면 감시가 새 설정(세션·제외종목)을 쓴다.
- [x] 보기 전환에 Dead Cross가 생기고, 고른 보기가 새로고침 뒤에도 유지된다. 기존 Trailing Stop 보기는 그대로 동작한다.
- [x] Dead Cross 보기는 줄을 눌러 상세를 펼치고, 상태 배지, 설정 요약("5분봉 · 단기 5봉 · 장기 20봉"), 활성 세션 여부, 제외종목 여부, 상태와 이유를 보인다. 아직 값이 없는 칸은 "–"이다. 매도 버튼과 기존 안내는 그대로다.
- [x] 목록 위에 spec의 안내 문구가 있다. 3초 갱신, 탭이 숨으면 멈춤, 조회 실패 안내가 Trailing Stop 보기와 같게 동작한다. 모바일 폭에서 잘리지 않는다.
- [x] 상태 조회는 실패만 INFO 이상으로 로그를 남긴다. 감시 시작·중단과 보유종목 다시 읽기를 로그로 남긴다.
- [x] 어떤 테스트에서도 매도 주문 TR이나 차트 TR이 나가지 않는다. 테스트, lint, 타입 검사, 화면 빌드가 통과한다.
