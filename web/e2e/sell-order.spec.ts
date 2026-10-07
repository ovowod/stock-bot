import { expect, test, type Page } from "@playwright/test";
import { openDashboard } from "./support";

// 브라우저의 /api 요청을 가로채 가짜 응답을 준다. 주문 요청도 가짜 서버가 받으며 키움 서버는 호출되지 않는다.

type Holding = { code: string; name: string; quantity: number | null; tradable_quantity: number | null };

const holding = (overrides: Partial<Holding> = {}) => ({
  code: "005930",
  name: "삼성전자",
  quantity: 10,
  tradable_quantity: 7,
  purchase_price: 70_000,
  current_price: 84_500,
  purchase_amount: 700_000,
  evaluation_amount: 845_000,
  profit_loss: 145_000,
  return_rate: 20.71,
  weight: 100,
  ...overrides,
});

const account = (environment: string, holdings: unknown[]) => ({
  environment,
  market: "domestic",
  account_no: "8135****11",
  fetched_at: "2026-10-05T06:30:00+00:00",
  summary: {
    estimated_assets: 1_000_000,
    total_evaluation: 845_000,
    total_purchase: 700_000,
    total_profit_loss: 145_000,
    total_return_rate: 20.71,
  },
  deposit: { deposit: 0, orderable: 0, withdrawable: 0, d1_estimated: 0, d2_estimated: 0 },
  holdings,
});

const usHolding = (overrides: Record<string, unknown> = {}) => ({
  code: "AAPL",
  name: "애플",
  exchange: "NASDAQ",
  currency: "USD",
  quantity: 395,
  sellable_quantity: 395,
  purchase_price: 282.1603,
  current_price: 275.24,
  purchase_amount: 111453.32,
  evaluation_amount: 108719.8,
  profit_loss: -3283.95,
  return_rate: -2.94,
  evaluation_amount_krw: 165743335,
  profit_loss_krw: -5006383,
  ...overrides,
});

const usAccount = (environment: string, holdings: unknown[]) => ({
  environment,
  market: "us",
  account_no: "6111****41",
  fetched_at: "2026-10-05T06:30:00+00:00",
  currency: "USD",
  summary: {
    total_evaluation: 108719.8,
    total_purchase: 111453.32,
    total_profit_loss: -3283.95,
    total_return_rate: -2.94,
    today_realized_profit_loss: 0,
    today_realized_return_rate: 0,
  },
  summary_krw: { total_evaluation: 0, total_purchase: 0, total_profit_loss: 0, today_realized_profit_loss: 0 },
  deposit: { krw_deposit: 0, currencies: [] },
  holdings,
});

const QUOTES: Record<string, number> = { "005930": 84_500, AAPL: 275.24 };

type OrderReply = { status?: number; body: unknown };
type HoldingReply = { status?: number; body: unknown; delayMs?: number };

/** 최종 확인의 잔고 재조회 응답. 기본은 계좌 확인과 같은 수량이다. */
const heldReply = (code: string, quantity: number, sellable: number): HoldingReply => ({
  body: { code, held: true, quantity, sellable_quantity: sellable, fetched_at: "2026-10-05T01:00:00+00:00" },
});
const defaultHolding = (code: string) => (code === "AAPL" ? heldReply(code, 395, 395) : heldReply(code, 10, 7));
type OrderRequest = { environment: string; body: Record<string, unknown> };

const accepted: (request: OrderRequest) => OrderReply = (request) => ({
  body: { order_key: request.body.order_key, order_no: "0000138", accepted_at: "2026-10-05T01:00:00+00:00" },
});

