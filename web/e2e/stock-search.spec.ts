import { expect, test, type Page, type Route } from "@playwright/test";
import { openDashboard } from "./support";

// 브라우저의 /api 요청을 가로채 가짜 응답을 준다. 키움 서버나 실전 서버는 호출되지 않는다.

const domesticItem = (code: string, name: string, extra: Record<string, unknown> = {}) => ({
  code,
  name,
  english_name: null,
  exchange: null,
  category: "코스피",
  industry: "전기전자",
  status: null,
  is_etf: null,
  ...extra,
});

const US_ITEM = {
  code: "AAPL",
  name: "애플",
  english_name: "APPLE INC",
  exchange: "NASDAQ",
  category: null,
  industry: "컴퓨터 및 전자장비",
  status: null,
  is_etf: false,
};

const result = (environment: string, query: string, items: unknown[], extra: Record<string, unknown> = {}) => ({
  environment,
  market: environment.startsWith("us") ? "us" : "domestic",
  query,
  total: items.length,
  truncated: false,
  list_fetched_at: "2026-10-05T08:00:00+00:00",
  items,
  ...extra,
});

type Reply = { status?: number; body: unknown; delayMs?: number };
type Seen = { environment: string; query: string };

async function mockSearch(page: Page, handler: (request: Seen) => Reply) {
  const seen: Seen[] = [];
  await page.route("**/api/environments/*/stocks?*", async (route: Route) => {
    const url = new URL(route.request().url());
    const request = { environment: url.pathname.split("/")[3], query: url.searchParams.get("q") ?? "" };
    seen.push(request);
    const reply = handler(request);
    if (reply.delayMs) await new Promise((resolve) => setTimeout(resolve, reply.delayMs));
    await route.fulfill({ status: reply.status ?? 200, json: reply.body }).catch(() => {});
  });
  // 처음 화면(계좌 확인)의 요청은 이 테스트와 관계없으므로 오류로 둔다.
  await page.route("**/api/environments/*/account", (route) =>
    route.fulfill({ status: 503, json: { error: { kind: "config_error", message: "test" } } }),
  );
  return seen;
}

async function openSearch(page: Page) {
  await openDashboard(page);
  await selectSearchMenu(page);
}

/** 모바일에서는 메뉴 서랍을 연 뒤 종목 검색을 고른다. */
async function selectSearchMenu(page: Page) {
  const menu = page.getByRole("button", { name: "메뉴 열기" });
  if (await menu.isVisible()) await menu.click();
  await page.getByRole("button", { name: "종목 검색" }).filter({ visible: true }).click();
}

const input = (page: Page) => page.getByRole("searchbox", { name: "종목 검색어" });
const results = (page: Page) => page.getByRole("region", { name: "검색 결과" });

async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  expect(await page.evaluate(() => window.innerWidth)).toBe(page.viewportSize()!.width);
}

test("처음 화면은 계좌 확인이고, 시세 → 종목 검색에서 국내 종목을 찾는다", async ({ page }, info) => {
  await mockSearch(page, ({ environment, query }) => ({
    body: result(environment, query, [
      domesticItem("005930", "삼성전자"),
      domesticItem("000040", "KR모터스", { status: "관리종목", industry: "운송장비/부품" }),
      domesticItem("069500", "KODEX 200", { category: "ETF", industry: null }),
    ]),
  }));
  await openDashboard(page);
  await expect(page.getByRole("heading", { name: "계좌 확인" })).toBeVisible();

  await selectSearchMenu(page);
  await expect(page.getByText("찾을 종목을 입력하세요")).toBeVisible();
  await expect(input(page)).toHaveAttribute("placeholder", "종목명 또는 종목코드");

  await input(page).fill("삼성");
  await expect(results(page)).toContainText("삼성전자");
  await expect(results(page)).toContainText("005930 · 코스피 · 전기전자");
  await expect(results(page)).toContainText("관리종목");
  await expect(results(page)).toContainText("069500 · ETF");
  await expect(results(page)).toContainText("3개");
  await expectNoHorizontalScroll(page);
  await page.screenshot({ path: `screenshots/${info.project.name}-search.png`, fullPage: true });
});

test("미국 환경은 영문명, 티커, 거래소, ETF 표시를 보여준다", async ({ page }) => {
  await mockSearch(page, ({ environment, query }) => ({
    body: result(environment, query, [
      US_ITEM,
      { ...US_ITEM, code: "SPY", name: "S&P 500 SPDR ETF", english_name: "SPDR S&P 500", exchange: "NYSE", industry: null, is_etf: true },
    ]),
  }));
  await openSearch(page);
  await page.getByRole("radio", { name: "미국 모의" }).click();

  await expect(input(page)).toHaveAttribute("placeholder", "종목명(한글·영문) 또는 티커");
  await input(page).fill("apple");
  await expect(results(page)).toContainText("APPLE INC");
  await expect(results(page)).toContainText("AAPL · NASDAQ · 컴퓨터 및 전자장비");
  await expect(results(page).getByText("ETF", { exact: true })).toBeVisible();
});

test("입력을 멈춘 뒤 한 번만 요청하고, 검색어를 지우면 요청하지 않는다", async ({ page }) => {
  const seen = await mockSearch(page, ({ environment, query }) => ({
    body: result(environment, query, [domesticItem("000660", "SK하이닉스")]),
  }));
  await openSearch(page);

  await input(page).pressSequentially("하이닉", { delay: 50 });
  await expect(results(page)).toContainText("SK하이닉스");
  expect(seen.map((r) => r.query)).toEqual(["하이닉"]);

  await input(page).fill("");
  await expect(page.getByText("찾을 종목을 입력하세요")).toBeVisible();
  await page.waitForTimeout(500);
  expect(seen).toHaveLength(1);
});

