# app.py 정리와 폴더 정리

Status: ready-for-agent

## Problem Statement

`app.py`의 `create_app` 한 함수가 약 310줄이다. 서비스 조립, middleware 두 개, 오류 처리, route 17개를 모두 품고 있다.
- 로그인 쪽(login·logout·session route, `require_login` middleware, cookie 설정, 출처 확인, 접속 알림 내용)이 약 120줄로, API 목록 사이에 섞여 있다.
- 주문 취소 알림 내용(`_cancel_embed`)이 주문 쪽이 아니라 `app.py`에 있다.
- `parse_environment` + `environment_var.set`이 12번, 요청 본문 읽기(`try: await request.json() except ValueError`)가 5번 되풀이된다.

`src/stock_bot/`에는 파일 25개가 폴더 없이 한곳에 있다. 화면 쪽(`web/src/features/`)은 기능별 폴더로 나뉘어 있는데 서버 쪽은 그렇지 않아, "주문 기능은 어디 있나"에 답하려면 파일 이름을 하나씩 봐야 한다.

`tests/`도 한곳에 있고, 테스트 11개 파일이 다른 테스트 파일에서 도우미를 가져다 쓴다(`test_fill_notifications`, `test_sl_tp_auto_sell` 등). 가짜 Discord 서버(`FakeDiscord`)는 세 파일에 서로 다른 모양으로 있다.

## Solution

- `app.py`에는 서비스 조립, 공통 middleware·오류 처리, 얇은 route만 남긴다. 로그인 쪽은 `auth/`로, 취소 알림 내용은 `order/`로 옮기고, 되풀이는 dependency와 도우미 하나씩으로 줄인다.
- 소스를 기능별 폴더로 나눈다. 모든 기능이 쓰는 기반은 `core/`, 키움 연결은 `kiwoom/`에 둔다.
- 테스트도 같은 폴더로 나누고, 테스트끼리 가져다 쓰던 도우미를 도우미 파일로 옮긴다.

사용자가 보는 동작, API, 로그는 바뀌지 않는다.

## User Stories

1. As a 개발자, I want `app.py`를 열면 API 목록과 서비스 조립이 한 화면에 보이기를, so that 어떤 API가 있고 무엇이 무엇에 연결되는지 한눈에 본다.
2. As a 개발자, I want 로그인 관련 route·middleware·cookie·출처 확인이 한 파일에 있기를, so that 로그인 동작을 고칠 때 한 곳만 본다.
3. As a 개발자, I want 주문 취소 알림 내용이 주문 쪽에 있기를, so that SL / TP 알림(`sl_tp_alerts`)처럼 기능 옆에서 알림 내용을 찾는다.
4. As a 개발자, I want 투자 환경을 읽고 로그에 환경을 표시하는 일이 route마다 되풀이되지 않기를, so that 새 route에서 그 줄을 빠뜨리지 않는다.
5. As a 개발자, I want 서버 쪽 폴더 이름이 CONTEXT.md의 절과 화면 쪽 기능 폴더에 맞기를, so that 같은 기능을 서버와 화면에서 같은 이름으로 찾는다.
6. As a 개발자, I want 모든 기능이 쓰는 기반 파일이 `core/` 한곳에 있기를, so that 기능 파일과 기반 파일을 구분한다.
7. As a 개발자, I want 키움 호출·실시간 연결·응답 읽기·거래소 코드가 `kiwoom/`에 있기를, so that 키움 쪽 문제를 볼 때 한 폴더만 본다.
8. As a 개발자, I want 테스트가 소스와 같은 폴더 이름으로 나뉘기를, so that 기능의 테스트를 바로 찾는다.
9. As a 개발자, I want 여러 테스트가 쓰는 도우미가 테스트 파일이 아니라 도우미 파일에 있기를, so that 테스트 파일 하나를 고칠 때 다른 테스트가 깨질까 걱정하지 않는다.
10. As a 개발자, I want 가짜 Discord 서버가 공통으로 하나이기를, so that 알림 테스트마다 다른 가짜의 동작을 따로 기억하지 않는다.
11. As a 개발자, I want 폴더 이동 커밋이 경로 이동과 import 수정만 담기를, so that 리뷰에서 내용 변경을 따로 찾지 않는다.
12. As a 운영자, I want 로그의 logger 이름이 지금과 같기를, so that `logs/`의 이전 기록과 새 기록을 같은 이름으로 찾는다.
13. As a 운영자, I want `.env`, `logs/`, `data/`를 지금과 같은 폴더에서 찾기를, so that 폴더를 옮긴 뒤에도 설정·로그·SL / TP 설정이 그대로 쓰인다.
14. As a 운영자, I want 서버 실행 명령(`stock_bot.app:create_app`)이 그대로이기를, so that README와 배포 설정을 바꾸지 않는다.
15. As a 투자자, I want 로그인, 계좌 확인, 순위, 종목 검색, 주문, 취소, SL / TP, 체결 알림이 지금과 같이 동작하기를, so that 구조를 바꾸는 작업이 기능을 바꾸지 않는다.

