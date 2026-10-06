import { expect, test, type Page, type Route } from "@playwright/test";
import { openDashboard } from "./support";

// 브라우저의 /api 요청을 가로채 가짜 응답을 준다. 키움 서버나 실전 서버는 호출되지 않는다.

const domestic = (environment = "domestic_paper", holdings = DOMESTIC_HOLDINGS) => ({
  environment,
  market: "domestic",
  account_no: "8135****11",
  fetched_at: "2026-10-05T06:30:00+00:00",
  summary: {
    estimated_assets: 512_345_678,
    total_evaluation: 12_345_678,
    total_purchase: 11_000_000,
    total_profit_loss: 1_345_678,
    total_return_rate: 12.23,
  },
  deposit: {
    deposit: 500_000_000,
    orderable: 499_000_000,
    withdrawable: 498_000_000,
    d1_estimated: 500_000_000,
    d2_estimated: 500_000_000,
  },
  holdings,
});

const DOMESTIC_HOLDINGS = [
  {
    code: "005930",
    name: "삼성전자",
    quantity: 100,
    tradable_quantity: 100,
    purchase_price: 70_000,
    current_price: 84_500,
    purchase_amount: 7_000_000,
    evaluation_amount: 8_450_000,
    profit_loss: 1_450_000,
    return_rate: 20.71,
    weight: 68.45,
  },
  {
    code: "035420",
    name: "NAVER",
    quantity: 20,
    tradable_quantity: 20,
    purchase_price: 200_000,
    current_price: 194_800,
    purchase_amount: 4_000_000,
    evaluation_amount: 3_896_000,
    profit_loss: -104_000,
    return_rate: -2.6,
    weight: 31.55,
  },
];

const us = (environment = "us_paper") => ({
  environment,
  market: "us",
  account_no: "6111****41",
  fetched_at: "2026-10-05T06:30:00+00:00",
  currency: "USD",
  summary: {
    total_evaluation: 156464.67,
    total_purchase: 157279.97,
    total_profit_loss: -1599.66,
    total_return_rate: -1.01,
    today_realized_profit_loss: 0,
    today_realized_return_rate: 0,
  },
  summary_krw: {
    total_evaluation: 238530390,
    total_purchase: 239773317,
    total_profit_loss: -2438684,
    today_realized_profit_loss: 0,
  },
  deposit: {
    krw_deposit: 930907881,
    currencies: [
      { currency: "USD", currency_name: "미국달러", deposit: 100000, orderable: 100000, withdrawable: 100000 },
    ],
  },
  holdings: [
    {
      code: "AAPL",
      name: "MSCI 브라질 아이셰어즈 ETF 긴 이름",
      exchange: "미국",
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
    },
  ],
});

type Reply = { status?: number; body: unknown; delayMs?: number };

async function mockAccount(page: Page, replies: Record<string, Reply | (() => Reply)>) {
  await page.route("**/api/environments/*/account", async (route: Route) => {
    const environment = new URL(route.request().url()).pathname.split("/")[3];
    const entry = replies[environment];
    if (!entry) return route.fulfill({ status: 500, json: { error: { kind: "test", message: "unmocked" } } });
    const reply = typeof entry === "function" ? entry() : entry;
    if (reply.delayMs) await new Promise((resolve) => setTimeout(resolve, reply.delayMs));
    await route.fulfill({ status: reply.status ?? 200, json: reply.body }).catch(() => {});
  });
}

const envButton = (page: Page, label: string) => page.getByRole("radio", { name: label });
// 데스크톱 표와 모바일 카드가 함께 DOM에 있으므로 보이는 요소만 고른다.
const visibleText = (page: Page, text: string) => page.getByText(text).filter({ visible: true }).first();

/** 모바일 보유종목 줄을 눌러 펼친 세부 정보에서 항목 값을 찾는다. */
const holdingDetail = (row: ReturnType<Page["getByRole"]>, term: string) => row.locator(`dd[data-term="${term}"]`);

async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  // 내용이 넓으면 모바일 브라우저는 화면 자체를 넓혀 버려 위 검사만으로는 잡히지 않는다.
  expect(await page.evaluate(() => window.innerWidth)).toBe(page.viewportSize()!.width);
}

