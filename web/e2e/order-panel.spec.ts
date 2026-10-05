import { expect, test, type Page } from "@playwright/test";

// 브라우저의 /api 요청을 가로채 가짜 응답을 준다. 이 파일의 테스트는 주문을 보내지 않으며 키움 서버도 호출되지 않는다.

const rankingItem = (code: string, name: string, exchange: string | null, price: number) => ({
  rank: 1,
  code,
  name,
  exchange,
  price,
  direction: "up",
  change_rate: 1.5,
  trading_value: 1_000_000_000,
  previous_rank: 1,
  volume: 1000,
  rank_change: 0,
});

const searchItem = (extra: Record<string, unknown>) => ({
  code: "005930",
  name: "삼성전자",
  english_name: null,
  exchange: null,
  category: "코스피",
  industry: "전기전자",
  status: null,
  is_etf: null,
  ...extra,
});

// 순위 가격과 다른 현재가를 줘서, 가격 칸이 현재가 조회 결과로 채워지는지 구별한다.
const QUOTES: Record<string, number> = { "000660": 190_000, "005930": 61_300, SOXL: 170.25 };

type Reply = { status?: number; body?: unknown; delayMs?: number };
type QuoteRequest = { environment: string; code: string; exchange: string | null };

/** 순위·검색·현재가 요청에 환경별 가짜 응답을 준다. 그 밖의 요청은 기록한다. */
async function mockApi(
  page: Page,
  items: { domestic: unknown[]; us: unknown[] },
  searchItems: unknown[] = [],
  quote: (request: QuoteRequest) => Reply = ({ code }) =>
    code in QUOTES
      ? { body: { code, price: QUOTES[code], fetched_at: "2026-10-05T06:30:00+00:00" } }
      : { status: 502, body: { error: { kind: "kiwoom_error", message: "test" } } },
) {
  const unexpected: string[] = [];
  const quotes: QuoteRequest[] = [];
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const [, , , environment, resource, kind] = url.pathname.split("/");
    const market = environment?.startsWith("us") ? "us" : "domestic";
    if (resource === "quote") {
      const request = {
        environment,
        code: url.searchParams.get("code") ?? "",
        exchange: url.searchParams.get("exchange"),
      };
      quotes.push(request);
      const reply = quote(request);
      if (reply.delayMs) await new Promise((resolve) => setTimeout(resolve, reply.delayMs));
      await route.fulfill({ status: reply.status ?? 200, json: reply.body }).catch(() => {});
    } else if (resource === "rankings") {
      await route.fulfill({
        json: { environment, market, kind, exchange: "all", fetched_at: "2026-10-05T06:30:00+00:00", items: items[market] },
      });
    } else if (resource === "stocks") {
      const query = url.searchParams.get("q") ?? "";
      await route.fulfill({
        json: {
          environment,
          market,
          query,
          total: searchItems.length,
          truncated: false,
          list_fetched_at: "2026-10-05T08:00:00+00:00",
          items: searchItems,
        },
      });
    } else if (resource === "account") {
      await route.fulfill({ status: 503, json: { error: { kind: "config_error", message: "test" } } });
    } else {
      unexpected.push(url.pathname);
      await route.fulfill({ status: 404, json: {} });
    }
  });
  return { unexpected, quotes };
}

async function selectMenu(page: Page, name: string) {
  const menu = page.getByRole("button", { name: "메뉴 열기" });
  if (await menu.isVisible()) await menu.click();
  await page.getByRole("button", { name }).filter({ visible: true }).click();
}

async function selectEnvironment(page: Page, label: string) {
  await page.getByRole("radio", { name: label }).filter({ visible: true }).click();
}

const panel = (page: Page, name: string) => page.getByRole("dialog", { name: `${name} 주문` });
const tradingValueCard = (page: Page) => page.getByRole("region", { name: "거래대금 상위" });

test("순위의 종목을 누르면 현재가 조회 결과와 수량 1이 채워진 매수 패널이 열린다", async ({ page }) => {
  const { unexpected, quotes } = await mockApi(page, { domestic: [rankingItem("000660", "SK하이닉스", null, 184_100)], us: [] });
  await page.goto("/");
  await selectEnvironment(page, "국내 모의");
  await selectMenu(page, "순위");

  await tradingValueCard(page).getByRole("button", { name: /SK하이닉스/ }).click();
  const dialog = panel(page, "SK하이닉스");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("국내 모의 매수")).toBeVisible();
  await expect(dialog.getByText("000660")).toBeVisible();
  await expect(dialog.getByRole("radio", { name: "지정가" })).toHaveAttribute("aria-checked", "true");
  await expect(dialog.getByLabel("가격 (원)")).toHaveValue("190,000");
  await expect(dialog.getByLabel("수량 (주)")).toHaveValue("1");
  await expect(dialog.getByLabel("예상 주문금액")).toHaveText("190,000원");
  expect(quotes).toEqual([{ environment: "domestic_paper", code: "000660", exchange: null }]);

  await dialog.getByLabel("수량 (주)").fill("3");
  await expect(dialog.getByLabel("예상 주문금액")).toHaveText("570,000원");
  // 가격은 천 단위 쉼표로 나눠 보여주고, 계산에는 쉼표를 뺀 값을 쓴다.
  await dialog.getByLabel("가격 (원)").fill("1841000");
  await expect(dialog.getByLabel("가격 (원)")).toHaveValue("1,841,000");
  await expect(dialog.getByLabel("예상 주문금액")).toHaveText("5,523,000원");
  await expect(dialog.getByRole("button", { name: "매수" })).toBeEnabled();

  await dialog.getByRole("radio", { name: "시장가" }).click();
  await expect(dialog.getByLabel("가격 (원)")).toHaveCount(0);
  await expect(dialog.getByLabel("예상 주문금액")).toHaveCount(0);
  await expect(dialog.getByText("시장가는 가격을 정하지 않고")).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  expect(unexpected).toEqual([]);
});

