# Discord 채널 알림 보내기 조사

조사일: 2026-10-06. 공식 문서(docs.discord.com, discord/discord-api-docs GitHub, discord.py 공식 문서·source, httpx 공식 문서)만 근거로 삼았다.
공식 문서에서 확인하지 못한 내용은 **[미확인]** 으로 표시했다.

참고: 예전 주소 `discord.com/developers/docs/...`는 현재 `docs.discord.com/developers/...`로 301 redirect된다.
문서 원본은 GitHub `discord/discord-api-docs` repository(조사 시점 commit `c43598d`, 2026-10-02)다.

## 결론 요약

- 알림을 **보내기만** 할 거라면 **Channel Webhook**이 가장 간단하다. bot도, 토큰 header도, 권한 계산도 필요 없고 URL 하나로 끝난다.
- 나중에 Discord에서 slash command로 봇을 조작할 계획이 있다면 **Bot + REST API**로 시작하는 편이 이후 확장이 자연스럽다.
- 어느 쪽이든 Gateway(WebSocket) 연결 없이 HTTP 요청 하나로 메시지를 보낼 수 있다. 예전에는 "Gateway에 한 번은 연결해야 한다"는 제한이 있었지만 2021-06-08에 문서에서 제거됐다(아래 1.3 참고).
- 이 project는 이미 `httpx`를 쓰므로 `discord.py`(aiohttp 기반)를 추가할 필요 없이 `httpx.AsyncClient`로 충분하다.
- **사용자 결정 필요**: 어느 방식이든 새 secret이 하나 이상 필요하다. AGENTS.md 규칙상 `.env`에 새 변수를 추가하려면 사용자 승인이 필요하다(아래 6.2).

## 1. 두 가지 방식 비교

### 1.1 (a) Bot user + REST API

