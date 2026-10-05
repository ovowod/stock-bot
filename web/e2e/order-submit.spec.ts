import { expect, test, type Page } from "@playwright/test";

// 브라우저의 /api 요청을 가로채 가짜 응답을 준다. 주문 요청도 가짜 서버가 받으며 키움 서버는 호출되지 않는다.

const RANKING_ITEM = {
  rank: 1,
  code: "000660",
  name: "SK하이닉스",
  exchange: null,
  price: 184_100,
  direction: "up",
  change_rate: 1.5,
  trading_value: 1_000_000_000,
  previous_rank: 1,
  volume: 1000,
  rank_change: 0,
};

const US_ITEM = { ...RANKING_ITEM, code: "SOXL", name: "디렉시온 반도체", exchange: "NYSE", price: 162.6 };
const QUOTES: Record<string, number> = { "000660": 190_000, SOXL: 170.25 };

type Reply = {
  status?: number;
  body?: unknown;
  text?: string;
  delayMs?: number;
  headers?: Record<string, string>;
  abort?: boolean;
  hang?: boolean;
};
type OrderRequest = { environment: string; body: Record<string, unknown> };

const accepted = (orderNo: string): ((request: OrderRequest) => Reply) => (request) => ({
  body: { order_key: request.body.order_key, order_no: orderNo, accepted_at: "2026-10-05T01:00:00+00:00" },
  headers: { "X-Request-ID": "req-ok-1" },
});

async function mockApi(page: Page, order: (request: OrderRequest) => Reply = accepted("00024")) {
  const orders: OrderRequest[] = [];
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const [, , , environment, resource, kind] = url.pathname.split("/");
    const market = environment?.startsWith("us") ? "us" : "domestic";
    if (resource === "orders") {
      const request = { environment, body: route.request().postDataJSON() };
      orders.push(request);
      const reply = order(request);
      if (reply.hang) return;
      if (reply.delayMs) await new Promise((resolve) => setTimeout(resolve, reply.delayMs));
      if (reply.abort) {
        await route.abort("connectionreset");
      } else if (reply.text !== undefined) {
        await route.fulfill({ status: reply.status ?? 200, contentType: "text/html", body: reply.text });
      } else {
        await route
          .fulfill({ status: reply.status ?? 200, json: reply.body, headers: reply.headers })
          .catch(() => {});
      }
    } else if (resource === "quote") {
      const code = url.searchParams.get("code") ?? "";
      await route.fulfill({ json: { code, price: QUOTES[code], fetched_at: "2026-10-05T01:00:00+00:00" } });
    } else if (resource === "rankings") {
      await route.fulfill({
        json: {
          environment,
          market,
          kind,
          exchange: "all",
          fetched_at: "2026-10-05T01:00:00+00:00",
          items: market === "domestic" ? [RANKING_ITEM] : [US_ITEM],
        },
      });
    } else {
      await route.fulfill({ status: 503, json: { error: { kind: "config_error", message: "test" } } });
    }
  });
  return orders;
}

async function openPanel(page: Page, environment = "국내 모의", name = "SK하이닉스", price = "190,000") {
  await page.goto("/");
  await page.getByRole("radio", { name: environment }).filter({ visible: true }).click();
  const menu = page.getByRole("button", { name: "메뉴 열기" });
  if (await menu.isVisible()) await menu.click();
  await page.getByRole("button", { name: "순위" }).filter({ visible: true }).click();
  await page.getByRole("region", { name: "거래대금 상위" }).getByRole("button", { name }).click();
  const dialog = page.getByRole("dialog", { name: `${name} 주문` });
  await expect(dialog.getByLabel(/가격/)).toHaveValue(price);
  return dialog;
}

const confirmation = (page: Page) => page.getByRole("region", { name: "최종 확인" });