test("처음에는 국내 모의로 열리고 계좌 요약·예수금·보유종목을 보여준다", async ({ page }, info) => {
  await mockAccount(page, { domestic_paper: { body: domestic() } });
  await openDashboard(page);

  await expect(envButton(page, "국내 모의")).toHaveAttribute("aria-checked", "true");
  await expect(page.getByRole("heading", { name: "계좌 확인" })).toBeVisible();
  if (!info.project.name.includes("mobile")) {
    await expect(page.getByRole("button", { name: "계좌 확인" })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("group", { name: "자산" }).getByRole("button", { name: "계좌 확인" })).toBeVisible();
  }
  await expect(page.getByText("512,345,678원")).toBeVisible();
  await expect(page.getByText("8135****11")).toBeVisible();
  await expect(page.getByText("실전", { exact: true })).toHaveCount(0);
  await expect(visibleText(page, "삼성전자")).toBeVisible();
  const samsung = page.getByRole("listitem").filter({ hasText: "삼성전자" }).filter({ visible: true }).first();
  if (info.project.name.includes("mobile")) {
    // 모바일 목록은 이름·수량과 평가금액·손익만 보여주고, 줄을 누르면 세부 정보를 펼친다.
    const toggle = samsung.locator("button[aria-expanded]");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(samsung.getByText("100주", { exact: true }).filter({ visible: true })).toBeVisible();
    await expect(samsung.getByText("8,450,000원", { exact: true }).filter({ visible: true })).toBeVisible();
    await expect(samsung.getByText("(+20.71%)", { exact: true }).filter({ visible: true })).toBeVisible();
    await expect(holdingDetail(samsung, "현재가")).toBeHidden();
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(holdingDetail(samsung, "현재가")).toHaveText("84,500원");
    await expect(holdingDetail(samsung, "평균단가")).toHaveText("70,000원");
    await expect(holdingDetail(samsung, "종목코드")).toHaveText("005930");
    await expect(holdingDetail(samsung, "보유비중")).toHaveText("68.45%");
    await toggle.click();
    await expect(holdingDetail(samsung, "현재가")).toBeHidden();
  } else {
    // 넓은 화면: 왼쪽은 종목 정보(수량·현재가, 코드·평균단가·보유비중), 오른쪽은 평가금액과 손익이다.
    // 매입금액은 수량 × 평균단가라 따로 보여주지 않는다.
    await expect(samsung.getByText("100주 · 현재가 84,500원", { exact: true }).filter({ visible: true })).toBeVisible();
    const samsungInfo = samsung.getByText("005930 · 평균 70,000원 · 보유비중 68.45%", { exact: true }).filter({ visible: true });
    await expect(samsungInfo).toBeVisible();
    // 왼쪽과 오른쪽은 각각 위에서부터 차례로 쌓인다. 오른쪽 칸 아래로 넘어가지 않는다.
    const change = samsung.getByText("(+20.71%)", { exact: true }).filter({ visible: true });
    const value = samsung.getByText("8,450,000원", { exact: true }).filter({ visible: true });
    expect((await change.boundingBox())!.y).toBeLessThan((await value.boundingBox())!.y + 40);
    const infoBox = (await samsungInfo.boundingBox())!;
    expect(infoBox.x + infoBox.width).toBeLessThanOrEqual((await value.boundingBox())!.x + 1);
  }
  await expect(samsung).not.toContainText("매입금액");
  await expect(samsung).not.toContainText("매도가능");
  await expectNoHorizontalScroll(page);
  await page.screenshot({ path: `screenshots/${info.project.name}-domestic-paper.png`, fullPage: true });
});

test("수익은 빨강 +, 손실은 파랑 − 로 표시하고 수익률은 괄호로 붙인다", async ({ page }) => {
  await mockAccount(page, { domestic_paper: { body: domestic() } });
  await openDashboard(page);

  const gain = visibleText(page, "+1,450,000원");
  const loss = visibleText(page, "−104,000원");
  await expect(gain).toBeVisible();
  await expect(loss).toBeVisible();
  const color = (locator: typeof gain) => locator.evaluate((el) => getComputedStyle(el).color);
  expect(await color(gain)).toBe("rgb(240, 68, 82)");
  expect(await color(loss)).toBe("rgb(49, 130, 246)");
  // 화살표 없이 부호와 색으로만 방향을 보여주고, 수익률은 금액 뒤 괄호에 둔다.
  const holding = (name: string) =>
    page.getByRole("listitem").filter({ hasText: name }).filter({ visible: true }).first();
  await expect(holding("삼성전자")).not.toContainText("▲");
  await expect(holding("삼성전자")).not.toContainText("▼");
  await expect(visibleText(page, "(+20.71%)")).toBeVisible();
  await expect(visibleText(page, "(−2.60%)")).toBeVisible();
  await expect(visibleText(page, "(+12.23%)")).toBeVisible();
});

