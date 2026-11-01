# 02: Trailing Stop 메뉴, 화면, 제외종목

**What to build:** 왼쪽 메뉴 "자동매매" 묶음에서 SL / TP 아래에 "Trailing Stop"이 생긴다. 화면에는 매도 방식 안내, 준비 중 안내, 제외종목(검색·추가·삭제), 저장 막대가 있다. 설정은 자동매매 설정 파일의 `trailing_stop` 칸에 저장되고, 같은 환경의 SL / TP 칸은 바뀌지 않는다. 이 티켓에서 세션은 빈 목록만 받는다(세션은 03에서).

Spec: `.scratch/trailing-stop/spec.md` (자체 서버 API, Trailing Stop 검사 규칙의 제외종목, 화면, 로그)

**Blocked by:** 01

**Status:** ready-for-agent

- [x] `GET/PUT /api/environments/{environment}/trailing-stop`이 생긴다. 응답 모양과 처리 순서, 오류 종류(`trailing_stop_settings_unreadable`, `invalid_trailing_stop_settings`, `trailing_stop_settings_write_failed`)는 spec을 따른다. 로그인 세션, 잘못된 환경 값, 다른 사이트 출처 PUT은 기존 규칙대로 거부한다.
- [x] 저장은 `trailing_stop` 칸과 그 칸의 `updated_at`만 바꾼다. Trailing Stop을 저장해도 `sl_tp` 칸과 그 `updated_at`이 그대로고, SL / TP를 저장해도 `trailing_stop` 칸이 그대로다. Trailing Stop 저장은 SL / TP 감시에 알리지 않는다.
- [x] `trailing_stop` 칸이 규칙에 맞지 않으면 두 기능의 GET·PUT이 모두 500이고 파일은 그대로다.
- [x] 제외종목 검사는 SL / TP와 같은 규칙을 함께 쓴다. 이 티켓에서 `sessions`는 빈 목록만 받는다.
- [x] 메뉴는 데스크톱 메뉴와 모바일 서랍에 보이고 아이콘은 하락 추세 모양이다. 화면 제목은 "Trailing Stop"이고 그 아래 환경 이름이 보인다.
- [x] 매도 방식 안내와 준비 중 안내가 spec 문구대로 보인다.
- [x] SL / TP 화면에서 제외종목 영역, 불러오기 실패 안내, 저장 막대를 공통 부품으로 빼서 두 화면이 함께 쓴다. SL / TP 화면의 겉모습과 동작은 바뀌지 않는다.
- [x] 불러오는 중, 불러오기 실패와 다시 시도, 저장 성공·실패 토스트("Trailing Stop 설정을 저장했습니다"), 저장 중 편집 막힘, 저장하지 않은 변경 보호(환경 전환, 메뉴 이동, SL / TP로 이동, 로그아웃)가 SL / TP와 같게 동작한다.
- [x] 조회·저장·거부·읽기 실패·쓰기 실패 로그 이벤트를 환경, 요청 ID, 결과, 개수, 원인과 함께 남긴다.
- [x] 테스트: pytest로 위 API 동작과 칸 보존·깨진 칸 처리를, Playwright로 메뉴·안내·제외종목·저장·이동 확인 창·불러오기 실패·모바일 폭을 확인한다. 기존 SL / TP e2e가 모두 통과한다.
- [x] 기존 테스트, lint, 타입 검사, 화면 빌드가 모두 통과한다.
