import { expect, test, type Page } from "@playwright/test";
import { openDashboard } from "./support";

// 브라우저의 /api 요청을 가로채 가짜 응답을 준다. 키움 서버는 호출되지 않는다.

const account = (environment: string) => ({
  environment,
  market: environment.startsWith("us") ? "us" : "domestic",
  account_no: "8135****11",
  fetched_at: "2026-10-05T06:30:00+00:00",
  currency: "USD",
  summary: {
    estimated_assets: 1_000_000,
    total_evaluation: 845_000,
    total_purchase: 700_000,
    total_profit_loss: 145_000,
    total_return_rate: 20.71,
    today_realized_profit_loss: 0,
    today_realized_return_rate: 0,
  },
  summary_krw: { total_evaluation: 0, total_purchase: 0, total_profit_loss: 0, today_realized_profit_loss: 0 },
  deposit: { deposit: 0, orderable: 0, withdrawable: 0, d1_estimated: 0, d2_estimated: 0, krw_deposit: 0, currencies: [] },
  holdings: [
    {
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
    },
  ],
});

const openOrder = (overrides: Record<string, unknown> = {}) => ({
  order_no: "0000070",
  code: "000660",
  name: "SK하이닉스",
  side: "sell",
  side_label: "매도",
  order_type: "지정가",
  price: 201_000,
  ordered_quantity: 10,
  remaining_quantity: 3,
  time: "09:30:05",
  exchange: "KRX",
  cancelable: true,
  blocked_reason: null,
  ...overrides,
});

const marketBuy = openOrder({
  order_no: "0000069",
  code: "005930",
  name: "삼성전자",
  side: "buy",
  side_label: "매수",
  order_type: "시장가",
  price: null,
  ordered_quantity: 1,
  remaining_quantity: 1,
  time: "15:41:13",
});

type Reply = { status?: number; body: unknown };
const listed = (orders: unknown[]): Reply => ({ body: { orders, fetched_at: "2026-10-05T06:30:00+00:00" } });

async function mockApi(page: Page, { openOrders = (): Reply => listed([openOrder(), marketBuy]) } = {}) {
  const accountRequests: string[] = [];
  const openOrderRequests: string[] = [];
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const [, , , environment, resource] = url.pathname.split("/");
    if (resource === "account") {
      accountRequests.push(environment);
      await route.fulfill({ json: account(environment) });
    } else if (resource === "open-orders") {
      openOrderRequests.push(environment);
      const reply = openOrders();
      await route.fulfill({ status: reply.status ?? 200, json: reply.body });
    } else if (resource === "quote") {
      await route.fulfill({ json: { code: "005930", price: 84_500, fetched_at: "2026-10-05T01:00:00+00:00" } });
    } else if (resource === "holdings") {
      await route.fulfill({
        json: { code: "005930", held: true, quantity: 10, sellable_quantity: 7, fetched_at: "2026-10-05T01:00:00+00:00" },
      });
    } else if (resource === "orders") {
      const body = route.request().postDataJSON();
      await route.fulfill({
        json: { order_key: body.order_key, order_no: "0000138", accepted_at: "2026-10-05T01:00:00+00:00" },
      });
    } else {
      await route.fulfill({ status: 503, json: { error: { kind: "config_error", message: "test" } } });
    }
  });
  return { accountRequests, openOrderRequests };
}

const panel = (page: Page) => page.getByRole("region", { name: "미체결 주문" });
const row = (page: Page, name: string) => panel(page).getByRole("listitem").filter({ hasText: name });

async function open(page: Page, environment = "국내 모의") {
  await openDashboard(page);
  await page.getByRole("radio", { name: environment }).first().click();
  await expect(page.getByRole("heading", { name: "예수금" })).toBeVisible();
}

test("국내 계좌 확인 아래에 미체결 주문이 보인다", async ({ page }) => {
  const { openOrderRequests } = await mockApi(page);
  await open(page);

  await expect(panel(page)).toContainText("2건");
  const sell = row(page, "SK하이닉스");
  await expect(sell).toContainText("000660");
  await expect(sell).toContainText("매도");
  await expect(sell).toContainText("지정가");
  await expect(sell).toContainText("201,000원");
  await expect(sell).toContainText("미체결 3 / 주문 10주");
  await expect(sell).toContainText("09:30:05");
  await expect(sell).toContainText("KRX");
  const buy = row(page, "삼성전자");
  await expect(buy).toContainText("매수");
  await expect(buy).toContainText("시장가");
  await expect(buy).toContainText("미체결 1 / 주문 1주");
  expect(openOrderRequests).toEqual(["domestic_paper"]);
});

test("미체결 주문이 없으면 없다고 보여준다", async ({ page }) => {
  await mockApi(page, { openOrders: () => listed([]) });
  await open(page);

  await expect(panel(page)).toContainText("미체결 주문이 없습니다");
});