test("인기 종목에서 열어도 집계 시점 가격이 아니라 조회한 현재가를 채운다", async ({ page }) => {
  await mockApi(page, { domestic: [rankingItem("000660", "SK하이닉스", null, 184_100)], us: [] });
  await page.goto("/");
  await selectEnvironment(page, "국내 모의");
  await selectMenu(page, "순위");

  await page.getByRole("region", { name: "인기 종목" }).getByRole("button", { name: /SK하이닉스/ }).click();
  await expect(panel(page, "SK하이닉스").getByLabel("가격 (원)")).toHaveValue("190,000");
});

test("현재가를 불러오는 동안 표시가 보이고, 먼저 입력한 가격은 조회 결과로 덮어쓰지 않는다", async ({ page }) => {
  await mockApi(page, { domestic: [rankingItem("000660", "SK하이닉스", null, 184_100)], us: [] }, [], ({ code }) => ({
    body: { code, price: 190_000, fetched_at: "2026-10-05T06:30:00+00:00" },
    delayMs: 800,
  }));
  await page.goto("/");
  await selectEnvironment(page, "국내 모의");
  await selectMenu(page, "순위");
  await tradingValueCard(page).getByRole("button", { name: /SK하이닉스/ }).click();
  const dialog = panel(page, "SK하이닉스");

  await expect(dialog.getByText("현재가를 불러오는 중입니다.")).toBeVisible();
  await dialog.getByLabel("가격 (원)").fill("185000");
  await expect(dialog.getByText("현재가를 불러오는 중입니다.")).toHaveCount(0, { timeout: 3000 });
  await expect(dialog.getByLabel("가격 (원)")).toHaveValue("185,000");
});

test("현재가를 불러오지 못하면 안내를 보여주고, 입력 여부에 따라 빈칸이거나 입력값을 남긴다", async ({ page }) => {
  await mockApi(page, { domestic: [rankingItem("000660", "SK하이닉스", null, 184_100)], us: [] }, [], () => ({
    status: 502,
    body: { error: { kind: "kiwoom_error", message: "test" } },
    delayMs: 800,
  }));
  await page.goto("/");
  await selectEnvironment(page, "국내 모의");
  await selectMenu(page, "순위");
  const row = tradingValueCard(page).getByRole("button", { name: /SK하이닉스/ });

  await row.click();
  let dialog = panel(page, "SK하이닉스");
  await expect(dialog.getByText("현재가를 불러오지 못했습니다.", { exact: false })).toBeVisible({ timeout: 3000 });
  await expect(dialog.getByLabel("가격 (원)")).toHaveValue("");
  await page.keyboard.press("Escape");

  // 입력한 뒤에 실패가 와도 입력값은 그대로 둔다.
  await row.click();
  dialog = panel(page, "SK하이닉스");
  await dialog.getByLabel("가격 (원)").fill("185000");
  await expect(dialog.getByText("현재가를 불러오지 못했습니다.", { exact: false })).toBeVisible({ timeout: 3000 });
  await expect(dialog.getByLabel("가격 (원)")).toHaveValue("185,000");
});

test("패널이 열린 동안 뒤쪽 화면은 막히고, 닫으면 눌렀던 종목으로 초점이 돌아간다", async ({ page }) => {
  await mockApi(page, { domestic: [rankingItem("000660", "SK하이닉스", null, 184_100)], us: [] });
  await page.goto("/");
  await selectEnvironment(page, "국내 모의");
  await selectMenu(page, "순위");

  const row = tradingValueCard(page).getByRole("button", { name: /SK하이닉스/ });
  await row.click();
  await expect(panel(page, "SK하이닉스")).toBeVisible();
  await expect(page.locator("#root")).toHaveAttribute("inert", "");
  await expect(page.getByRole("button", { name: "닫기", exact: true })).toBeFocused();

  await page.keyboard.press("Escape");
  await expect(page.locator("#root")).not.toHaveAttribute("inert");
  await expect(row).toBeFocused();
});