## Implementation Decisions

**app.py 정리**

- 로그인 쪽을 `auth`의 새 module `login`으로 옮긴다. `install_login(app, auth, notify)` 하나가 다음을 등록한다.
  - `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/session`
  - `require_login` middleware (다른 사이트에서 온 조회 아닌 요청 거부, 로그인 없는 `/api` 거부)
  - 함께 옮기는 것: 로그인 없이 부를 수 있는 API 목록(`PUBLIC_API_PATHS`), cookie 설정, 출처 확인, 접속 정보 읽기, 접속 알림 내용과 색
- `create_app`은 지금 `require_login`을 등록하는 자리에서 `install_login`을 부른다. `require_login`이 `request_context`보다 먼저 등록돼야 거부 응답에도 요청 ID가 붙기 때문이다.
- `require_login`과 오류 처리가 함께 쓰는 오류 응답 만들기(`_error_response`)는 `errors`로 옮겨 하나만 둔다. 응답 모양(`error.kind`, `message`, `request_id`, detail)과 message 가리기는 그대로다.
- 알림을 기다리지 않고 따로 보내는 `notify`는 `create_app`에 둔다. 로그인 알림과 취소 알림이 함께 쓴다.
- 취소 알림 내용(`_cancel_embed`와 색)은 `order`의 새 module `alerts`로 옮긴다.
- 시각을 `"%Y-%m-%d %H:%M:%S KST"` 글자로 바꾸는 `_kst`는 접속 알림과 취소 알림이 함께 쓰므로 `notification`으로 옮긴다.
- 투자 환경을 읽고 `environment_var`를 설정하는 일은 FastAPI dependency 하나로 바꾼다. route는 `EnvironmentSpec`을 받는다. 잘못된 환경 이름의 오류 응답은 지금과 같다.
  - dependency는 `async def`로 쓴다. 일반 함수면 FastAPI가 다른 thread에서 돌려, 거기서 설정한 `environment_var`(ContextVar)가 route로 넘어오지 않고 로그의 `env`가 `"-"`로 남는다.
- 요청 본문 읽기는 도우미 하나로 바꾼다. 형식이 틀리면 지금처럼 `None`을 넘긴다.
- 얇은 route, 서비스 조립, lifespan, `request_context` middleware, 오류 처리, 화면 파일 mount는 `create_app`에 그대로 둔다.
- `create_app`의 인자는 바꾸지 않는다.

**소스 폴더**

| 폴더 | module |
|---|---|
| (맨 위) | `app` |
| `core` | `config`, `errors`, `logging_setup`, `masking`, `notification` |
| `kiwoom` | `kiwoom`, `realtime`, `reader`, `us_exchange` |
| `auth` | `auth`, `login`(새) |
| `account` | `account`, `holdings` |
| `market` | `ranking`, `stock_search`, `quote` |
| `order` | `order`, `open_order`, `auto_sell`, `alerts`(새) |
| `fill` | `feed`(← `fill_feed`), `watch`(← `fill_watch`) |
| `sl_tp` | `sl_tp`, `watch`(← `sl_tp_watch`), `alerts`(← `sl_tp_alerts`) |

- 폴더 이름은 CONTEXT.md의 절과 `web/src/features/`에 맞춘다. 순위와 종목 검색은 각자 파일 하나라 `market` 하나로 묶는다.
- `open_order`는 화면에서는 계좌 확인 안에 나오지만, `OrderService`가 취소를 다시 확인할 때 쓰고 CONTEXT.md에서도 "주문" 절이라 `order`에 둔다.
- `fill_feed`는 키움 실시간 데이터를 읽지만 쓰는 곳이 체결 알림과 SL / TP라 `fill`에 둔다.
- 파일 이름은 폴더 이름이 앞에 붙은 것만 줄인다. 폴더와 이름이 똑같은 것(`kiwoom/kiwoom`, `order/order` 등)은 그 폴더의 대표 module로 보고 그대로 둔다.
- 폴더마다 빈 `__init__.py`를 둔다. 다시 내보내기(re-export)는 하지 않는다.
- logger 이름은 바꾸지 않는다(`"stock_bot.fill_watch"` 등). logger 이름은 파일 경로가 아니라 로그를 찾는 이름표다. `app`의 `"stock_bot.api"`가 이미 그렇다.
- `config`가 한 단계 깊어지므로 `PROJECT_ROOT`의 계산을 한 단계 더 올라가게 고친다. 폴더 이동 커밋에서 내용이 바뀌는 곳은 이 한 줄뿐이다.
- 서버 실행 경로 `stock_bot.app:create_app`은 그대로다.

