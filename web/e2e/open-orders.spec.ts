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

type CancelBody = { order_key: string; order_no: string; quantity: string };
/** 취소 응답. abort는 연결 끊김, html은 형식이 다른 응답이다. */
type CancelReply = Reply | { abort: true } | { html: string } | { delayMs: number; reply: Reply };

const cancelAccepted = (body: CancelBody): Reply => ({
  body: {
    order_key: body.order_key,
    order_no: "0000141",
    original_order_no: body.order_no,
    cancel_quantity: null,
    accepted_at: "2026-10-05T01:00:00+00:00",
  },
});
const failure = (status: number, kind: string, message = "test"): Reply => ({
  status,
  body: { error: { kind, message, request_id: "req-1" } },
});

async function mockApi(
  page: Page,
  {
    openOrders = (): Reply => listed([openOrder(), marketBuy]),
    cancel = (body: CancelBody): CancelReply => cancelAccepted(body),
  }: { openOrders?: () => Reply; cancel?: (body: CancelBody) => CancelReply } = {},
) {
  const accountRequests: string[] = [];
  const openOrderRequests: string[] = [];
  const cancels: CancelBody[] = [];
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
    } else if (resource === "cancellations") {
      const body = route.request().postDataJSON() as CancelBody;
      cancels.push(body);
      let reply = cancel(body);
      if ("abort" in reply) return route.abort("connectionreset");
      if ("html" in reply) return route.fulfill({ status: 200, contentType: "text/html", body: reply.html });
      if ("delayMs" in reply) {
        await new Promise((resolve) => setTimeout(resolve, reply.delayMs));
        reply = reply.reply;
      }
      await route.fulfill({ status: reply.status ?? 200, json: reply.body }).catch(() => {});
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
  return { accountRequests, openOrderRequests, cancels };
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

// ---- 주문 취소 ----

const sheet = (page: Page) => page.getByRole("dialog", { name: "SK하이닉스 주문 취소" });
const submitCancel = (page: Page) => sheet(page).getByRole("button", { name: "주문 취소하기" });

async function openCancel(page: Page) {
  await row(page, "SK하이닉스").getByRole("button", { name: "SK하이닉스 주문 취소" }).click();
  await expect(sheet(page)).toBeVisible();
}

test("취소 버튼을 누르면 미체결을 다시 확인한 최종 확인이 열리고, 주문 취소가 접수된다", async ({ page }) => {
  let remaining = 3;
  const { accountRequests, openOrderRequests, cancels } = await mockApi(page, {
    openOrders: () => listed([openOrder({ remaining_quantity: remaining }), marketBuy]),
  });
  await open(page);
  await expect(row(page, "SK하이닉스")).toBeVisible();
  // 목록을 띄운 뒤 1주가 체결됐다.
  remaining = 2;

  await openCancel(page);

  await expect.poll(() => openOrderRequests.length).toBe(2);
  const confirm = sheet(page);
  await expect(confirm.locator('dd[data-term="미체결 수량"]')).toHaveText("2주");
  await expect(confirm.locator('dd[data-term="원주문번호"]')).toHaveText("0000070");
  await expect(confirm.locator('dd[data-term="주문"]')).toHaveText("매도");
  await expect(confirm.locator('dd[data-term="주문가격"]')).toHaveText("201,000원");
  await expect(confirm.getByRole("button", { name: "닫기", exact: true }).last()).toBeVisible();
  await submitCancel(page).click();

  await expect(page.getByRole("status").filter({ hasText: "주문 취소가 접수되었습니다" })).toContainText("0000141");
  await expect(page.getByRole("status").filter({ hasText: "주문 취소가 접수되었습니다" })).toContainText(
    "남은 수량 전부",
  );
  await expect(sheet(page)).toHaveCount(0);
  expect(cancels).toHaveLength(1);
  expect(cancels[0]).toEqual({ order_key: expect.any(String), order_no: "0000070", quantity: "2" });
  await expect.poll(() => accountRequests.length).toBe(2);
  await expect.poll(() => openOrderRequests.length).toBe(3);
});

test("취소할 수 있는 주문에만 취소 버튼이 있다", async ({ page }) => {
  await mockApi(page, {
    openOrders: () => listed([openOrder(), openOrder({ order_no: "9", name: "신용종목", cancelable: false, blocked_reason: "credit" })]),
  });
  await open(page);

  await expect(row(page, "SK하이닉스").getByRole("button", { name: "SK하이닉스 주문 취소" })).toBeVisible();
  await expect(row(page, "신용종목").getByRole("button")).toHaveCount(0);
});

test("다시 확인했더니 주문이 없으면 안내와 함께 주문 취소하기가 막힌다", async ({ page }) => {
  let first = true;
  await mockApi(page, {
    openOrders: () => {
      const reply = first ? listed([openOrder()]) : listed([]);
      first = false;
      return reply;
    },
  });
  await open(page);
  await openCancel(page);

  await expect(sheet(page)).toContainText("이미 체결되었거나 취소된 주문입니다");
  await expect(submitCancel(page)).toBeDisabled();
});

test("다시 확인이 실패하면 다시 확인 버튼으로 다시 요청하고, 성공하면 주문 취소하기가 풀린다", async ({ page }) => {
  let calls = 0;
  const { openOrderRequests } = await mockApi(page, {
    openOrders: () => {
      calls += 1;
      return calls === 2 ? failure(502, "connection_error", "키움 서버에 연결하지 못했습니다.") : listed([openOrder()]);
    },
  });
  await open(page);
  await openCancel(page);

  await expect(sheet(page)).toContainText("미체결을 확인하지 못했습니다");
  await expect(submitCancel(page)).toBeDisabled();
  await sheet(page).getByRole("button", { name: "다시 확인" }).click();

  await expect(submitCancel(page)).toBeEnabled();
  expect(openOrderRequests).toHaveLength(3);
});

test("주문 취소하기를 여러 번 눌러도 요청은 하나이고, 보내는 동안 닫히지 않는다", async ({ page }) => {
  const { cancels } = await mockApi(page, { cancel: (body) => ({ delayMs: 800, reply: cancelAccepted(body) }) });
  await open(page);
  await openCancel(page);
  await expect(submitCancel(page)).toBeEnabled();

  const submit = sheet(page).getByRole("button", { name: /취소/ }).last();
  await submit.click();
  await expect(sheet(page).getByRole("button", { name: "취소 중…" })).toBeDisabled();
  await submit.click({ force: true });
  await page.keyboard.press("Escape");
  await expect(sheet(page)).toBeVisible();
  await expect(sheet(page).getByRole("button", { name: "닫기", exact: true }).first()).toBeDisabled();

  await expect(sheet(page)).toHaveCount(0);
  expect(cancels).toHaveLength(1);
});

test("거부되면 실패 알림과 함께 최종 확인이 남고, 다시 보내면 새 주문 키를 쓴다", async ({ page }) => {
  let reject = true;
  const { cancels } = await mockApi(page, {
    cancel: (body) => (reject ? failure(502, "open_orders_check_failed", "미체결을 확인하지 못해 취소하지 않았습니다.") : cancelAccepted(body)),
  });
  await open(page);
  await openCancel(page);

  await submitCancel(page).click();
  const alert = page.getByRole("alert").filter({ hasText: "미체결을 확인하지 못해 취소하지 않았습니다." });
  await expect(alert).toContainText("주문을 취소하지 못했습니다");
  await expect(sheet(page)).toBeVisible();

  reject = false;
  await submitCancel(page).click();
  await expect(sheet(page)).toHaveCount(0);
  expect(cancels).toHaveLength(2);
  expect(cancels[1].order_key).not.toBe(cancels[0].order_key);
});

test("주문 없음으로 거부되면 미체결을 다시 확인해 안내를 보여준다", async ({ page }) => {
  let gone = false;
  const { openOrderRequests } = await mockApi(page, {
    openOrders: () => (gone ? listed([]) : listed([openOrder()])),
    cancel: () => {
      gone = true;
      return failure(400, "open_order_not_found", "이미 체결되었거나 취소된 주문입니다.");
    },
  });
  await open(page);
  await openCancel(page);
  await expect(submitCancel(page)).toBeEnabled();

  await submitCancel(page).click();

  await expect(sheet(page).getByRole("note")).toContainText("이미 체결되었거나 취소된 주문입니다");
  await expect(submitCancel(page)).toBeDisabled();
  expect(openOrderRequests).toHaveLength(3);
});

for (const [label, reply] of [
  ["409 중복", failure(409, "duplicate_order")],
  ["502 확인 불가", failure(502, "order_result_unknown")],
  ["연결 끊김", { abort: true }],
  ["HTML 응답", { html: "<html>bad gateway</html>" }],
] as [string, CancelReply][]) {
  test(`${label}이면 확인 불가 알림을 띄우고 최종 확인을 닫은 뒤 다시 불러온다`, async ({ page }) => {
    const { accountRequests, openOrderRequests, cancels } = await mockApi(page, { cancel: () => reply });
    await open(page);
    await openCancel(page);
    await expect(submitCancel(page)).toBeEnabled();

    await submitCancel(page).click();

    const alert = page.getByRole("alert").filter({ hasText: "접수 여부를 확인할 수 없습니다" });
    await expect(alert).toContainText("키움에서 주문 내역을 확인한 뒤 다시 시도하세요.");
    await expect(sheet(page)).toHaveCount(0);
    await expect.poll(() => accountRequests.length).toBe(2);
    await expect.poll(() => openOrderRequests.length).toBe(3);
    expect(cancels).toHaveLength(1);
  });
}

test("주문 취소 최종 확인이 가로 스크롤을 만들지 않는다", async ({ page }) => {
  await mockApi(page);
  await open(page);
  await openCancel(page);
  await expect(submitCancel(page)).toBeEnabled();

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

// ---- 일부 수량 취소 ----

const quantityInput = (page: Page) => sheet(page).getByLabel("취소 수량 (주)");

test("취소 수량은 최신 미체결 수량으로 채워지고, 줄여서 일부만 취소할 수 있다", async ({ page }) => {
  const { cancels } = await mockApi(page);
  await open(page);
  await openCancel(page);

  await expect(quantityInput(page)).toHaveValue("3");
  await quantityInput(page).fill("1");
  await sheet(page).getByRole("button", { name: "전부" }).click();
  await expect(quantityInput(page)).toHaveValue("3");
  await quantityInput(page).fill("1");
  await submitCancel(page).click();

  await expect(sheet(page)).toHaveCount(0);
  expect(cancels[0].quantity).toBe("1");
});

for (const [value, message] of [
  ["0", "수량은 1주 이상"],
  ["1.5", "수량은 1주 이상"],
  ["4", "미체결 수량(3주)보다 많이 취소할 수 없습니다."],
] as const) {
  test(`취소 수량 ${value}은 안내와 함께 막힌다`, async ({ page }) => {
    await mockApi(page);
    await open(page);
    await openCancel(page);
    await expect(quantityInput(page)).toHaveValue("3");

    await quantityInput(page).fill(value);

    await expect(sheet(page)).toContainText(message);
    await expect(submitCancel(page)).toBeDisabled();
  });
}

test("수량 초과로 거부되면 다시 확인해 줄어든 미체결 수량을 보여주고, 입력 값은 그대로 둔다", async ({ page }) => {
  let remaining = 3;
  const { openOrderRequests, cancels } = await mockApi(page, {
    openOrders: () => listed([openOrder({ remaining_quantity: remaining })]),
    cancel: (body) => {
      if (cancels.length > 1) return cancelAccepted(body);
      remaining = 1;
      return failure(400, "cancel_quantity_exceeded", "미체결 수량(1주)을 넘어 취소하지 않았습니다.");
    },
  });
  await open(page);
  await openCancel(page);
  await quantityInput(page).fill("2");

  await submitCancel(page).click();

  await expect(page.getByRole("alert").filter({ hasText: "미체결 수량(1주)을 넘어" })).toBeVisible();
  await expect(sheet(page).getByRole("note")).toContainText("미체결 수량이 1주로 줄었습니다");
  await expect(quantityInput(page)).toHaveValue("2");
  await expect(sheet(page).locator('dd[data-term="미체결 수량"]')).toHaveText("1주");
  await expect(submitCancel(page)).toBeDisabled();
  expect(openOrderRequests).toHaveLength(3);

  await sheet(page).getByRole("button", { name: "전부" }).click();
  await expect(quantityInput(page)).toHaveValue("1");
  await submitCancel(page).click();
  await expect(sheet(page)).toHaveCount(0);
  expect(cancels.map((c) => c.quantity)).toEqual(["2", "1"]);
});
