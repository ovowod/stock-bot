import { expect, test, type Page, type Route } from "@playwright/test";

// 브라우저의 /api 요청을 가로채 가짜 응답을 준다. 키움 서버나 실전 서버는 호출되지 않는다.

const DOMESTIC_ITEM = {
  rank: 1,
  code: "000660",
  name: "SK하이닉스",
  exchange: null,
  price: 1_841_000,
  direction: "up",
  change_rate: 0.44,
  trading_value: 5_359_250_000_000,
  previous_rank: 2,
};

const US_ITEM = {
  rank: 1,
  code: "SOXL",
  name: "미국 반도체 3배 디렉시온 ETF",
  exchange: "NYSE",
  price: 162.6,
  direction: "down",
  change_rate: -0.68,
  trading_value: 104_125_000,
  previous_rank: null,
};

const ranking = (environment: string, kind: string, items: unknown[], exchange = "all") => ({
  environment,
  market: environment.startsWith("us") ? "us" : "domestic",
  kind,
  exchange,
  fetched_at: "2026-10-05T06:30:00+00:00",
  items,
});

type Reply = { status?: number; body: unknown; delayMs?: number };
type Seen = {
  environment: string;
  kind: string;
  query: URLSearchParams;
  startedAt: number;
  endedAt?: number;
};

/** 순위 요청을 가로챈다. handler가 응답을 정하고, 요청 순서는 seen에 남는다. */
async function mockRankings(page: Page, handler: (request: Seen) => Reply) {
  const seen: Seen[] = [];
  await page.route("**/api/environments/*/rankings/*", async (route: Route) => {
    const url = new URL(route.request().url());
    const [, , , environment, , kind] = url.pathname.split("/");
    const request: Seen = { environment, kind, query: url.searchParams, startedAt: Date.now() };
    seen.push(request);
    const reply = handler(request);
    if (reply.delayMs) await new Promise((resolve) => setTimeout(resolve, reply.delayMs));
    await route.fulfill({ status: reply.status ?? 200, json: reply.body }).catch(() => {});
    request.endedAt = Date.now();
  });
  // 처음 화면(계좌 확인)의 요청은 순위 테스트와 관계없으므로 오류로 둔다.
  await page.route("**/api/environments/*/account", (route) =>
    route.fulfill({ status: 503, json: { error: { kind: "config_error", message: "test" } } }),
  );
  return seen;
}

async function openRanking(page: Page) {
  await page.goto("/");
  await selectRankingMenu(page);
}

/** 모바일에서는 메뉴 서랍을 연 뒤 순위를 고른다. */
async function selectRankingMenu(page: Page) {
  const menu = page.getByRole("button", { name: "메뉴 열기" });
  if (await menu.isVisible()) await menu.click();
  await page.getByRole("button", { name: "순위" }).filter({ visible: true }).click();
}

const card = (page: Page, title: string) => page.getByRole("region", { name: title });

async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  expect(await page.evaluate(() => window.innerWidth)).toBe(page.viewportSize()!.width);
}

test("처음 화면은 계좌 확인이고, 시세 → 순위 메뉴로 거래대금 상위를 본다", async ({ page }, info) => {
  await mockRankings(page, ({ environment, kind }) => ({ body: ranking(environment, kind, [DOMESTIC_ITEM]) }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "계좌 확인" })).toBeVisible();

  await openRanking(page);
  const trading = card(page, "거래대금 상위");
  await expect(trading).toContainText("SK하이닉스");
  await expect(trading).toContainText("000660");
  await expect(trading).toContainText("1,841,000원");
  await expect(trading).toContainText("+0.44%");
  await expect(trading).toContainText("5.36조");
  await expect(trading).toContainText("전일 2위");
  await expect(trading.locator("[data-direction=up]")).toHaveClass(/text-gain/);
  await expect(page.getByText(/조회$/)).toBeVisible();
  await expectNoHorizontalScroll(page);
  await page.screenshot({ path: `screenshots/${info.project.name}-ranking.png`, fullPage: true });
});

test("미국 환경은 미국 거래소 선택지와 USD, 거래소를 보여준다", async ({ page }) => {
  await mockRankings(page, ({ environment, kind }) => ({ body: ranking(environment, kind, [US_ITEM]) }));
  await openRanking(page);
  await page.getByRole("radio", { name: "미국 모의" }).click();

  const exchanges = page.getByRole("radiogroup", { name: "거래소" });
  await expect(exchanges.getByRole("radio")).toHaveText(["전체", "NYSE", "NASDAQ", "AMEX"]);
  const trading = card(page, "거래대금 상위");
  await expect(trading).toContainText("SOXL · NYSE");
  await expect(trading).toContainText("$162.60");
  await expect(trading).toContainText("−0.68%");
  await expect(trading).toContainText("$104.1M");
});