- Endpoint: `POST /channels/{channel.id}/messages` — "Post a message to a guild text or DM channel."
  (https://docs.discord.com/developers/resources/message#create-message)
- 인증 header: `Authorization: Bot <token>` (https://docs.discord.com/developers/reference#authentication)
- 필요한 권한: "When operating on a guild channel, the current user must have the `SEND_MESSAGES` permission."
  (https://docs.discord.com/developers/resources/message#create-message)
- `VIEW_CHANNEL`이 거부되면 `SEND_MESSAGES`도 사실상 무시된다: "Denying a user or a role `VIEW_CHANNEL` on a channel implicitly denies other permissions on the channel."
  (https://docs.discord.com/developers/topics/permissions#implicit-permissions)
  → 그래서 invite 시 `View Channel` + `Send Messages`를 함께 준다.

### 1.2 (b) Channel Webhook

- "Webhooks are a low-effort way to post messages to channels in Discord. They do not require a bot user or authentication to use."
  (https://docs.discord.com/developers/resources/webhook)
- Endpoint: `POST /webhooks/{webhook.id}/{webhook.token}`. `wait` query parameter에 따라 message object 또는 `204 No Content`를 돌려준다.
  (https://docs.discord.com/developers/resources/webhook#execute-webhook)
- `content`, `embeds`, `components`, `file`, `poll` 중 최소 하나는 있어야 한다. (같은 출처)
- `username`, `avatar_url`로 보낸 사람 이름·아이콘을 메시지마다 바꿀 수 있다. (같은 출처)
- API로 webhook을 만들 때는 `MANAGE_WEBHOOKS` 권한이 필요하고, 이름에 `clyde`, `discord`를 넣을 수 없다.
  (https://docs.discord.com/developers/resources/webhook#create-webhook)
- Discord 앱 UI에서 만드는 절차(채널 설정 → 연동(Integrations) → Webhooks → 새 Webhook → URL 복사): **[미확인]** support.discord.com 문서가 403으로 막혀 원문을 확인하지 못했다.

### 1.3 Bot이 REST로 메시지를 보내려면 Gateway 연결이 필요한가

**필요 없다(현재 기준).**

- 2017-10-23 commit `87cd6c4`("Clarify gateway requirements to send messages")에서 Create Message 문서에 다음 경고가 추가됐다:
  "Before using this endpoint, you must connect to and identify with a gateway at least once."
- 2021-06-08 commit `5f4decb`("document removal of message gateway limitation")에서 이 경고가 삭제됐다.
  (https://github.com/discord/discord-api-docs/commit/5f4decb7c488064d469188bc631e23fef7adf6ae)
- 현재 Create Message 문서에는 Gateway 관련 전제 조건이 없다. Gateway 문서도 "In *most* cases, performing REST operations on Discord resources can be done using the HTTP API rather than the Gateway API."라고 쓴다.
  (https://docs.discord.com/developers/events/gateway)
- 인터넷의 오래된 글에서 "봇은 최소 한 번 Gateway에 접속해야 메시지를 보낼 수 있다"는 말이 보이면 2021년 이전 정보다.
- Gateway에 접속하지 않으면 봇이 member 목록에서 offline으로 보인다는 점: **[미확인]** (공식 문서에서 명시 문장을 찾지 못함. 알림 기능에는 영향 없음.)

### 1.4 나중에 명령을 받고 싶을 때 (slash command / interactions)

- Interaction은 Gateway로 받거나 "outgoing webhook"(HTTP)으로 받을 수 있다. HTTP로 받으려면 app에 **Interactions Endpoint URL**을 설정해야 한다.
  (https://docs.discord.com/developers/interactions/receiving-and-responding#receiving-an-interaction)
- HTTP 방식은 요청마다 `X-Signature-Ed25519`, `X-Signature-Timestamp` header를 검증해야 한다.
  (https://docs.discord.com/developers/interactions/overview)
  → 외부에서 접근 가능한 공개 HTTPS 주소가 필요하다. 집/개인 Docker 환경이라면 Gateway 방식이 현실적이다.
- Channel Webhook은 메시지를 **보내기만** 할 수 있고 명령 수신과는 무관하다. 명령이 필요해지면 결국 Bot application을 만들어야 한다.
- `bot` scope를 고르면 `applications.commands` scope가 자동 포함된다.
  (https://docs.discord.com/developers/topics/oauth2#bot-authorization-flow)

### 1.5 비교표

| 항목 | (a) Bot + REST | (b) Channel Webhook |
|---|---|---|
| 준비 | Developer Portal에서 app 생성 → token 발급 → 서버에 invite → channel ID 확인 | 채널에서 webhook 생성 → URL 복사 |
| 필요한 secret | bot token + channel ID | webhook URL(안에 token 포함) 하나 |
| 인증 | `Authorization: Bot <token>` header | URL 자체가 인증. header 불필요 |
| 권한 관리 | 봇 role/채널 권한(View Channel, Send Messages) | webhook이 붙은 채널에만 게시 |
| 보낼 수 있는 곳 | 권한이 있는 모든 채널 | 그 webhook의 채널 한 곳(+ `thread_id`로 thread) |
| Gateway 필요 | 아니요(1.3) | 아니요 |
| 명령 수신 확장 | 같은 app에 slash command 추가 가능 | 불가. 별도 bot 필요 |
| 유출 시 피해 | token으로 봇 권한 내 모든 작업 가능 | 그 채널에 아무 메시지나 게시 가능 |
| 기본 mention 처리 | 모든 mention 파싱(`@everyone` 포함) | user mention만 파싱 |

mention 기본값 출처: https://docs.discord.com/developers/resources/message#allowed-mentions-object-default-settings-for-allowed-mentions

## 2. Bot 준비 절차

출처: https://docs.discord.com/developers/quick-start/getting-started

1. Developer Portal(https://discord.com/developers/applications)에서 **New Application** → 이름 입력 → Create.
2. **Bot** 페이지 → Token → **Reset Token**으로 bot token 발급.
   - "You won't be able to view your token again unless you regenerate it" — 한 번만 보이므로 바로 안전한 곳에 저장한다.
   - "Make sure to never share your token or check it into any kind of version control."
3. **Installation** 페이지 → Guild Install의 scope에 `bot` 추가 → Permissions에서 권한 선택 → install link로 내 서버에 추가.
   - 서버에 설치하려면 그 서버의 `MANAGE_GUILD` 권한이 있어야 한다. (같은 출처)
   - 혼자 쓸 봇이면 Bot 페이지에서 **Public Bot**을 끄면 "only you can add the bot to guilds".
     (https://docs.discord.com/developers/topics/oauth2#bot-authorization-flow)
4. invite URL을 직접 만들 수도 있다:
   `https://discord.com/oauth2/authorize?client_id=<APPLICATION_ID>&scope=bot&permissions=<정수>`
   (https://docs.discord.com/developers/topics/oauth2#bot-authorization-flow)
5. channel ID 확인: Discord 설정 → 고급(Advanced) → **Developer Mode** 켜기 → 채널 우클릭 → ID 복사.
   - Developer Mode 위치(Settings > Advanced > Developer Mode)는 공식 개발자 문서에 나온다:
     https://docs.discord.com/developers/social-layer/game-stats-widgets/widget-configuration
   - "채널 우클릭 → ID 복사" 단계 자체는 **[미확인]** (support.discord.com 403).

### 권한 bit 값

출처: https://docs.discord.com/developers/topics/permissions

| 권한 | bit | 10진수 |
|---|---|---|
| VIEW_CHANNEL | `1 << 10` | 1024 |
| SEND_MESSAGES | `1 << 11` | 2048 |
| EMBED_LINKS | `1 << 14` | 16384 |
| READ_MESSAGE_HISTORY | `1 << 16` | 65536 |
| MENTION_EVERYONE | `1 << 17` | 131072 |
| MANAGE_WEBHOOKS | `1 << 29` | 536870912 |
| SEND_MESSAGES_IN_THREADS | `1 << 38` | 274877906944 |

- 최소 권한: View Channel + Send Messages = `1024 + 2048 = 3072` → `permissions=3072`.
- embed로 보낼 거라면 Embed Links를 더해 `3072 + 16384 = 19456`.
  - 단, "봇이 보내는 embed에 `EMBED_LINKS`가 필수"라는 문장은 Create Message 문서에서 찾지 못했다 **[미확인]**. 문서상 `EMBED_LINKS` 설명은 "Links sent by users with this permission will be auto-embedded."이다. 안전하게 함께 주는 것을 권한다.

## 3. 메시지 payload

출처: https://docs.discord.com/developers/resources/message

- `content`: 최대 **2000자**.
- embed: 메시지당 최대 **10개**, 모든 embed 합계 **6000자**.
- embed 개별 제한: title 256, description 4096, fields 25개, field name 256, field value 1024, footer text 2048, author name 256.
  (https://docs.discord.com/developers/resources/message#embed-object-embed-limits)
- `allowed_mentions`: `parse`(`"users"`, `"roles"`, `"everyone"` 중 선택), `users`(최대 100), `roles`(최대 100), `replied_user`.
  - 모든 mention을 막으려면 `{"parse": []}`.
  - 알림 문구에 종목명 등 외부 문자열이 섞일 수 있으므로 `{"parse": []}`로 의도치 않은 `@everyone`을 막는 것을 권한다. (Webhook 문서도 "using `allowed_mentions` to prevent unexpected mentions"를 권한다: https://docs.discord.com/developers/resources/webhook#execute-webhook)

### 공통 HTTP 규칙

출처: https://docs.discord.com/developers/reference

- Base URL: `https://discord.com/api`, 현재 버전 **v10** → `https://discord.com/api/v10/...`
- User-Agent 필수 형식: `User-Agent: DiscordBot ($url, $versionNumber)`
  - "Client requests that do not have a valid User Agent specified may be blocked and return a Cloudflare error."
  - 예: `DiscordBot (https://github.com/<user>/stock-bot, 0.1.0)`. (discord.py도 `DiscordBot (https://github.com/Rapptz/discord.py {version}) Python/... aiohttp/...` 형태로 보낸다: https://github.com/Rapptz/discord.py/blob/master/discord/http.py)
  - 이 요구가 webhook 호출에도 적용되는지 문서는 구분하지 않는다. "Clients using the HTTP API"라고만 쓰므로 webhook에도 같은 header를 붙이는 것이 안전하다.

## 4. Rate limit

출처: https://docs.discord.com/developers/topics/rate-limits

- **값을 하드코딩하지 말 것**: "rate limits should not be hard coded into your app. Instead, your app should parse response headers".
- 응답 header: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`, `X-RateLimit-Reset-After`, `X-RateLimit-Bucket`, `X-RateLimit-Global`, `X-RateLimit-Scope`.
- 초과 시 **HTTP 429** + JSON body `message`, `retry_after`(초, float), `global`(bool), `code`(선택).
  "Your application should rely on the `Retry-After` header or `retry_after` field to determine when to retry the request."
- per-route limit은 top-level resource(channel_id, guild_id, webhook_id 또는 webhook_id+token)별로 따로 계산된다.
- **Global limit**: "All bots can make up to 50 requests per second to our API. If no authorization header is provided, then the limit is applied to the IP address."
  → 인증 header가 없는 webhook 호출은 IP 기준 global limit이 적용된다고 읽힌다.
- **Invalid request limit**: 10분에 10,000건. 401, 403, 429 응답이 해당되며, 넘으면 IP가 Cloudflare에 일시 차단된다. `X-RateLimit-Scope: shared`인 429는 세지 않는다.
- **Webhook 404**: "If a webhook returns a 404 status you should not attempt to use it again - repeated attempts to do so will result in a temporary restriction."
  → 삭제된 webhook에 계속 보내지 않도록, 404면 재시도하지 말고 로그만 남긴다.
- Webhook 전용 수치(예: "채널당 2초에 5건" 같은 숫자): 공식 문서에 없음 **[미확인]**. header를 읽어서 처리하면 된다.
- 개인용 알림(체결, 오류) 수준이면 limit에 걸릴 일은 드물다. 그래도 429를 받으면 `retry_after`만큼 기다렸다 한 번 재시도하는 정도면 충분하다.

## 5. Python 구현 선택지

### 5.1 discord.py

- 최신 2.7.1. `aiohttp<4,>=3.7.4`에 의존한다. PyPI classifier는 Python 3.8~3.12까지만 표기한다(3.14 미표기, `requires_python >=3.8`). (https://pypi.org/pypi/discord.py/json)
- `Client.start()`는 `login()` + `connect()`이고 `connect()`는 WebSocket(Gateway) 연결을 만든다. (https://discordpy.readthedocs.io/en/stable/api.html)
  → 상시 Gateway 연결을 FastAPI process 안에서 함께 돌려야 하므로 단방향 알림에는 과하다.
- Webhook만 쓰는 `Webhook.from_url(url, *, session: aiohttp.ClientSession, ...)`와 requests 기반 `SyncWebhook`도 있다.
  (https://github.com/Rapptz/discord.py/blob/master/discord/webhook/async_.py)
  → 그래도 aiohttp라는 HTTP client가 하나 더 들어온다.
- 장점: rate limit 처리, slash command, embed builder가 내장. slash command를 본격적으로 쓸 때 고려할 만하다.

### 5.2 httpx 직접 호출 (권장)

- project가 이미 `httpx>=0.28.1`을 쓴다(`pyproject.toml`). 의존성 추가가 없다.
- httpx 문서는 client를 매번 만들지 말고 하나를 재사용하라고 권한다: "make sure you're not instantiating multiple client instances - for example by using `async with` inside a 'hot loop'." (https://www.python-httpx.org/async/)

최소 예시. 이 project의 `logging_setup.log`와 `masking.secrets`를 그대로 쓴다. **아직 project에 넣은 코드가 아니며, 환경 변수 이름은 사용자 결정 전 가안이다.**

```python
"""Discord 채널로 알림을 보낸다. 실패해도 주문 흐름을 막지 않는다."""

import asyncio
import logging

import httpx

from stock_bot.logging_setup import log
from stock_bot.masking import secrets

logger = logging.getLogger("stock_bot.discord_notify")

API_BASE = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/<user>/stock-bot, 0.1.0)"
NO_MENTIONS = {"parse": []}


class DiscordNotifier:
    """(a) bot 방식. webhook 방식이면 url만 바꾸고 Authorization header를 뺀다."""

    def __init__(self, client: httpx.AsyncClient, bot_token: str, channel_id: str) -> None:
        secrets.add(bot_token)  # 로그·오류 메시지에서 token을 *** 로 가린다.
        self._client = client
        self._url = f"{API_BASE}/channels/{channel_id}/messages"
        self._headers = {"Authorization": f"Bot {bot_token}", "User-Agent": USER_AGENT}

    async def send(self, content: str) -> bool:
        payload = {"content": content[:2000], "allowed_mentions": NO_MENTIONS}
        for attempt in (1, 2):
            try:
                resp = await self._client.post(
                    self._url, json=payload, headers=self._headers, timeout=10
                )
            except httpx.HTTPError as exc:
                log(
                    logger,
                    logging.ERROR,
                    "discord.send.error",
                    attempt=attempt,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                return False
            if resp.status_code == 429 and attempt == 1:
                retry_after = float(resp.json().get("retry_after", 1.0))
                log(
                    logger,
                    logging.WARNING,
                    "discord.send.rate_limited",
                    retry_after=retry_after,
                    scope=resp.headers.get("X-RateLimit-Scope"),
                    is_global=resp.headers.get("X-RateLimit-Global"),
                )
                await asyncio.sleep(retry_after)
                continue
            ok = resp.is_success
            log(
                logger,
                logging.INFO if ok else logging.ERROR,
                "discord.send.response",
                attempt=attempt,
                status=resp.status_code,
                body=None if ok else resp.text[:500],
            )
            return ok
        return False
```

요점:

- token은 `secrets.add`로 등록해 두면 project의 `JsonFormatter`가 최종 로그 줄에서 `***`로 바꾼다. webhook 방식이면 webhook URL 전체(또는 token 부분)를 등록한다.
- header나 URL 자체는 로그에 남기지 않는다. 로그에는 status, attempt, retry_after, 오류 종류만 남긴다.
- 429는 `retry_after`만큼 기다렸다 한 번만 재시도한다. 401/403/404는 재시도하지 않는다(invalid request limit, webhook 404 제한).
- 알림 실패가 주문 처리를 실패로 만들지 않도록 예외를 삼키고 `bool`만 돌려준다. 호출 측에서는 응답을 기다리지 않게 background task로 보내는 것도 방법이다.
- 테스트는 AGENTS.md 규칙대로 실제 Discord에 보내지 말고 `httpx.MockTransport`로 가짜 응답(200, 429, 403)을 준다.

## 6. 이 project에 대한 권장

### 6.1 방식

**1단계는 Channel Webhook + httpx를 권한다.**

- 요구가 "체결·오류 알림을 채널에 보내기"라는 단방향이다. webhook은 secret 하나, 코드 몇십 줄로 끝난다.
- bot token이 유출되면 봇 권한 안의 모든 작업이 가능하지만, webhook URL 유출은 그 채널 게시로 피해가 한정된다(1.5 표). 유출됐다면 webhook을 삭제하고 새로 만들면 된다.
- webhook 기본 mention 처리가 user만 파싱이라 `@everyone` 사고 위험도 낮다(그래도 `{"parse": []}`를 명시).

**Bot 방식을 고를 이유:**

- 여러 채널(예: 체결 채널, 오류 채널)에 하나의 secret으로 보내고 싶을 때. webhook은 채널마다 URL이 하나씩 필요하다.
- 나중에 Discord에서 `/잔고` 같은 slash command로 조회·조작하고 싶을 때. 이 경우 Gateway 연결(discord.py 등) 또는 공개 HTTPS endpoint + 서명 검증이 추가로 필요하다(1.4).
- webhook으로 시작해도 나중에 bot으로 옮기는 비용은 작다. 메시지 payload 형식(content, embeds, allowed_mentions)이 같고 URL과 header만 다르다.

### 6.2 사용자 결정이 필요한 새 secret

AGENTS.md: "When adding or changing features, do not introduce new variables that must be added to `.env`." 따라서 아래는 **사용자 승인 후에만** 추가한다. 이름은 가안이다.

| 방식 | 필요한 값 | 가안 이름 | 성격 |
|---|---|---|---|
| Webhook | webhook URL | `DISCORD_WEBHOOK_URL` | secret (URL 안에 token 포함) |
| Bot | bot token | `DISCORD_BOT_TOKEN` | secret |
| Bot | channel ID | `DISCORD_CHANNEL_ID` | secret 아님(ID일 뿐) — 그래도 `.env`에 둔다면 새 변수 |

결정할 것:

1. webhook과 bot 중 무엇으로 할지.
2. 위 변수를 `.env`/`.env.example`에 추가해도 되는지.
3. 값이 비어 있을 때의 동작. 권장: 알림 기능만 끄고(로그 한 줄) 앱은 정상 실행. 알림은 거래 안전에 필수 기능이 아니므로 시작 실패로 만들 이유가 없다.
4. 거래 환경(real / paper KR / paper US)별로 채널을 나눌지, 메시지에 환경 이름만 붙일지.

## 7. Discord 개발자 정책과 주식·금융 정보

확인한 원문 (support-dev.discord.com은 WebFetch에 403이라 Zendesk Help Center API로 받았다):

- Discord Developer Policy, Effective date: July 8, 2024 — https://support-dev.discord.com/hc/en-us/articles/8563934450327-Discord-Developer-Policy
- Discord Developer Terms of Service, Effective date: July 8, 2024 — https://support-dev.discord.com/hc/en-us/articles/8562894815383-Discord-Developer-Terms-of-Service

### 적용 대상

Developer Terms는 "apply to your access to and use of the APIs"라고 한다. webhook 실행도 API endpoint이므로 bot과 webhook 모두 적용 대상으로 본다(Terms에 webhook을 따로 언급한 문장은 없다 — 해석).
개인용·비공개 application을 예외로 두는 조항은 없다.

### 주식·트레이딩 자체를 막는 조항은 없다

Developer Policy 8번이 금지하는 위험·불법 활동은 "Risks to physical safety; Environmental damage; Financial scams; or, Illegal online gambling."이다. 주식 매매, 투자, 트레이딩 bot을 금지하거나 제한하는 문구는 두 문서 어디에도 없다.

### 주의할 조항: 금융 정보 전송 (Policy 16번)

> "you may not, and may not use your Application to, obtain API Data or transmit data to Discord ... (ii) that includes protected health information, financial information, or other sensitive information under applicable law, except to the extent specifically allowed by our Terms for a given Discord service or if necessary to process a financial transaction as enabled by a Discord service."

- 즉 Discord로 **"financial information"을 보내는 것 자체가 금지 대상**이다.
- 두 문서 어디에도 "financial information"의 정의는 없다. "under applicable law"가 붙어 있어, 법으로 보호되는 금융 정보(계좌번호, 카드번호 등)를 가리키는 것으로 읽히지만 이는 해석이다 **[미확인]**.
- 본인 체결 내역(종목, 수량, 가격)이 여기 해당하는지는 원문만으로 판단할 수 없다 **[미확인]**. 한국 법(예: 신용정보법)상 해당 여부도 확인하지 않았다.

### 이 project에 주는 의미

- 보내지 말 것: 계좌번호, app key/secret, access token. 계좌번호는 이 조항에 해당할 가능성이 가장 높고, 원래도 AGENTS.md상 노출 금지 대상이다.
- 회색 지대: 체결 종목·수량·가격, 잔고·평가금액. 위험을 줄이려면 메시지를 "무슨 일이 일어났는지" 수준으로 줄이고(예: "국내 모의 매수 체결 1건", "주문 오류 발생"), 자세한 내용은 대시보드에서 보게 하는 방법이 있다.
- Policy 위반 시 Developer Terms의 "Enforcement Reasons"에 따라 API 접근이 중지·제한될 수 있다. 알림이 끊기는 것 외에 매매 기능에는 영향이 없도록 설계해야 한다(5.2).

## 미확인 항목 정리

- Discord 앱 UI에서 webhook을 만드는 정확한 메뉴 경로, 채널 ID 복사 메뉴 (support.discord.com 접근 403).
- 봇이 보내는 embed에 `EMBED_LINKS` 권한이 필수인지.
- Webhook 전용 rate limit 수치.
- Gateway 미접속 봇의 offline 표시 여부.
- discord.py의 Python 3.14 공식 지원 여부(classifier 미표기).
- Developer Policy 16번의 "financial information" 범위, 본인 체결 내역이 해당하는지(7절).