test("국내 모의에서 최종 확인을 거쳐 지정가 매수 주문을 보내고, 접수 알림을 본다", async ({ page }) => {
  const orders = await mockApi(page);
  const holdingRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/holdings/")) holdingRequests.push(request.url());
  });
  const dialog = await openPanel(page);
  await dialog.getByLabel("수량 (주)").fill("2");
  await dialog.getByRole("button", { name: "매수" }).click();

  const confirm = confirmation(page);
  await expect(confirm.getByText("모의투자 주문")).toBeVisible();
  for (const [term, value] of [
    ["투자 환경", "국내 모의"],
    ["종목", "SK하이닉스"],
    ["종목코드", "000660"],
    ["거래소", "KRX"],
    ["주문 유형", "지정가"],
    ["가격", "190,000원"],
    ["수량", "2주"],
    ["예상 주문금액", "380,000원"],
  ]) {
    await expect(confirm.locator(`dd[data-term="${term}"]`)).toHaveText(value);
  }
  expect(orders).toEqual([]);

  // 매수는 최종 확인에서 잔고를 다시 조회하지 않는다.
  expect(holdingRequests).toEqual([]);
  await confirm.getByRole("button", { name: "주문하기" }).click();
  const toast = page.getByRole("status").filter({ hasText: "매수 주문이 접수되었습니다" });
  await expect(toast).toContainText("주문번호 00024");
  await expect(dialog).toHaveCount(0);

  expect(orders).toHaveLength(1);
  const { order_key: orderKey, ...rest } = orders[0].body;
  expect(rest).toEqual({ side: "buy", code: "000660", order_type: "limit", quantity: "2", price: "190000" });
  expect(typeof orderKey).toBe("string");
  await expect(toast).toContainText(`주문 키 ${orderKey}`);
  await expect(toast).toContainText("요청 ID req-ok-1");
  // 성공 알림은 잠시 뒤 저절로 사라진다.
  await expect(toast).toHaveCount(0, { timeout: 7000 });
});

test("최종 확인에서 취소하면 입력 값 그대로 돌아간다", async ({ page }) => {
  const orders = await mockApi(page);
  const dialog = await openPanel(page);
  await dialog.getByLabel("수량 (주)").fill("5");
  await dialog.getByRole("button", { name: "매수" }).click();
  await confirmation(page).getByRole("button", { name: "취소" }).click();

  await expect(confirmation(page)).toHaveCount(0);
  await expect(dialog.getByLabel("수량 (주)")).toHaveValue("5");
  await expect(dialog.getByLabel("가격 (원)")).toHaveValue("190,000");
  expect(orders).toEqual([]);
});

test("키움이 거부하면 실패 알림이 남고, 패널은 입력 값과 함께 입력 화면으로 돌아간다", async ({ page }) => {
  await mockApi(page, () => ({
    status: 502,
    body: {
      error: { kind: "kiwoom_error", message: "키움 오류 [20] 주문가능금액이 부족합니다", request_id: "req-err-1" },
    },
  }));
  const dialog = await openPanel(page);
  await dialog.getByLabel("수량 (주)").fill("7");
  await dialog.getByRole("button", { name: "매수" }).click();
  await confirmation(page).getByRole("button", { name: "주문하기" }).click();

  const alert = page.getByRole("alert").filter({ hasText: "주문하지 못했습니다" });
  await expect(alert).toContainText("주문가능금액이 부족합니다");
  await expect(alert).toContainText("요청 ID req-err-1");
  await expect(alert).toContainText("주문 키");
  await expect(dialog.getByLabel("수량 (주)")).toHaveValue("7");
  await expect(confirmation(page)).toHaveCount(0);

  // 실패 알림은 저절로 사라지지 않고, 닫기를 눌러야 사라진다.
  await page.waitForTimeout(5500);
  await expect(alert).toBeVisible();
  await alert.getByRole("button", { name: "알림 닫기" }).click();
  await expect(alert).toHaveCount(0);
});

test("알림이 여러 개면 최신 알림이 위에 오고, 최종 확인마다 새 주문 키를 쓴다", async ({ page }) => {
  let count = 0;
  const orders = await mockApi(page, () => ({
    status: 502,
    body: { error: { kind: "kiwoom_error", message: `거부 ${++count}`, request_id: `req-${count}` } },
  }));
  const dialog = await openPanel(page);
  for (let i = 0; i < 2; i += 1) {
    await dialog.getByRole("button", { name: "매수" }).click();
    await confirmation(page).getByRole("button", { name: "주문하기" }).click();
    await expect(page.getByRole("alert")).toHaveCount(i + 1);
  }
  await expect(page.getByRole("alert").first()).toContainText("거부 2");
  expect(orders[0].body.order_key).not.toEqual(orders[1].body.order_key);
});