**테스트 폴더**

| 폴더 | 테스트 |
|---|---|
| (맨 위) | `conftest`, `fake_kiwoom`, `fake_realtime`, `log_capture`, `fake_discord`(새), `support`(새) |
| `core` | `test_environments`, `test_notification` |
| `kiwoom` | `test_kiwoom_pacing`, `test_kiwoom_robustness`, `test_realtime` |
| `auth` | `test_auth`, `test_login_notifications` |
| `account` | `test_domestic_account`, `test_us_account`, `test_holdings` |
| `market` | `test_rankings`, `test_stock_search`, `test_quote` |
| `order` | `test_orders`, `test_sell_orders`, `test_cancel_orders`, `test_us_cancel_orders`, `test_open_orders`, `test_us_open_orders`, `test_cancel_notifications`, `test_auto_sell`, `test_order_switch` |
| `fill` | `test_fill_notifications` |
| `sl_tp` | `test_sl_tp`, `test_sl_tp_auto_sell`, `test_sl_tp_burst`, `test_sl_tp_holdings`, `test_sl_tp_notifications`, `test_sl_tp_sessions`, `test_sl_tp_us` |

- 하위 폴더마다 `__init__.py`를 둔다. 테스트가 `tests.` 꼴로 서로를 불러 쓰기 때문이다.
- `test_order_switch`는 관문이 키움 client에 있지만(ADR 0003), 수동 주문·자동 매도·취소가 모두 막히는지를 보므로 `order`에 둔다.

**테스트 도우미 정리**

- 다른 테스트 파일이 쓰는 도우미만 옮긴다. 자기 파일 안에서만 쓰는 도우미는 그대로 둔다.

| 옮길 곳 | 내용 |
|---|---|
| `fake_kiwoom` | `DOMESTIC_PAPER_ENV`, `PAPER_ENV`, `REAL_ENV`, `US_PAPER_ENV` (`FAKE_ENV` 옆) |
| `fake_realtime` | `REPLACED` |
| `fake_discord`(새) | `FakeDiscord`, `SlowDiscord`, `DISCORD_ENV`, `fields`, 알림 색 상수 |
| `support`(새) | `wait_until` |
| `sl_tp/helpers`(새) | `test_sl_tp_auto_sell`, `test_sl_tp_us`, `test_sl_tp_sessions`이 내주던 도우미 전부(`Clock`, `sl_tp_kiwoom`, `running`, `watching`, `write_settings`, `us_*`, `nth_call_gate` 등) |
| `order/helpers`(새) | `test_cancel_orders`, `test_open_orders`, `test_us_open_orders`, `test_orders`가 내주던 도우미 |
| `fill/helpers`(새) | `fill_kiwoom` |

- `FakeDiscord`는 체결 알림 쪽 것(응답을 차례로 주고, 다 쓰면 200)을 공통으로 쓴다. 로그인·취소 알림 테스트도 이것을 쓰고, 늘 같은 상태 코드를 주던 쓰임은 같은 동작이 되게 고친다.
- `test_notification`의 `FakeDiscord`는 그대로 둔다. 알림 전송 자체를 시험하며 요청 헤더를 보는 다른 용도이고, 그 파일 안에서만 쓴다.
- `test_realtime`의 `Clock`은 시작 시각이 다른 별개 도우미라 그대로 둔다.

**진행**

- branch `refactor/app-cleanup` 하나, 티켓 없음, 커밋은 다음 순서로 나눈다. PR은 마지막에 하나 연다.
  1. app.py 정리
  2. 폴더 이동: 소스와 테스트를 옮기고 import만 고친다. `PROJECT_ROOT` 한 줄을 포함한다.
  3. 테스트 도우미 정리
  4. 리뷰 반영

## Testing Decisions

- **좋은 테스트는 바깥에서 보이는 결과만 본다.** HTTP 응답, 가짜 키움·Discord 서버에 나간 요청, 로그 이벤트가 그 결과다. 이번 작업은 동작을 바꾸지 않으므로 테스트의 기대값도 바꾸지 않는다.
- **seam은 지금 것을 쓴다.** 테스트는 `create_app(...)`에 가짜 transport·notifier·실시간 연결을 넣고 HTTP로 부른다. 새 seam을 만들지 않는다. module 안쪽 함수를 patch하는 테스트를 새로 만들지 않는다.
- **커밋 ①**: 로그인·출처 확인·로그인 없는 요청 거부·잘못된 환경 이름·취소 알림 테스트(`test_auth`, `test_login_notifications`, `test_cancel_notifications`, `test_environments`)가 기대값을 바꾸지 않고 통과해야 한다. 거부 응답에 요청 ID가 붙는지는 지금 테스트가 확인한다.
- **커밋 ① 새 테스트**: HTTP 요청의 로그에 투자 환경이 남는지 본다. 지금은 HTTP 쪽 로그의 `env`를 보는 테스트가 없다.
  - 국내 모의와 미국 모의에서(parametrize) 각각, 키움 호출 로그와 `api_error` 로그의 `env`가 그 환경이다.
  - app.py를 고치기 전에 먼저 넣어 지금 코드에서 통과하는지 확인한다.