test("거래소를 바꾸면 카드가 바로 skeleton이 되고 새 조건으로 조회한다", async ({ page }) => {
  const seen = await mockRankings(page, ({ environment, kind, query }) => ({
    body: ranking(environment, kind, [
      { ...DOMESTIC_ITEM, name: query.get("exchange") === "kospi" ? "코스피종목" : "SK하이닉스" },
    ]),
    delayMs: query.get("exchange") === "kospi" ? 1000 : 0,
  }));
  await openRanking(page);
  await expect(card(page, "거래대금 상위")).toContainText("SK하이닉스");

  await page.getByRole("radio", { name: "코스피" }).click();
  await expect(page.getByRole("status", { name: "거래대금 상위 불러오는 중" })).toBeVisible();
  await expect(card(page, "거래대금 상위")).not.toContainText("SK하이닉스");
  await expect(card(page, "거래대금 상위")).toContainText("코스피종목");
  expect(seen.at(-1)!.query.get("exchange")).toBe("kospi");
});

test("새로고침 중에는 기존 목록을 유지하고 버튼을 비활성화한다", async ({ page }) => {
  let slow = false;
  await mockRankings(page, ({ environment, kind }) => ({
    body: ranking(environment, kind, [DOMESTIC_ITEM]),
    delayMs: slow ? 800 : 0,
  }));
  await openRanking(page);
  await expect(page.getByRole("button", { name: "새로고침" })).toBeEnabled();

  slow = true;
  await page.getByRole("button", { name: "새로고침" }).click();
  await expect(page.getByRole("button", { name: "새로고침 중" })).toBeDisabled();
  await expect(card(page, "거래대금 상위")).toContainText("SK하이닉스");
  await expect(page.getByRole("button", { name: "새로고침", exact: true })).toBeEnabled();
});

test("조회에 실패하면 카드에 오류와 다시 시도를 보여준다", async ({ page }, info) => {
  let failing = true;
  await mockRankings(page, ({ environment, kind }) =>
    failing
      ? {
          status: 429,
          body: {
            error: {
              kind: "rate_limited",
              message: "키움 API 호출 한도를 넘었습니다. 잠시 후 다시 시도하세요. [1700]",
              request_id: "r1",
            },
          },
        }
      : { body: ranking(environment, kind, [DOMESTIC_ITEM]) },
  );
  await openRanking(page);

  const trading = card(page, "거래대금 상위");
  await expect(trading.getByRole("alert")).toContainText("호출 한도를 넘었습니다");
  await page.screenshot({ path: `screenshots/${info.project.name}-ranking-error.png`, fullPage: true });

  failing = false;
  await trading.getByRole("button", { name: "다시 시도" }).click();
  await expect(trading).toContainText("SK하이닉스");
});

test("순위가 없으면 빈 상태를 안내한다", async ({ page }) => {
  await mockRankings(page, ({ environment, kind }) => ({ body: ranking(environment, kind, []) }));
  await openRanking(page);

  await expect(card(page, "거래대금 상위")).toContainText("순위 데이터가 없습니다");
});

test("값이 없으면 –, 알 수 없는 등락 방향은 중립으로 표시한다", async ({ page }) => {
  const item = { ...DOMESTIC_ITEM, price: null, trading_value: null, direction: "unknown", change_rate: 1.5 };
  await mockRankings(page, ({ environment, kind }) => ({ body: ranking(environment, kind, [item]) }));
  await openRanking(page);

  const trading = card(page, "거래대금 상위");
  await expect(trading).toContainText("SK하이닉스");
  const change = trading.locator("[data-direction=unknown]");
  await expect(change).toHaveText("1.50%");
  await expect(change).toHaveClass(/text-muted/);
  await expect(trading.getByText("–").first()).toBeVisible();
});

test("방향을 알 수 없어도 음수 등락률은 − 부호를 유지한다", async ({ page }) => {
  const item = { ...DOMESTIC_ITEM, direction: "unknown", change_rate: -1.5 };
  await mockRankings(page, ({ environment, kind }) => ({ body: ranking(environment, kind, [item]) }));
  await openRanking(page);

  const change = card(page, "거래대금 상위").locator("[data-direction=unknown]");
  await expect(change).toHaveText("−1.50%");
  await expect(change).toHaveClass(/text-muted/);
});