test("주문하기를 누르면 결과가 올 때까지 버튼이 잠겨 여러 번 눌러도 주문은 하나다", async ({ page }) => {
  const orders = await mockApi(page, (request) => ({ ...accepted("00031")(request), delayMs: 1000 }));
  const dialog = await openPanel(page);
  await dialog.getByRole("button", { name: "매수" }).click();
  const submit = confirmation(page).getByRole("button", { name: /주문/ }).last();
  await submit.click();
  await expect(confirmation(page).getByRole("button", { name: "주문 중…" })).toBeDisabled();
  await submit.click({ force: true });
  await submit.click({ force: true });
  await expect(page.getByRole("status").filter({ hasText: "주문번호 00031" })).toBeVisible();
  expect(orders).toHaveLength(1);
});

test("입력이 올바르지 않거나 너무 길면 매수 버튼이 눌리지 않는다", async ({ page }) => {
  await mockApi(page);
  const dialog = await openPanel(page);
  await dialog.getByLabel("수량 (주)").fill("1234567890123");
  await expect(dialog.getByText("수량은 1주 이상, 12자리 이하의 정수로 입력하세요.")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "매수" })).toBeDisabled();
  await dialog.getByLabel("수량 (주)").fill("1");
  await dialog.getByLabel("가격 (원)").fill("1234567890123");
  await expect(dialog.getByText("가격은 1원 이상, 12자리 이하의 정수로 입력하세요.")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "매수" })).toBeDisabled();
});

test("실전투자에서는 매수 버튼 대신 안내가 보이고 주문 요청이 나가지 않는다", async ({ page }) => {
  const orders = await mockApi(page);
  const dialog = await openPanel(page, "국내 실전");
  await expect(dialog.getByText("실전투자에서는 주문할 수 없습니다.")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "매수" })).toHaveCount(0);
  await expect(dialog.getByLabel("수량 (주)")).toHaveValue("1");
  expect(orders).toEqual([]);
});