test("부호 없이 표시하는 금액도 음수면 − 를 유지한다", async ({ page }) => {
  const body = domestic();
  body.deposit.d2_estimated = -50_000;
  await mockAccount(page, { domestic_paper: { body } });
  await openDashboard(page);

  await expect(visibleText(page, "−50,000원")).toBeVisible();
  await expect(visibleText(page, "−50,000원")).not.toContainText("▼");
});

test("실전 환경을 고르면 경고와 실전 배지를 보여준다", async ({ page }, info) => {
  await mockAccount(page, {
    domestic_paper: { body: domestic() },
    domestic_real: { body: domestic("domestic_real") },
    us_real: { body: us("us_real") },
  });
  await openDashboard(page);
  await envButton(page, "국내 실전").click();

  await expect(page.getByText("실전", { exact: true })).toBeVisible();
  await expect(page.getByText("실제 계좌와 실제 자금을 다루는 실전투자 환경입니다.")).toBeVisible();
  await page.screenshot({ path: `screenshots/${info.project.name}-domestic-real.png`, fullPage: true });

  await envButton(page, "미국 실전").click();
  await expect(page.getByText("$156,464.67")).toBeVisible();
  await expect(page.getByText("실전", { exact: true })).toBeVisible();
});

test("미국 계좌는 USD와 원화 환산, 통화별 예수금, 거래소명을 보여준다", async ({ page }, info) => {
  await mockAccount(page, { domestic_paper: { body: domestic() }, us_paper: { body: us() } });
  await openDashboard(page);
  await envButton(page, "미국 모의").click();

  await expect(page.getByText("$156,464.67")).toBeVisible();
  await expect(page.getByText("238,530,390원")).toBeVisible();
  await expect(page.getByText("미국달러")).toBeVisible();
  // 외화예수금의 주문가능·출금가능은 각각 한 줄로, 낱말 중간에서 끊기지 않는다.
  for (const text of ["주문가능 $100,000.00", "출금가능 $100,000.00"]) {
    const line = visibleText(page, text);
    await expect(line).toHaveText(text);
    const lineHeight = await line.evaluate((el) => parseFloat(getComputedStyle(el).lineHeight));
    expect((await line.boundingBox())!.height).toBeLessThan(lineHeight * 1.5);
  }
  // 보유종목 이름은 말줄임(...)으로 자르지 않는다.
  const row = page.getByRole("listitem").filter({ hasText: "MSCI" }).filter({ visible: true }).first();
  const name = row.getByText("MSCI 브라질 아이셰어즈 ETF 긴 이름", { exact: true }).filter({ visible: true });
  const clipped = await name.evaluate((el) => el.scrollWidth > el.clientWidth || el.scrollHeight > el.clientHeight);
  expect(clipped).toBe(false);
  if (info.project.name.includes("mobile")) {
    // 모바일: 이름·수량과 평가금액·손익만 보이고, 누르면 원화 평가금액 등 세부 정보가 펼쳐진다.
    await expect(row.getByText("395주", { exact: true }).filter({ visible: true })).toBeVisible();
    await expect(row.getByText("$108,719.80", { exact: true }).filter({ visible: true })).toBeVisible();
    await row.getByRole("button", { name: /MSCI/ }).click();
    await expect(holdingDetail(row, "현재가")).toHaveText("$275.24");
    await expect(holdingDetail(row, "평균단가")).toHaveText("$282.1603");
    await expect(holdingDetail(row, "종목코드")).toHaveText("AAPL");
    await expect(holdingDetail(row, "거래소")).toHaveText("미국");
    await expect(holdingDetail(row, "원화 평가금액")).toHaveText("165,743,335원");
    await expect(holdingDetail(row, "매도가능")).toHaveCount(0);
  } else {
    // 넓은 화면: 1줄 이름, 2줄 수량·현재가, 3줄 종목코드·거래소·평균단가. 오른쪽은 평가금액·원화 금액·손익.
    await expect(row.getByText("395주 · 현재가 $275.24", { exact: true }).filter({ visible: true })).toBeVisible();
    await expect(row.getByText("AAPL · 미국 · 평균 $282.1603", { exact: true }).filter({ visible: true })).toBeVisible();
    await expect(row.getByText("165,743,335원", { exact: true }).filter({ visible: true })).toBeVisible();
    await expect(row).not.toContainText("평가금액(원)");
    await expect(row).not.toContainText("매도가능");
    const oneLine = async (locator: ReturnType<Page["getByText"]>) => {
      const lineHeight = await locator.evaluate((el) => parseFloat(getComputedStyle(el).lineHeight));
      expect((await locator.boundingBox())!.height, await locator.textContent()).toBeLessThan(lineHeight * 1.5);
    };
    await oneLine(name);
    // 줄이 좁으면 가운뎃점 자리에서만 바꾸고, 덩어리 안에서는 끊지 않는다.
    for (const part of ["평균 $282.1603", "현재가 $275.24", "165,743,335원"]) {
      await oneLine(row.getByText(part, { exact: true }).filter({ visible: true }));
    }
  }
  await expectNoHorizontalScroll(page);
  await page.screenshot({ path: `screenshots/${info.project.name}-us-paper.png`, fullPage: true });
});