async function mockApi(
  page: Page,
  {
    holdings = [holding()],
    usHoldings = [usHolding()],
    order = accepted,
    holdingReply = defaultHolding,
  }: {
    holdings?: unknown[];
    usHoldings?: unknown[];
    order?: (r: OrderRequest) => OrderReply;
    holdingReply?: (code: string) => HoldingReply;
  } = {},
) {
  const orders: OrderRequest[] = [];
  const holdingRequests: string[] = [];
  const accountRequests: string[] = [];
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const [, , , environment, resource] = url.pathname.split("/");
    if (resource === "account") {
      accountRequests.push(environment);
      await route.fulfill({
        json: environment.startsWith("us") ? usAccount(environment, usHoldings) : account(environment, holdings),
      });
    } else if (resource === "quote") {
      const code = url.searchParams.get("code") ?? "";
      await route.fulfill({ json: { code, price: QUOTES[code] ?? 100, fetched_at: "2026-10-05T01:00:00+00:00" } });
    } else if (resource === "holdings") {
      const code = url.pathname.split("/")[5];
      holdingRequests.push(code);
      const reply = holdingReply(code);
      if (reply.delayMs) await new Promise((resolve) => setTimeout(resolve, reply.delayMs));
      await route.fulfill({ status: reply.status ?? 200, json: reply.body }).catch(() => {});
    } else if (resource === "orders") {
      const request = { environment, body: route.request().postDataJSON() };
      orders.push(request);
      const reply = order(request);
      await route.fulfill({ status: reply.status ?? 200, json: reply.body });
    } else {
      await route.fulfill({ status: 503, json: { error: { kind: "config_error", message: "test" } } });
    }
  });
  return { orders, accountRequests, holdingRequests };
}

/** 계좌 확인에서 보유종목의 매도 버튼을 누른다. 좁은 화면에서는 줄을 펼쳐야 버튼이 보인다. */
async function openSell(page: Page, environment = "국내 모의", name = "삼성전자") {
  await openDashboard(page);
  await page.getByRole("radio", { name: environment }).filter({ visible: true }).click();
  const sell = page.getByRole("button", { name: `${name} 매도` }).filter({ visible: true });
  if ((await sell.count()) === 0) {
    await page.getByRole("button", { name: new RegExp(name) }).filter({ visible: true }).first().click();
  }
  await sell.click();
  return page.getByRole("dialog", { name: `${name} 주문` });
}

const confirmation = (page: Page) => page.getByRole("region", { name: "최종 확인" });

test("국내 모의 보유종목에서 매도 패널을 열어 매도 주문을 보내고, 계좌 확인을 다시 조회한다", async ({ page }) => {
  const { orders, accountRequests } = await mockApi(page);
  const dialog = await openSell(page);

  await expect(dialog.getByText("국내 모의 매도")).toBeVisible();
  await expect(dialog.getByLabel("보유 수량", { exact: true })).toHaveText("10주");
  await expect(dialog.getByLabel("매도 가능 수량", { exact: true })).toHaveText("7주");
  await expect(dialog.getByLabel("수량 (주)")).toHaveValue("7");
  await expect(dialog.getByLabel(/가격/)).toHaveValue("84,500");
  await expect(dialog.getByRole("button", { name: "매수" })).toHaveCount(0);

  await dialog.getByRole("button", { name: "매도", exact: true }).click();
  const confirm = confirmation(page);
  await expect(confirm.locator('dd[data-term="주문"]')).toHaveText("매도");
  await expect(confirm.locator('dd[data-term="수량"]')).toHaveText("7주");
  await expect(confirm).toContainText("매도 주문을 보냅니다");
  await confirm.getByRole("button", { name: "주문하기" }).click();

  const toast = page.getByRole("status").filter({ hasText: "매도 주문이 접수되었습니다" });
  await expect(toast).toContainText("주문번호 0000138");
  await expect(dialog).toHaveCount(0);
  const { order_key: _, ...rest } = orders[0].body;
  expect(rest).toEqual({ side: "sell", code: "005930", order_type: "limit", quantity: "7", price: "84500" });
  await expect.poll(() => accountRequests.length).toBe(2);
});

test("매도 가능 수량을 넘거나 매도 가능 수량이 없으면 매도 버튼이 눌리지 않는다", async ({ page }) => {
  const { orders } = await mockApi(page, {
    holdings: [holding(), holding({ code: "035420", name: "NAVER", quantity: 5, tradable_quantity: 0 })],
  });
  const dialog = await openSell(page);
  await dialog.getByLabel("수량 (주)").fill("8");
  await expect(dialog.getByText("매도 가능 수량(7주)을 넘을 수 없습니다.")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "매도", exact: true })).toBeDisabled();
  await dialog.getByRole("button", { name: "닫기", exact: true }).click();

  const empty = await openSell(page, "국내 모의", "NAVER");
  await expect(empty.getByText("매도 가능 수량이 없습니다")).toBeVisible();
  await expect(empty.getByLabel("수량 (주)")).toHaveValue("");
  await expect(empty.getByText("수량은 1주 이상", { exact: false })).toHaveCount(0);
  await expect(empty.getByRole("button", { name: "매도", exact: true })).toBeDisabled();
  expect(orders).toEqual([]);
});