test("알림이 떠도 가로 스크롤이 생기지 않는다", async ({ page }) => {
  await mockApi(page);
  const dialog = await openPanel(page);
  await dialog.getByRole("button", { name: "매수" }).click();
  await confirmation(page).getByRole("button", { name: "주문하기" }).click();
  await expect(page.getByRole("status").filter({ hasText: "매수 주문이 접수되었습니다" })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test("국내 시장가 주문은 가격 없이 보내고, 최종 확인에 시장가로 보인다", async ({ page }) => {
  const orders = await mockApi(page);
  const dialog = await openPanel(page);
  await dialog.getByRole("radio", { name: "시장가" }).click();
  await dialog.getByRole("button", { name: "매수" }).click();

  const confirm = confirmation(page);
  await expect(confirm.locator('dd[data-term="주문 유형"]')).toHaveText("시장가");
  await expect(confirm.locator('dd[data-term="가격"]')).toHaveText("시장가");
  await expect(confirm.locator('dd[data-term="예상 주문금액"]')).toHaveCount(0);
  await confirm.getByRole("button", { name: "주문하기" }).click();

  await expect(page.getByRole("status").filter({ hasText: "매수 주문이 접수되었습니다" })).toBeVisible();
  const { order_key: _, ...rest } = orders[0].body;
  expect(rest).toEqual({ side: "buy", code: "000660", order_type: "market", quantity: "1" });
});

test("미국 모의에서 거래소와 USD 금액을 확인하고 지정가 주문을 보낸다", async ({ page }) => {
  const orders = await mockApi(page);
  const dialog = await openPanel(page, "미국 모의", "디렉시온 반도체", "170.25");
  await dialog.getByLabel("수량 (주)").fill("2");
  await dialog.getByRole("button", { name: "매수" }).click();

  const confirm = confirmation(page);
  await expect(confirm.locator('dd[data-term="투자 환경"]')).toHaveText("미국 모의");
  await expect(confirm.locator('dd[data-term="거래소"]')).toHaveText("NYSE");
  await expect(confirm.locator('dd[data-term="가격"]')).toHaveText("$170.25");
  await expect(confirm.locator('dd[data-term="예상 주문금액"]')).toHaveText("$340.50");
  await confirm.getByRole("button", { name: "주문하기" }).click();

  await expect(page.getByRole("status").filter({ hasText: "매수 주문이 접수되었습니다" })).toBeVisible();
  expect(orders[0].environment).toBe("us_paper");
  const { order_key: _, ...rest } = orders[0].body;
  expect(rest).toEqual({
    side: "buy",
    code: "SOXL",
    exchange: "NYSE",
    order_type: "limit",
    quantity: "2",
    price: "170.25",
  });
});

test("미국 실전에서도 매수 버튼 대신 안내가 보이고 주문 요청이 나가지 않는다", async ({ page }) => {
  const orders = await mockApi(page);
  const dialog = await openPanel(page, "미국 실전", "디렉시온 반도체", "170.25");
  await expect(dialog.getByText("실전투자에서는 주문할 수 없습니다.")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "매수" })).toHaveCount(0);
  expect(orders).toEqual([]);
});

const unknownAlert = (page: Page) => page.getByRole("alert").filter({ hasText: "접수 여부를 확인할 수 없습니다" });

for (const [title, reply] of [
  ["서버가 접수 여부 확인 불가로 응답", {
    status: 502,
    body: { error: { kind: "order_result_unknown", message: "확인 불가", request_id: "req-u-1" } },
  }],
  ["같은 주문 키가 이미 처리됨(409)", {
    status: 409,
    body: { error: { kind: "duplicate_order", message: "중복", request_id: "req-u-2" } },
  }],
  ["브라우저 연결이 끊김", { abort: true }],
  ["HTML 오류 페이지가 옴", { status: 502, text: "<html>Bad Gateway</html>" }],
] as [string, Reply][]) {
  test(`${title}: 접수 여부 확인 불가 알림을 보여주고 다시 보내지 않는다`, async ({ page }) => {
    const orders = await mockApi(page, () => reply);
    const dialog = await openPanel(page);
    await dialog.getByLabel("수량 (주)").fill("4");
    await dialog.getByRole("button", { name: "매수" }).click();
    await confirmation(page).getByRole("button", { name: "주문하기" }).click();

    const alert = unknownAlert(page);
    await expect(alert).toContainText("키움에서 주문 내역을 확인한 뒤 다시 주문하세요.");
    await expect(alert).toContainText(`주문 키 ${orders[0].body.order_key}`);
    await expect(page.getByRole("alert").filter({ hasText: "주문하지 못했습니다" })).toHaveCount(0);
    await expect(dialog.getByLabel("수량 (주)")).toHaveValue("4");
    await page.waitForTimeout(500);
    expect(orders).toHaveLength(1);
  });
}

test("30초 안에 응답이 없으면 요청을 끊고 접수 여부 확인 불가로 알린다", async ({ page }) => {
  await page.clock.install();
  const orders = await mockApi(page, () => ({ hang: true }));
  const dialog = await openPanel(page);
  await dialog.getByRole("button", { name: "매수" }).click();
  await confirmation(page).getByRole("button", { name: "주문하기" }).click();
  await expect(confirmation(page).getByRole("button", { name: "주문 중…" })).toBeVisible();

  await page.clock.fastForward(29_000);
  await expect(unknownAlert(page)).toHaveCount(0);
  await page.clock.fastForward(2_000);
  await expect(unknownAlert(page)).toBeVisible();
  await expect(dialog.getByRole("button", { name: "매수" })).toBeVisible();
  expect(orders).toHaveLength(1);
});

test("전송 중에는 닫기 버튼, Esc, 바깥 영역으로 패널을 닫을 수 없다", async ({ page }) => {
  await mockApi(page, (request) => ({ ...accepted("00040")(request), delayMs: 1500 }));
  const dialog = await openPanel(page);
  await dialog.getByRole("button", { name: "매수" }).click();
  await confirmation(page).getByRole("button", { name: "주문하기" }).click();

  await expect(dialog.getByRole("button", { name: "닫기", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "주문 패널 닫기" }).click({ position: { x: 5, y: 5 }, force: true });
  await expect(dialog).toBeVisible();
  await expect(confirmation(page).getByRole("button", { name: "취소" })).toBeDisabled();

  await expect(page.getByRole("status").filter({ hasText: "주문번호 00040" })).toBeVisible();
  await expect(dialog).toHaveCount(0);
});