test("잘못된 수량과 가격은 이유를 보여준다", async ({ page }) => {
  await mockApi(page, { domestic: [rankingItem("000660", "SK하이닉스", null, 184_100)], us: [] });
  await page.goto("/");
  await selectEnvironment(page, "국내 모의");
  await selectMenu(page, "순위");
  await tradingValueCard(page).getByRole("button", { name: /SK하이닉스/ }).click();
  const dialog = panel(page, "SK하이닉스");

  await dialog.getByLabel("수량 (주)").fill("1.5");
  await expect(dialog.getByText("수량은 1주 이상, 12자리 이하의 정수로 입력하세요.")).toBeVisible();
  await dialog.getByLabel("수량 (주)").fill("0");
  await expect(dialog.getByText("수량은 1주 이상, 12자리 이하의 정수로 입력하세요.")).toBeVisible();
  await dialog.getByLabel("가격 (원)").fill("1000.5");
  await expect(dialog.getByText("가격은 1원 이상, 12자리 이하의 정수로 입력하세요.")).toBeVisible();
  await expect(dialog.getByLabel("예상 주문금액")).toHaveText("–");
});

test("미국 모의에서 NYSE·NASDAQ·AMEX가 아닌 종목은 현재가를 조회하지 않고 매수 칸 대신 이유를 보여준다", async ({ page }) => {
  const { quotes } = await mockApi(page, {
    domestic: [],
    us: [rankingItem("SOXL", "디렉시온 반도체", "NYSE", 162.6), rankingItem("ABCD", "장외 종목", "OTC", 1.2)],
  });
  await page.goto("/");
  await selectEnvironment(page, "미국 모의");
  await selectMenu(page, "순위");

  await tradingValueCard(page).getByRole("button", { name: /장외 종목/ }).click();
  const blocked = panel(page, "장외 종목");
  await expect(blocked.getByText("매수할 수 없는 종목입니다")).toBeVisible();
  await expect(blocked.getByText("미국 모의투자는 NYSE·NASDAQ·AMEX 종목만 매수할 수 있습니다.", { exact: false })).toBeVisible();
  await expect(blocked.getByText("이 종목의 거래소는 OTC입니다.", { exact: false })).toBeVisible();
  await expect(blocked.getByRole("button", { name: "매수" })).toHaveCount(0);
  await blocked.getByRole("button", { name: "닫기", exact: true }).click();
  expect(quotes).toEqual([]);

  await tradingValueCard(page).getByRole("button", { name: /디렉시온 반도체/ }).click();
  const dialog = panel(page, "디렉시온 반도체");
  await expect(dialog.getByLabel("가격 (USD)")).toHaveValue("170.25");
  expect(quotes).toEqual([{ environment: "us_paper", code: "SOXL", exchange: "NYSE" }]);
  await dialog.getByLabel("수량 (주)").fill("2");
  await expect(dialog.getByLabel("예상 주문금액")).toHaveText("$340.50");
  // 1달러 미만 가격도 예상 주문금액이 0으로 보이지 않는다.
  await dialog.getByLabel("가격 (USD)").fill("0.0001");
  await dialog.getByLabel("수량 (주)").fill("1");
  await expect(dialog.getByLabel("예상 주문금액")).toHaveText("$0.0001");
});

test("검색 결과에서도 패널이 열리고, 종목 상태 경고와 실전 표시를 보여준다", async ({ page }) => {
  await mockApi(page, { domestic: [], us: [] }, [searchItem({ status: "관리종목" })]);
  await page.goto("/");
  await selectEnvironment(page, "국내 실전");
  await selectMenu(page, "종목 검색");
  await page.getByRole("searchbox", { name: "종목 검색어" }).fill("삼성");

  await page.getByRole("region", { name: "검색 결과" }).getByRole("button", { name: /삼성전자/ }).click();
  const dialog = panel(page, "삼성전자");
  await expect(dialog.getByText("실전", { exact: true })).toBeVisible();
  await expect(dialog.getByText('이 종목은 "관리종목" 상태입니다.', { exact: false })).toBeVisible();
  await expect(dialog.getByText("005930 · 코스피")).toBeVisible();
  // 검색 결과에는 가격이 없지만, 패널을 열 때 조회한 현재가로 채운다.
  await expect(dialog.getByLabel("가격 (원)")).toHaveValue("61,300");
  await expect(dialog.getByText("실전투자에서는 주문할 수 없습니다.")).toBeVisible();
});

test("바깥 영역을 누르면 패널이 닫히고, 패널이 열려도 가로 스크롤이 없다", async ({ page }) => {
  await mockApi(page, {
    domestic: [rankingItem("000660", "SK하이닉스", null, 184_100)],
    us: [rankingItem("SOXL", "디렉시온 반도체", "NYSE", 162.6)],
  });
  await page.goto("/");
  await selectEnvironment(page, "국내 모의");
  await selectMenu(page, "순위");
  await tradingValueCard(page).getByRole("button", { name: /SK하이닉스/ }).click();
  await expect(panel(page, "SK하이닉스")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);

  await page.getByRole("button", { name: "주문 패널 닫기" }).click({ position: { x: 5, y: 5 } });
  await expect(panel(page, "SK하이닉스")).toHaveCount(0);
});