test("매도 가능 수량을 모르면 확인 불가로 보이고 수량을 입력해 최종 확인으로 갈 수 있다", async ({ page }) => {
  await mockApi(page, { holdings: [holding({ quantity: null, tradable_quantity: null })] });
  const dialog = await openSell(page);

  await expect(dialog.getByLabel("보유 수량", { exact: true })).toHaveText("확인 불가");
  await expect(dialog.getByLabel("매도 가능 수량", { exact: true })).toHaveText("확인 불가");
  await expect(dialog.getByLabel("수량 (주)")).toHaveValue("");
  await dialog.getByLabel("수량 (주)").fill("100");
  await dialog.getByRole("button", { name: "매도", exact: true }).click();
  await expect(confirmation(page)).toBeVisible();
});

for (const [kind, message] of [
  ["sellable_exceeded", "매도 가능 수량(3주)을 넘어 주문하지 않았습니다."],
  ["sellable_check_failed", "잔고를 확인하지 못해 주문하지 않았습니다. 키움 오류 [20] 조회 실패"],
]) {
  test(`서버가 ${kind}로 막으면 실패 알림이 남고 입력 값이 그대로다`, async ({ page }) => {
    await mockApi(page, {
      order: () => ({ status: kind === "sellable_exceeded" ? 400 : 502, body: { error: { kind, message } } }),
    });
    const dialog = await openSell(page);
    await dialog.getByRole("button", { name: "매도", exact: true }).click();
    await confirmation(page).getByRole("button", { name: "주문하기" }).click();

    const alert = page.getByRole("alert").filter({ hasText: message });
    await expect(alert).toContainText("주문하지 못했습니다");
    await expect(alert).not.toContainText("접수 여부를 확인할 수 없습니다");
    await expect(dialog.getByLabel("수량 (주)")).toHaveValue("7");
  });
}

test("실전투자에서는 매도 버튼과 패널은 보이지만 주문할 수 없다", async ({ page }) => {
  const { orders, holdingRequests } = await mockApi(page);
  const dialog = await openSell(page, "국내 실전");

  await expect(dialog.getByText("실전투자에서는 주문할 수 없습니다.")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "매도", exact: true })).toHaveCount(0);
  expect(orders).toEqual([]);
  expect(holdingRequests).toEqual([]);
});

test("매도 버튼과 패널이 가로 스크롤을 만들지 않는다", async ({ page }) => {
  await mockApi(page);
  await openSell(page);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  expect(await page.evaluate(() => window.innerWidth)).toBe(page.viewportSize()!.width);
});

test("국내 시장가 매도는 가격 없이 보내고, 최종 확인에 시장가로 보인다", async ({ page }) => {
  const { orders } = await mockApi(page);
  const dialog = await openSell(page);
  await dialog.getByRole("radio", { name: "시장가" }).click();
  await dialog.getByRole("button", { name: "매도", exact: true }).click();

  const confirm = confirmation(page);
  await expect(confirm.locator('dd[data-term="가격"]')).toHaveText("시장가");
  await confirm.getByRole("button", { name: "주문하기" }).click();
  await expect(page.getByRole("status").filter({ hasText: "매도 주문이 접수되었습니다" })).toBeVisible();
  const { order_key: _, ...rest } = orders[0].body;
  expect(rest).toEqual({ side: "sell", code: "005930", order_type: "market", quantity: "7" });
});

test("미국 모의 보유종목을 거래소·USD 금액과 함께 매도하고 계좌 확인을 다시 조회한다", async ({ page }) => {
  const { orders, accountRequests } = await mockApi(page);
  const dialog = await openSell(page, "미국 모의", "애플");

  await expect(dialog.getByText("미국 모의 매도")).toBeVisible();
  await expect(dialog.getByText("AAPL · NASDAQ")).toBeVisible();
  await expect(dialog.getByLabel("매도 가능 수량", { exact: true })).toHaveText("395주");
  await expect(dialog.getByLabel(/가격/)).toHaveValue("275.24");
  await dialog.getByLabel("수량 (주)").fill("2");
  await dialog.getByRole("button", { name: "매도", exact: true }).click();

  const confirm = confirmation(page);
  await expect(confirm.locator('dd[data-term="거래소"]')).toHaveText("NASDAQ");
  await expect(confirm.locator('dd[data-term="가격"]')).toHaveText("$275.24");
  await expect(confirm.locator('dd[data-term="예상 주문금액"]')).toHaveText("$550.48");
  await confirm.getByRole("button", { name: "주문하기" }).click();

  await expect(page.getByRole("status").filter({ hasText: "매도 주문이 접수되었습니다" })).toBeVisible();
  expect(orders[0].environment).toBe("us_paper");
  const { order_key: _, ...rest } = orders[0].body;
  expect(rest).toEqual({
    side: "sell",
    code: "AAPL",
    exchange: "NASDAQ",
    order_type: "limit",
    quantity: "2",
    price: "275.24",
  });
  await expect.poll(() => accountRequests.filter((env) => env === "us_paper").length).toBe(2);
});