test("매도가능 수량이 보유 수량보다 적으면 함께 보여준다", async ({ page }, info) => {
  const body = us();
  body.holdings[0].sellable_quantity = 390;
  await mockAccount(page, { domestic_paper: { body: domestic() }, us_paper: { body } });
  await openDashboard(page);
  await envButton(page, "미국 모의").click();

  if (info.project.name.includes("mobile")) {
    const row = page.getByRole("listitem").filter({ hasText: "MSCI" }).filter({ visible: true }).first();
    await row.getByRole("button", { name: /MSCI/ }).click();
    await expect(holdingDetail(row, "매도가능")).toHaveText("390주");
  } else {
    await expect(visibleText(page, "AAPL · 미국 · 평균 $282.1603 · 매도가능 390주")).toBeVisible();
  }
});

test("좁은 화면에서도 요약 카드의 큰 금액을 말줄임으로 자르지 않는다", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  const body = domestic();
  body.summary.total_evaluation = 123_456_789;
  body.summary.total_purchase = 110_000_000;
  await mockAccount(page, { domestic_paper: { body } });
  await openDashboard(page);

  for (const text of ["123,456,789원", "110,000,000원"]) {
    const value = visibleText(page, text);
    await expect(value).toHaveText(text);
    const clipped = await value.evaluate((el) => el.scrollWidth > el.clientWidth);
    expect(clipped, text).toBe(false);
  }
});

test("마지막으로 고른 투자 환경으로 다시 열린다", async ({ page }) => {
  await mockAccount(page, { domestic_paper: { body: domestic() }, us_paper: { body: us() } });
  await openDashboard(page);
  await envButton(page, "미국 모의").click();
  await page.reload();

  await expect(envButton(page, "미국 모의")).toHaveAttribute("aria-checked", "true");
});

test("저장된 값이 잘못되면 국내 모의로 연다", async ({ page }) => {
  await mockAccount(page, { domestic_paper: { body: domestic() } });
  await page.addInitScript(() => localStorage.setItem("stock-bot:environment", "real"));
  await openDashboard(page);

  await expect(envButton(page, "국내 모의")).toHaveAttribute("aria-checked", "true");
});

test("불러오는 동안 skeleton을 보여준다", async ({ page }, info) => {
  await mockAccount(page, { domestic_paper: { body: domestic(), delayMs: 1500 } });
  await openDashboard(page);

  await expect(page.getByRole("status", { name: "계좌 정보를 불러오는 중" })).toBeVisible();
  await expect(page.getByRole("button", { name: "새로고침" })).toBeDisabled();
  await page.screenshot({ path: `screenshots/${info.project.name}-loading.png` });
  await expect(page.getByText("512,345,678원")).toBeVisible();
});

test("새로고침 중에는 기존 데이터를 유지하고 버튼을 비활성화한다", async ({ page }) => {
  let slow = false;
  await mockAccount(page, {
    domestic_paper: () => ({ body: domestic(), delayMs: slow ? 1500 : 0 }),
  });
  await openDashboard(page);
  await expect(page.getByText("512,345,678원")).toBeVisible();

  slow = true;
  await page.getByRole("button", { name: "새로고침" }).click();
  const refreshing = page.getByRole("button", { name: "새로고침 중" });
  await expect(refreshing).toBeDisabled();
  await expect(page.getByText("512,345,678원")).toBeVisible();
  await expect(page.getByRole("button", { name: "새로고침", exact: true })).toBeEnabled();
});

test("보유종목이 없으면 빈 상태를 안내하고 요약은 그대로 보여준다", async ({ page }, info) => {
  await mockAccount(page, { domestic_paper: { body: domestic("domestic_paper", []) } });
  await openDashboard(page);

  await expect(page.getByText("보유종목이 없습니다")).toBeVisible();
  await expect(page.getByText("512,345,678원")).toBeVisible();
  await page.screenshot({ path: `screenshots/${info.project.name}-empty.png`, fullPage: true });
});