test("투자 환경을 바꾸면 이전 시장의 순위가 바로 사라지고 늦은 응답은 무시한다", async ({ page }) => {
  await mockRankings(page, ({ environment, kind }) =>
    environment === "domestic_paper"
      ? { body: ranking(environment, kind, [DOMESTIC_ITEM]), delayMs: 1500 }
      : { body: ranking(environment, kind, [US_ITEM]) },
  );
  await openRanking(page);
  await expect(page.getByRole("status", { name: "거래대금 상위 불러오는 중" })).toBeVisible();

  await page.getByRole("radio", { name: "미국 모의" }).click();
  await expect(card(page, "거래대금 상위")).toContainText("SOXL");
  await page.waitForTimeout(1700);
  await expect(card(page, "거래대금 상위")).not.toContainText("SK하이닉스");
});

test("상승률은 등락률을 크게, 거래량은 거래량을 짧게 보여준다", async ({ page }) => {
  const item = { ...DOMESTIC_ITEM, change_rate: 29.96, volume: 505_027_412 };
  await mockRankings(page, ({ environment, kind }) => ({ body: ranking(environment, kind, [item]) }));
  await openRanking(page);

  await expect(card(page, "상승률 상위").locator("[data-direction=up]").last()).toHaveText("▲+29.96%");
  await expect(card(page, "거래량 상위")).toContainText("5.05억주");
});

test("카드는 위에서부터 한 번에 하나씩 조회한다", async ({ page }) => {
  const seen = await mockRankings(page, ({ environment, kind }) => ({
    body: ranking(environment, kind, [DOMESTIC_ITEM]),
    delayMs: 300,
  }));
  await openRanking(page);
  await expect(card(page, "거래량 상위")).toContainText("SK하이닉스");

  expect(seen.map((r) => r.kind)).toEqual(["trading_value", "gainers", "volume", "popular"]);
  for (let i = 1; i < seen.length; i++) {
    expect(seen[i].startedAt).toBeGreaterThanOrEqual(seen[i - 1].endedAt!);
  }
});

test("한 카드가 실패해도 나머지는 보이고, 다시 시도는 그 카드만 요청한다", async ({ page }) => {
  let failing = true;
  const seen = await mockRankings(page, ({ environment, kind }) =>
    kind === "gainers" && failing
      ? {
          status: 502,
          body: { error: { kind: "kiwoom_error", message: "키움 오류 [1511] 테스트", request_id: "g1" } },
        }
      : { body: ranking(environment, kind, [DOMESTIC_ITEM]) },
  );
  await openRanking(page);

  await expect(card(page, "상승률 상위").getByRole("alert")).toContainText("키움 API 오류");
  await expect(card(page, "거래대금 상위")).toContainText("SK하이닉스");
  await expect(card(page, "거래량 상위")).toContainText("SK하이닉스");

  failing = false;
  const before = seen.length;
  await card(page, "상승률 상위").getByRole("button", { name: "다시 시도" }).click();
  await expect(card(page, "상승률 상위")).toContainText("SK하이닉스");
  expect(seen.slice(before).map((r) => r.kind)).toEqual(["gainers"]);
});

test("거래소를 바꾸면 세 카드를 새 조건으로 다시 조회한다", async ({ page }) => {
  const seen = await mockRankings(page, ({ environment, kind }) => ({
    body: ranking(environment, kind, [DOMESTIC_ITEM]),
  }));
  await openRanking(page);
  await expect(card(page, "거래량 상위")).toContainText("SK하이닉스");

  const before = seen.length;
  await page.getByRole("radio", { name: "코스닥" }).click();
  await expect(card(page, "거래량 상위")).toContainText("SK하이닉스");
  const after = seen.slice(before);
  expect(after.map((r) => r.kind)).toEqual(["trading_value", "gainers", "volume"]);
  expect(after.every((r) => r.query.get("exchange") === "kosdaq")).toBe(true);
});

test("조회 중인 카드가 있으면 새로고침 버튼을 비활성화한다", async ({ page }) => {
  await mockRankings(page, ({ environment, kind }) => ({
    body: ranking(environment, kind, [DOMESTIC_ITEM]),
    delayMs: kind === "volume" ? 1500 : 0,
  }));
  await openRanking(page);

  await expect(card(page, "거래대금 상위")).toContainText("SK하이닉스");
  await expect(page.getByRole("button", { name: "새로고침" })).toBeDisabled();
  await expect(card(page, "거래량 상위")).toContainText("SK하이닉스");
  await expect(page.getByRole("button", { name: "새로고침" })).toBeEnabled();
});

