# 02: 로그인 화면과 API 보호

**What to build:** `.env`의 `PASSWORD`로 로그인한다. 로그인하지 않고 열면 비밀번호 입력 화면만 보이고, 맞히면 대시보드로 간다. 로그인하지 않은 `/api` 요청은 모두 401이고 키움에 아무 요청도 나가지 않는다. 어떤 화면에서든 401을 받으면 로그인 화면으로 돌아간다. 이 단계에서는 로그인 세션이 쿠키가 있는 동안 유지된다(재접속 종료·만료·시도 제한·알림은 뒤 ticket).

Spec: `.scratch/password-auth/spec.md` (설정, 세션, API, 화면, 로그)

**Blocked by:** 01

**Status:** ready-for-agent

- [x] `PASSWORD`가 없거나 빈 값이면 앱이 시작을 거부하고 원인이 로그에 남는다. 값은 앞뒤 공백을 떼지 않고 그대로 쓰며 로그 가림 목록에 등록한다. `.env.example`에 `PASSWORD=`를 더한다.
- [x] `POST /api/auth/login`: 맞으면 204와 세션 쿠키, 틀리면 401 `invalid_password`. 비교는 양쪽을 UTF-8 바이트로 바꾼 뒤 `hmac.compare_digest`로 한다. 한글이 섞인 비밀번호도 성공·실패가 정상이다.
- [x] 로그인 본문은 직접 읽는다. 깨진 JSON, 객체가 아님, `password` 없음, 문자열이 아님은 400 `invalid_request`이고 응답·로그에 보낸 값이 없다.
- [x] 세션 ID는 무작위 값으로 서버 메모리에 하나만 둔다. 새로 로그인하면 이전 세션은 401이 된다(`session_replaced` 로그).
- [x] 쿠키는 `stock_bot_session`, `HttpOnly`, `SameSite=Strict`, `Path=/`, `Max-Age`·`Expires` 없음. HTTPS 요청일 때만 `Secure`를 붙인다.
- [x] `GET /api/auth/session`: 유효하면 200, 아니면 401 `unauthorized`.
- [x] 로그인·세션 확인(과 뒤 ticket의 로그아웃)을 뺀 모든 `/api` 요청은 세션이 없으면 401 `unauthorized`이고 가짜 키움에 요청이 없다. 검사는 요청 ID middleware 안쪽에서 돌아 401 로그에 요청 ID가 남는다.
- [x] `POST` 요청에 `Origin`이 있고 Host와 다르면 403 `forbidden_origin`.
- [x] 정적 파일은 인증 없이 준다.
- [x] 로그: `login_succeeded`, `login_failed`, `session_replaced`, `unauthorized_request`, `origin_rejected`. 비밀번호·세션 ID·쿠키 헤더는 남지 않는다.
- [x] 화면: 처음 열면 세션을 확인하고, 없으면 로그인 화면만 보인다(계좌 요청 없음). Enter로 제출되고 보내는 동안 버튼이 잠긴다. 틀리면 "비밀번호가 올바르지 않습니다"와 함께 입력 칸이 비워진다.
- [x] 화면: 아무 API든 401 `unauthorized`면 로그인 화면으로 돌아가고 "로그인이 만료되었습니다"가 보인다. 주문 요청의 401은 "접수 여부 확인 불가"가 아니라 주문하지 않은 실패다.
- [x] 백엔드 테스트: `FAKE_ENV`에 `PASSWORD`를 넣고 `make_client`와 `create_app`을 직접 부르는 테스트가 로그인한 상태로 기존처럼 통과한다.
- [x] e2e: `openDashboard`가 `/api/auth/*`를 가짜로 응답하고 로그인 화면을 거친다. `**/api/**`를 통째로 가로채는 spec도 `/api/auth/*`를 처리한다. 기존 기대는 그대로 통과한다.
- [x] README: HTTP에서는 비밀번호·쿠키가 암호화되지 않으므로 LAN·VPN에서만 접속한다는 안내, `@live` 테스트를 `PASSWORD` 환경변수와 함께 실행하는 방법.
- [x] `uv run pytest`, `ruff check`, `ruff format --check`, `mypy`, `pnpm --dir web typecheck`, `build`, `test:e2e` 통과.