test("설정 오류는 빠진 환경변수 이름과 다시 시도 버튼을 보여준다", async ({ page }, info) => {
  let failing = true;
  await mockAccount(page, {
    domestic_paper: () =>
      failing
        ? {
            status: 503,
            body: {
              error: {
                kind: "config_error",
                message: "환경변수가 설정되지 않았습니다: PAPER_KR_APP_SECRET",
                request_id: "abc123",
                missing: ["PAPER_KR_APP_SECRET"],
              },
            },
          }
        : { body: domestic() },
  });
  await openDashboard(page);

  await expect(page.getByRole("alert")).toContainText("설정을 확인해야 합니다");
  await expect(page.getByRole("alert")).toContainText("PAPER_KR_APP_SECRET");
  await expect(page.getByRole("alert")).toContainText("요청 ID: abc123");
  await page.screenshot({ path: `screenshots/${info.project.name}-error.png`, fullPage: true });

  failing = false;
  await page.getByRole("button", { name: "다시 시도" }).click();
  await expect(page.getByText("512,345,678원")).toBeVisible();
});

test("호출 한도 초과는 잠시 후 다시 시도하라고 안내한다", async ({ page }) => {
  await mockAccount(page, {
    domestic_paper: {
      status: 429,
      body: {
        error: {
          kind: "rate_limited",
          message: "키움 API 호출 한도를 넘었습니다. 잠시 후 다시 시도하세요. [1700]",
          request_id: "r1",
        },
      },
    },
  });
  await openDashboard(page);

  await expect(page.getByRole("alert")).toContainText("호출 한도를 넘었습니다");
  await expect(page.getByRole("alert")).toContainText("잠시 후 다시 시도하세요");
});

test("환경을 바꾸면 이전 환경 데이터를 즉시 지우고, 늦게 온 이전 응답은 반영하지 않는다", async ({ page }) => {
  let domesticSlow = false;
  await mockAccount(page, {
    domestic_paper: () => ({ body: domestic(), delayMs: domesticSlow ? 1500 : 0 }),
    us_paper: { body: us(), delayMs: 800 },
  });
  await openDashboard(page);
  await expect(page.getByText("512,345,678원")).toBeVisible();

  // 국내 → 미국: 미국 응답이 오기 전에도 국내 데이터가 남아 있으면 안 된다.
  await envButton(page, "미국 모의").click();
  await expect(page.getByText("512,345,678원")).toHaveCount(0);
  await expect(page.getByRole("status", { name: "계좌 정보를 불러오는 중" })).toBeVisible();
  await expect(page.getByText("$156,464.67")).toBeVisible();

  domesticSlow = true;
  // 미국 → 국내(느림) → 바로 미국: 늦게 도착한 국내 응답이 미국 화면을 덮으면 안 된다.
  await envButton(page, "국내 모의").click();
  await envButton(page, "미국 모의").click();
  await expect(page.getByText("$156,464.67")).toBeVisible();
  await page.waitForTimeout(1800);
  await expect(page.getByText("512,345,678원")).toHaveCount(0);
  await expect(page.getByText("$156,464.67")).toBeVisible();
});

test("모바일에서는 메뉴 서랍을 열고 닫을 수 있다", async ({ page, isMobile }, info) => {
  test.skip(!isMobile, "모바일 전용");
  await mockAccount(page, { domestic_paper: { body: domestic() } });
  await openDashboard(page);

  await page.getByRole("button", { name: "메뉴 열기" }).click();
  const drawer = page.getByRole("dialog", { name: "메뉴" });
  await expect(drawer).toBeVisible();
  await page.screenshot({ path: `screenshots/${info.project.name}-drawer.png` });
  await drawer.getByRole("button", { name: "계좌 확인" }).click();
  await expect(drawer).toBeHidden();
});

// 실제 서버 확인용. 백엔드를 띄운 상태에서 `pnpm test:live`로만 실행한다. 모의 환경만 호출한다.
for (const [label, amountPattern] of [
  ["국내 모의", /원$/],
  ["미국 모의", /^\$/],
] as const) {
  test(`@live ${label} 실제 모의 서버 조회`, async ({ page }, info) => {
    await openDashboard(page);
    await envButton(page, label).click();
    await expect(page.getByRole("heading", { name: "예수금" })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("alert")).toHaveCount(0);
    const firstStat = page.getByTestId("hero-value");
    await expect(firstStat).toHaveText(amountPattern);
    await page.screenshot({ path: `screenshots/live-${info.project.name}-${label}.png`, fullPage: true });
  });
}