const POPULAR_ITEMS = [
  { ...DOMESTIC_ITEM, rank: 1, name: "삼성전자", code: "005930", rank_change: 0 },
  { ...DOMESTIC_ITEM, rank: 2, name: "성호전자", code: "043260", rank_change: 3 },
  { ...DOMESTIC_ITEM, rank: 3, name: "SK하이닉스", code: "000660", rank_change: -2 },
];

test("인기 종목은 기준 시각, 전체 거래소, 순위 변동을 보여준다", async ({ page }, info) => {
  await mockRankings(page, ({ environment, kind }) => ({
    body:
      kind === "popular"
        ? { ...ranking(environment, kind, POPULAR_ITEMS), period: "1h", base_time: "2026-10-05T17:00:00+09:00" }
        : ranking(environment, kind, [DOMESTIC_ITEM]),
  }));
  await openRanking(page);

  const popular = card(page, "인기 종목");
  await expect(popular).toContainText("전체 거래소 · 17:00 기준가");
  await expect(popular.getByRole("radio", { name: "1시간" })).toHaveAttribute("aria-checked", "true");
  await expect(popular.locator("[data-rank-change='3']")).toHaveText("▲3");
  await expect(popular.locator("[data-rank-change='-2']")).toHaveText("▼2");
  await expect(popular.locator("[data-rank-change='0']")).toHaveText("−");
  await expectNoHorizontalScroll(page);
  await page.screenshot({ path: `screenshots/${info.project.name}-ranking-all.png`, fullPage: true });
});

test("집계 시각이 없으면 기준 표시를 숨긴다", async ({ page }) => {
  await mockRankings(page, ({ environment, kind }) => ({
    body: kind === "popular" ? { ...ranking(environment, kind, []), period: "1h", base_time: null } : ranking(environment, kind, []),
  }));
  await openRanking(page);

  const popular = card(page, "인기 종목");
  await expect(popular).toContainText("순위 데이터가 없습니다");
  await expect(popular).toContainText("전체 거래소");
  await expect(popular).not.toContainText("기준가");
});

test("집계 구간을 바꾸면 인기 종목만 다시 조회하고, 거래소를 바꿔도 인기 종목은 그대로다", async ({ page }) => {
  const seen = await mockRankings(page, ({ environment, kind, query }) => ({
    body:
      kind === "popular"
        ? { ...ranking(environment, kind, POPULAR_ITEMS), period: query.get("period"), base_time: null }
        : ranking(environment, kind, [DOMESTIC_ITEM]),
    delayMs: query.get("period") === "today" ? 800 : 0,
  }));
  await openRanking(page);
  await expect(card(page, "인기 종목")).toContainText("성호전자");
  expect(seen.map((r) => r.kind)).toEqual(["trading_value", "gainers", "volume", "popular"]);
  expect(seen.at(-1)!.query.get("period")).toBe("1h");

  let before = seen.length;
  await card(page, "인기 종목").getByRole("radio", { name: "당일" }).click();
  await expect(page.getByRole("status", { name: "인기 종목 불러오는 중" })).toBeVisible();
  await expect(card(page, "인기 종목")).toContainText("성호전자");
  expect(seen.slice(before).map((r) => [r.kind, r.query.get("period")])).toEqual([["popular", "today"]]);

  before = seen.length;
  await page.getByRole("radio", { name: "코스피" }).click();
  await expect(card(page, "거래량 상위")).toContainText("SK하이닉스");
  expect(seen.slice(before).map((r) => r.kind)).toEqual(["trading_value", "gainers", "volume"]);
  await expect(card(page, "인기 종목")).toContainText("성호전자");
});

// 실제 서버 확인용. 백엔드를 띄운 상태에서 `pnpm test:live`로만 실행한다. 모의 환경만 호출한다.
for (const label of ["국내 모의", "미국 모의"] as const) {
  test(`@live ${label} 순위 실제 모의 서버 조회`, async ({ page }, info) => {
    await page.goto("/");
    await page.getByRole("radio", { name: label }).click();
    await selectRankingMenu(page);
    for (const title of ["거래대금 상위", "상승률 상위", "거래량 상위", "인기 종목"]) {
      await expect(card(page, title).getByRole("listitem").first()).toBeVisible({ timeout: 15_000 });
      await expect(card(page, title).getByRole("alert")).toHaveCount(0);
    }
    await page.screenshot({ path: `screenshots/live-${info.project.name}-ranking-${label}.png`, fullPage: true });
  });
}