test("미국 매도도 주문할 수 없는 거래소의 종목은 막고, 매도 가능 수량을 모르면 확인 불가로 연다", async ({ page }) => {
  const { orders } = await mockApi(page, {
    usHoldings: [
      usHolding({ code: "OTCX", name: "장외 종목", exchange: "OTC" }),
      usHolding({ sellable_quantity: null }),
    ],
  });
  const otc = await openSell(page, "미국 모의", "장외 종목");
  await expect(otc.getByText("매도할 수 없는 종목입니다")).toBeVisible();
  await expect(otc).toContainText("미국 모의투자는 NYSE·NASDAQ·AMEX 종목만 매도할 수 있습니다.");
  await expect(otc.getByRole("button", { name: "매도", exact: true })).toHaveCount(0);
  await otc.getByRole("button", { name: "닫기", exact: true }).click();

  const unknown = await openSell(page, "미국 모의", "애플");
  await expect(unknown.getByLabel("매도 가능 수량", { exact: true })).toHaveText("확인 불가");
  await expect(unknown.getByLabel("수량 (주)")).toHaveValue("");
  expect(orders).toEqual([]);
});

const notHeld = (code: string): HoldingReply => ({
  body: { code, held: false, quantity: 0, sellable_quantity: 0, fetched_at: "2026-10-05T01:00:00+00:00" },
});
const failed: HoldingReply = { status: 502, body: { error: { kind: "kiwoom_error", message: "키움 오류 [20] 조회 실패" } } };
const orderButton = (page: Page) => confirmation(page).getByRole("button", { name: "주문하기" });

test("최종 확인을 열면 잔고를 다시 조회하고, 확인되기 전에는 주문하기가 막힌다", async ({ page }) => {
  const { holdingRequests } = await mockApi(page, {
    holdingReply: (code) => ({ ...heldReply(code, 12, 9), delayMs: 500 }),
  });
  const dialog = await openSell(page);
  await dialog.getByRole("button", { name: "매도", exact: true }).click();

  const confirm = confirmation(page);
  await expect(confirm.getByText("잔고를 확인하는 중입니다.")).toBeVisible();
  await expect(orderButton(page)).toBeDisabled();
  await expect(confirm.locator('dd[data-term="보유 수량"]')).toHaveText("12주");
  await expect(confirm.locator('dd[data-term="매도 가능 수량"]')).toHaveText("9주");
  await expect(orderButton(page)).toBeEnabled();
  expect(holdingRequests).toEqual(["005930"]);

  // 패널의 표시도 최신 값이 되고, 입력한 수량은 그대로다.
  await confirm.getByRole("button", { name: "취소" }).click();
  await expect(dialog.getByLabel("보유 수량", { exact: true })).toHaveText("12주");
  await expect(dialog.getByLabel("매도 가능 수량", { exact: true })).toHaveText("9주");
  await expect(dialog.getByLabel("수량 (주)")).toHaveValue("7");
});

test("다시 조회한 매도 가능 수량이 줄었으면 안내와 함께 주문하기가 막히고, 취소하면 최신 수량으로 고친다", async ({
  page,
}) => {
  const { orders } = await mockApi(page, { holdingReply: (code) => heldReply(code, 10, 4) });
  const dialog = await openSell(page);
  await dialog.getByRole("button", { name: "매도", exact: true }).click();

  await expect(confirmation(page).getByText("매도 가능 수량이 4주로 줄었습니다")).toBeVisible();
  await expect(orderButton(page)).toBeDisabled();
  await confirmation(page).getByRole("button", { name: "취소" }).click();

  await expect(dialog.getByLabel("수량 (주)")).toHaveValue("7");
  await expect(dialog.getByLabel("매도 가능 수량", { exact: true })).toHaveText("4주");
  await expect(dialog.getByText("매도 가능 수량(4주)을 넘을 수 없습니다.")).toBeVisible();
  expect(orders).toEqual([]);
});