- **커밋 ②**: import 경로와 `conftest`의 patch 대상(`app_module.DATA_DIR`, `order.RECHECK_RETRY_SECONDS`, `kiwoom.CALL_INTERVAL_SECONDS`)만 고친다. `PROJECT_ROOT`는 기존 `test_env_file_path_is_project_root`(`pyproject.toml`이 있는 폴더인지 확인)가 그대로 통과해야 한다. 새 테스트는 더하지 않는다.
- **커밋 ③**: 테스트 수와 기대값이 그대로여야 한다. 공통 `FakeDiscord`로 바꾼 로그인·취소 알림 테스트도 같은 것을 확인한다.
- **매 커밋마다** 전체 테스트, `ruff check`, `ruff format --check`, `mypy`(strict)를 돌린다. 지금 테스트는 742개이고, 끝나면 744개다(커밋 ①의 새 테스트 2개).
- **실제 서버는 띄우지 않는다.** 진짜 `.env`로 띄우면 키움 실시간 연결(실전 포함)이 열린다. 대신 `python -c "import stock_bot.app"`로 import를 확인하고, merge 뒤 사용자가 평소처럼 띄워 확인한다.
- **화면 build·e2e는 돌리지 않는다.** `web/`을 바꾸지 않고, e2e는 화면 개발 서버만 띄워 서버 코드에 닿지 않는다.

## Out of Scope

- `web/` 변경
- route를 기능별 `APIRouter` 파일로 나누기(Q1에서 고르지 않은 안)
- 서비스 조립을 `app.py` 밖으로 빼기
- 폴더와 이름이 똑같은 module의 이름 바꾸기(`kiwoom/kiwoom` → `client` 등)
- logger 이름을 새 경로에 맞추기
- `test_notification`의 `FakeDiscord`와 `test_realtime`의 `Clock` 합치기
- CONTEXT.md 변경(새 용어 없음), ADR(쉽게 되돌릴 수 있는 결정)

## Further Notes

- 아키텍처 점검(2026-10-27)의 마지막 후보 06이다. 04(PR #18) 다음에 한다.
- `.scratch/`의 이전 spec과 티켓은 그때의 파일 이름(`fill_watch.py` 등)을 그대로 둔다. 기록이기 때문이다. ADR 0003의 `config.py`도 파일 이름이 같아 그대로 둔다.
- 결정 기록(grilling, 2026-10-29)
  - Q1 app.py: 로그인 덩어리와 취소 알림만 빼고, 되풀이는 dependency·도우미로 줄임. 얇은 route는 그대로
  - Q2 폴더 기준: 기능별(CONTEXT.md 절, 화면 기능 폴더에 맞춤)
  - Q3 테스트: 소스와 같은 폴더로 나눔
  - Q4 `web/`: 범위 밖
  - Q5 진행: spec 하나, 티켓 없음, branch 하나, PR 하나
  - Q6 소스 배치: 위 표. 기반은 `core`
  - Q6-1 `config`: `core`로 옮기고 `PROJECT_ROOT` 한 줄 고침. 기존 경로 테스트로 확인
  - Q7 파일 이름: 폴더 이름이 앞에 붙은 것만 줄임
  - Q8 로그인: `install_login(app, auth, notify)`, `notify`는 `create_app`에
  - Q9 취소 알림은 `order/alerts`, `_kst`는 `notification`
  - Q10 logger 이름: 그대로
  - Q11 테스트 배치: 위 표
  - Q12 테스트 도우미: 이번에 정리
  - Q13 도우미 위치: 공통은 `tests/` 맨 위, 기능 전용은 폴더별 `helpers`
  - Q14 `FakeDiscord`: 체결·로그인 쪽 둘만 합침
  - Q15 커밋 순서: app.py → 폴더 이동 → 도우미 정리 → 리뷰 반영
  - Q16 실제 서버 기동: 하지 않음, merge 뒤 사용자 확인

## Review Log

### 2026-10-29: Codex review
- ✅ 환경 dependency의 비동기 실행 조건 누락: dependency를 `async def`로 쓴다고 적고, 커밋 ①에 HTTP 로그의 `env`를 보는 테스트(국내·미국 모의)를 정리 전에 먼저 넣기로 함.
- ✅ 새로 추가할 경로 테스트가 이미 존재: 기존 `test_env_file_path_is_project_root`를 그대로 쓰고 새 테스트를 빼기로 함. 최종 테스트 수를 744개로 고침.