test("미체결 조회가 실패해도 계좌는 보이고, 다시 시도하면 다시 요청한다", async ({ page }) => {
  let fail = true;
  const { openOrderRequests } = await mockApi(page, {
    openOrders: () =>
      fail
        ? { status: 502, body: { error: { kind: "connection_error", message: "키움 서버에 연결하지 못했습니다." } } }
        : listed([openOrder()]),
  });
  await open(page);

  await expect(page.getByTestId("hero-value")).toBeVisible();
  await expect(panel(page).getByRole("alert")).toContainText("키움 서버에 연결하지 못했습니다");
  fail = false;
  await panel(page).getByRole("button", { name: "다시 시도" }).click();
  await expect(row(page, "SK하이닉스")).toBeVisible();
  expect(openOrderRequests).toHaveLength(2);
});

test("상단 새로고침은 계좌와 미체결 주문을 함께 다시 불러온다", async ({ page }) => {
  const { accountRequests, openOrderRequests } = await mockApi(page);
  await open(page);
  await expect(row(page, "SK하이닉스")).toBeVisible();

  await page.getByRole("button", { name: "새로고침" }).click();

  await expect.poll(() => accountRequests.length).toBe(2);
  await expect.poll(() => openOrderRequests.length).toBe(2);
});

test("신용·NXT·통합 주문은 이유와 안내만 보이고 취소 버튼이 없다", async ({ page }) => {
  await mockApi(page, {
    openOrders: () =>
      listed([
        openOrder({ order_no: "1", name: "신용종목", side_label: "매수신용", side: "buy", cancelable: false, blocked_reason: "credit" }),
        openOrder({ order_no: "2", name: "NXT종목", exchange: "NXT", cancelable: false, blocked_reason: "exchange" }),
        openOrder({ order_no: "3", name: "통합종목", exchange: "통합", cancelable: false, blocked_reason: "exchange" }),
      ]),
  });
  await open(page);

  await expect(row(page, "신용종목")).toContainText("신용");
  await expect(row(page, "NXT종목")).toContainText("NXT 주문");
  await expect(row(page, "통합종목")).toContainText("통합 주문");
  for (const name of ["신용종목", "NXT종목", "통합종목"]) {
    await expect(row(page, name)).toContainText("키움 앱에서 취소하세요");
    await expect(row(page, name).getByRole("button", { name: /취소/ })).toHaveCount(0);
  }
});

test("실전투자에서는 목록만 보이고 취소할 수 없다는 안내가 한 번 보인다", async ({ page }) => {
  await mockApi(page, {
    openOrders: () =>
      listed([openOrder({ cancelable: false, blocked_reason: "real" }), { ...marketBuy, cancelable: false, blocked_reason: "real" }]),
  });
  await open(page, "국내 실전");

  await expect(panel(page).getByText("실전투자에서는 주문 취소를 할 수 없습니다.")).toHaveCount(1);
  await expect(row(page, "SK하이닉스")).toBeVisible();
  await expect(panel(page).getByText("키움 앱에서 취소하세요")).toHaveCount(0);
  await expect(panel(page).getByRole("button", { name: /취소/ })).toHaveCount(0);
});

test("미국 계좌 확인에는 미체결 주문 패널이 없고 요청도 나가지 않는다", async ({ page }) => {
  const { openOrderRequests } = await mockApi(page);
  await open(page, "미국 모의");

  await expect(panel(page)).toHaveCount(0);
  expect(openOrderRequests.filter((environment) => environment.startsWith("us"))).toEqual([]);
});

test("보유종목 매도가 접수되면 미체결 주문도 다시 불러온다", async ({ page }) => {
  const { openOrderRequests } = await mockApi(page);
  await open(page);
  await expect(row(page, "SK하이닉스")).toBeVisible();

  const holding = page.getByRole("button", { name: "삼성전자 매도" }).filter({ visible: true });
  if ((await holding.count()) === 0) await page.getByRole("button", { name: /삼성전자/ }).first().click();
  await page.getByRole("button", { name: "삼성전자 매도" }).filter({ visible: true }).click();
  const dialog = page.getByRole("dialog", { name: "삼성전자 주문" });
  await dialog.getByRole("button", { name: "매도", exact: true }).click();
  await dialog.getByRole("button", { name: "주문하기" }).click();

  await expect(page.getByRole("status").filter({ hasText: "매도 주문이 접수되었습니다" })).toBeVisible();
  await expect.poll(() => openOrderRequests.length).toBe(2);
});

test("미체결 주문 패널이 가로 스크롤을 만들지 않는다", async ({ page }) => {
  await mockApi(page, {
    openOrders: () =>
      listed([openOrder({ name: "아주아주긴이름의종목주식회사우선주", cancelable: false, blocked_reason: "credit" }), marketBuy]),
  });
  await open(page);
  await expect(panel(page)).toBeVisible();

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  expect(await page.evaluate(() => window.innerWidth)).toBe(page.viewportSize()!.width);
});