test("결과가 없거나 50개로 잘리면 안내한다", async ({ page }) => {
  await mockSearch(page, ({ environment, query }) =>
    query === "없음"
      ? { body: result(environment, query, []) }
      : {
          body: result(
            environment,
            query,
            Array.from({ length: 50 }, (_, i) => domesticItem(`1000${String(i).padStart(2, "0")}`, `테스트${i}`)),
            { total: 120, truncated: true },
          ),
        },
  );
  await openSearch(page);

  await input(page).fill("없음");
  await expect(page.getByText("검색 결과가 없습니다")).toBeVisible();

  await input(page).fill("테스트");
  await expect(results(page)).toContainText("120개");
  await expect(results(page)).toContainText("50개까지만 표시합니다");
  await expect(results(page).getByRole("listitem")).toHaveCount(50);
});

test("검색 중에는 이전 결과를 유지하고 검색 중 표시를 보여준다", async ({ page }) => {
  await mockSearch(page, ({ environment, query }) => ({
    body: result(environment, query, [domesticItem("005930", query === "삼성" ? "삼성전자" : "삼성전자우")]),
    delayMs: query === "삼성전자우" ? 1000 : 0,
  }));
  await openSearch(page);
  await input(page).fill("삼성");
  await expect(results(page)).toContainText("삼성전자");

  await input(page).fill("삼성전자우");
  await expect(page.getByRole("status", { name: "검색 중" })).toBeVisible();
  await expect(results(page)).toHaveAttribute("aria-busy", "true");
  await expect(results(page)).toContainText("삼성전자우");
  await expect(page.getByRole("status", { name: "검색 중" })).toBeHidden();
});

test("검색에 실패하면 오류와 다시 시도를 보여준다", async ({ page }, info) => {
  let failing = true;
  await mockSearch(page, ({ environment, query }) =>
    failing
      ? {
          status: 429,
          body: {
            error: {
              kind: "rate_limited",
              message: "키움 API 호출 한도를 넘었습니다. 잠시 후 다시 시도하세요. [1700]",
              request_id: "s1",
            },
          },
        }
      : { body: result(environment, query, [domesticItem("005930", "삼성전자")]) },
  );
  await openSearch(page);
  await input(page).fill("삼성");

  await expect(page.getByRole("alert")).toContainText("호출 한도를 넘었습니다");
  await page.screenshot({ path: `screenshots/${info.project.name}-search-error.png`, fullPage: true });
  failing = false;
  await page.getByRole("button", { name: "다시 시도" }).click();
  await expect(results(page)).toContainText("삼성전자");
});

test("검색 중에 입력을 지우면 늦게 온 응답이 안내 화면을 덮지 않는다", async ({ page }) => {
  await mockSearch(page, ({ environment, query }) => ({
    body: result(environment, query, [domesticItem("005930", "삼성전자")]),
    delayMs: 800,
  }));
  await openSearch(page);

  await input(page).fill("삼성");
  await expect(page.getByRole("status", { name: "검색 중" })).toBeVisible();
  await page.waitForTimeout(450);
  await input(page).fill("");
  await page.waitForTimeout(800);
  await expect(page.getByText("찾을 종목을 입력하세요")).toBeVisible();
  await expect(results(page)).toHaveCount(0);
});

test("다른 검색어로 바꾸고 기다리는 동안 이전 응답은 반영하지 않는다", async ({ page }) => {
  await mockSearch(page, ({ environment, query }) => ({
    body: result(environment, query, [domesticItem("000000", `${query} 결과`)]),
    delayMs: query === "첫째" ? 500 : 900,
  }));
  await openSearch(page);

  await input(page).fill("첫째");
  await page.waitForTimeout(400);
  await input(page).fill("둘째");
  await page.waitForTimeout(600);
  await expect(page.getByText("첫째 결과")).toHaveCount(0);
  await expect(results(page)).toContainText("둘째 결과");
});

test("투자 환경을 바꾸면 검색어와 결과를 지운다", async ({ page }) => {
  await mockSearch(page, ({ environment, query }) => ({
    body: result(environment, query, [domesticItem("005930", "삼성전자")]),
  }));
  await openSearch(page);
  await input(page).fill("삼성");
  await expect(results(page)).toContainText("삼성전자");

  await page.getByRole("radio", { name: "국내 실전" }).click();
  await expect(input(page)).toHaveValue("");
  await expect(results(page)).toHaveCount(0);
  await expect(page.getByText("찾을 종목을 입력하세요")).toBeVisible();
});

// 실제 서버 확인용. 백엔드를 띄운 상태에서 `pnpm test:live`로만 실행한다. 모의 환경만 호출한다.
for (const [label, query, expected] of [
  ["국내 모의", "삼성전자", "005930"],
  ["미국 모의", "apple", "AAPL"],
] as const) {
  test(`@live ${label} 종목 검색 실제 모의 서버 조회`, async ({ page }, info) => {
    await openDashboard(page);
    await page.getByRole("radio", { name: label }).click();
    await selectSearchMenu(page);
    await input(page).fill(query);
    await expect(results(page)).toContainText(expected, { timeout: 20_000 });
    await expect(page.getByRole("alert")).toHaveCount(0);
    await page.screenshot({ path: `screenshots/live-${info.project.name}-search-${label}.png`, fullPage: true });
  });
}