test("다시 조회했을 때 보유하지 않은 종목이면 주문하기가 막힌다", async ({ page }) => {
  const { orders } = await mockApi(page, { holdingReply: notHeld });
  const dialog = await openSell(page);
  await dialog.getByRole("button", { name: "매도", exact: true }).click();

  await expect(confirmation(page).getByText("보유하고 있지 않은 종목입니다")).toBeVisible();
  await expect(orderButton(page)).toBeDisabled();
  expect(orders).toEqual([]);
});

for (const [title, retry, expectation] of [
  ["충분하면 주문하기가 풀린다", (code: string) => heldReply(code, 10, 7), null],
  ["줄었으면 안내와 함께 막힌다", (code: string) => heldReply(code, 10, 2), "매도 가능 수량이 2주로 줄었습니다"],
  ["보유하지 않았으면 안내와 함께 막힌다", notHeld, "보유하고 있지 않은 종목입니다"],
] as const) {
  test(`잔고 조회가 실패하면 다시 확인할 수 있고, 다시 받은 결과가 ${title}`, async ({ page }) => {
    let calls = 0;
    await mockApi(page, { holdingReply: (code) => (calls++ === 0 ? failed : retry(code)) });
    const dialog = await openSell(page);
    await dialog.getByRole("button", { name: "매도", exact: true }).click();

    const confirm = confirmation(page);
    await expect(confirm.getByText("잔고를 확인하지 못했습니다")).toBeVisible();
    await expect(confirm).toContainText("키움 오류 [20] 조회 실패");
    await expect(orderButton(page)).toBeDisabled();
    await confirm.getByRole("button", { name: "다시 확인" }).click();

    if (expectation === null) {
      await expect(orderButton(page)).toBeEnabled();
      await expect(confirm.getByText("잔고를 확인하지 못했습니다")).toHaveCount(0);
    } else {
      await expect(confirm.getByText(expectation)).toBeVisible();
      await expect(orderButton(page)).toBeDisabled();
    }
  });
}

test("최종 확인을 닫으면 늦게 온 잔고 응답은 반영하지 않는다", async ({ page }) => {
  await mockApi(page, { holdingReply: (code) => ({ ...heldReply(code, 10, 1), delayMs: 600 }) });
  const dialog = await openSell(page);
  await dialog.getByRole("button", { name: "매도", exact: true }).click();
  await confirmation(page).getByRole("button", { name: "취소" }).click();

  await page.waitForTimeout(900);
  await expect(dialog.getByLabel("매도 가능 수량", { exact: true })).toHaveText("7주");
  await expect(dialog.getByRole("button", { name: "매도", exact: true })).toBeEnabled();
});

test("미국 매도도 최종 확인에서 티커로 잔고를 다시 조회한다", async ({ page }) => {
  const { holdingRequests } = await mockApi(page, { holdingReply: (code) => heldReply(code, 395, 1) });
  const dialog = await openSell(page, "미국 모의", "애플");
  await dialog.getByLabel("수량 (주)").fill("2");
  await dialog.getByRole("button", { name: "매도", exact: true }).click();

  await expect(confirmation(page).getByText("매도 가능 수량이 1주로 줄었습니다")).toBeVisible();
  await expect(orderButton(page)).toBeDisabled();
  expect(holdingRequests).toEqual(["AAPL"]);
});

test("최종 확인을 다시 열면 이전 확인 결과가 아니라 새 조회 결과로 판정한다", async ({ page }) => {
  let calls = 0;
  const { holdingRequests } = await mockApi(page, {
    holdingReply: (code) => (calls++ === 0 ? heldReply(code, 10, 7) : { ...heldReply(code, 10, 7), delayMs: 800 }),
  });
  const dialog = await openSell(page);
  await dialog.getByRole("button", { name: "매도", exact: true }).click();
  await expect(orderButton(page)).toBeEnabled();
  await confirmation(page).getByRole("button", { name: "취소" }).click();

  await dialog.getByRole("button", { name: "매도", exact: true }).click();
  await expect(orderButton(page)).toBeDisabled();
  await expect(confirmation(page).getByText("잔고를 확인하는 중입니다.")).toBeVisible();
  await expect(orderButton(page)).toBeEnabled();
  expect(holdingRequests).toHaveLength(2);
});
