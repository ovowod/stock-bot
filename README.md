# Stock Bot

키움 REST API 기반 개인용 주식 자동매매 대시보드. 현재 기능은 계좌 확인, 종목 검색, 순위다.

## 준비

- Python 3.14, [uv](https://docs.astral.sh/uv/), Node.js, pnpm
- 프로젝트 최상위 `.env`에 인증정보를 채운다. 변수 이름은 `.env.example`을 따른다.
  - `*_ACCOUNT_NO`는 8자리 또는 10자리로 적는다.
  - `PASSWORD`는 대시보드 로그인 비밀번호다. 비어 있으면 서버가 시작되지 않는다.

## 접속과 보안

- 대시보드는 `PASSWORD`로 로그인해야 쓸 수 있다.
- HTTPS가 없으므로 비밀번호와 로그인 쿠키가 암호화되지 않은 채 네트워크를 지나간다. 같은 PC, 집 안 네트워크, VPN에서만 접속하고 인터넷에 직접 열지 않는다.

```bash
uv sync
pnpm --dir web install
```

## 개발 모드

터미널 두 개에서 각각 실행하고 http://127.0.0.1:5173 을 연다.

```bash
uv run uvicorn stock_bot.app:create_app --factory --reload --port 8000
```

```bash
pnpm --dir web dev
```

## 빌드와 실행

```bash
pnpm --dir web build
uv run uvicorn stock_bot.app:create_app --factory --port 8000
```

빌드 후에는 서버 하나가 화면까지 제공한다(http://127.0.0.1:8000).

## 검증

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy
pnpm --dir web typecheck
pnpm --dir web test:e2e
```

- `test:e2e`는 브라우저의 `/api` 요청을 가짜 응답으로 바꿔 키움 서버를 호출하지 않는다.
- `pnpm --dir web test:live`는 서버를 띄운 상태에서 국내·미국 모의 서버만 실제로 조회한다. 로그인 비밀번호를 `PASSWORD` 환경변수로 넘긴다.

```bash
PASSWORD='로그인 비밀번호' pnpm --dir web test:live
```

로그는 stdout과 `logs/stock-bot.log`(날짜별 분리, 14일 보관)에 남는다.
