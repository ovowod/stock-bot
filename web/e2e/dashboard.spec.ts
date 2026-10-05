import { expect, test, type Page, type Route } from "@playwright/test";

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
      name: "애플",
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

async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

test("처음에는 국내 모의로 열리고 계좌 요약·예수금·보유종목을 보여준다", async ({ page }, info) => {
  await mockAccount(page, { domestic_paper: { body: domestic() } });
  await page.goto("/");

  await expect(envButton(page, "국내 모의")).toHaveAttribute("aria-checked", "true");
  await expect(page.getByRole("heading", { name: "계좌 확인" })).toBeVisible();
  if (!info.project.name.includes("mobile")) {
    await expect(page.getByRole("button", { name: "계좌 확인" })).toHaveAttribute("aria-current", "page");
  }
  await expect(page.getByText("512,345,678원")).toBeVisible();
  await expect(page.getByText("8135****11")).toBeVisible();
  await expect(page.getByText("실전", { exact: true })).toHaveCount(0);
  await expect(visibleText(page, "삼성전자")).toBeVisible();
  await expectNoHorizontalScroll(page);
  await page.screenshot({ path: `screenshots/${info.project.name}-domestic-paper.png`, fullPage: true });
});

test("수익은 빨강 ▲ +, 손실은 파랑 ▼ − 로 표시한다", async ({ page }) => {
  await mockAccount(page, { domestic_paper: { body: domestic() } });
  await page.goto("/");

  const gain = visibleText(page, "+1,450,000원");
  const loss = visibleText(page, "−104,000원");
  await expect(gain).toBeVisible();
  await expect(loss).toBeVisible();
  const color = (locator: typeof gain) => locator.evaluate((el) => getComputedStyle(el).color);
  expect(await color(gain)).toBe("rgb(209, 42, 42)");
  expect(await color(loss)).toBe("rgb(31, 95, 209)");
  await expect(gain).toContainText("▲");
  await expect(loss).toContainText("▼");
});

test("부호 없이 표시하는 금액도 음수면 − 를 유지한다", async ({ page }) => {
  const body = domestic();
  body.deposit.d2_estimated = -50_000;
  await mockAccount(page, { domestic_paper: { body } });
  await page.goto("/");

  await expect(visibleText(page, "−50,000원")).toBeVisible();
  await expect(visibleText(page, "−50,000원")).not.toContainText("▼");
});

test("실전 환경을 고르면 경고와 실전 배지를 보여준다", async ({ page }, info) => {
  await mockAccount(page, {
    domestic_paper: { body: domestic() },
    domestic_real: { body: domestic("domestic_real") },
    us_real: { body: us("us_real") },
  });
  await page.goto("/");
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
  await page.goto("/");
  await envButton(page, "미국 모의").click();

  await expect(page.getByText("$156,464.67")).toBeVisible();
  await expect(page.getByText("238,530,390원")).toBeVisible();
  await expect(page.getByText("미국달러")).toBeVisible();
  await expect(visibleText(page, "AAPL · 미국")).toBeVisible();
  await expectNoHorizontalScroll(page);
  await page.screenshot({ path: `screenshots/${info.project.name}-us-paper.png`, fullPage: true });
});

test("마지막으로 고른 투자 환경으로 다시 열린다", async ({ page }) => {
  await mockAccount(page, { domestic_paper: { body: domestic() }, us_paper: { body: us() } });
  await page.goto("/");
  await envButton(page, "미국 모의").click();
  await page.reload();

  await expect(envButton(page, "미국 모의")).toHaveAttribute("aria-checked", "true");
});

test("저장된 값이 잘못되면 국내 모의로 연다", async ({ page }) => {
  await mockAccount(page, { domestic_paper: { body: domestic() } });
  await page.addInitScript(() => localStorage.setItem("stock-bot:environment", "real"));
  await page.goto("/");

  await expect(envButton(page, "국내 모의")).toHaveAttribute("aria-checked", "true");
});

test("불러오는 동안 skeleton을 보여준다", async ({ page }, info) => {
  await mockAccount(page, { domestic_paper: { body: domestic(), delayMs: 1500 } });
  await page.goto("/");

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
  await page.goto("/");
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
  await page.goto("/");

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
  await page.goto("/");

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
  await page.goto("/");

  await expect(page.getByRole("alert")).toContainText("호출 한도를 넘었습니다");
  await expect(page.getByRole("alert")).toContainText("잠시 후 다시 시도하세요");
});

test("환경을 바꾸면 이전 환경 데이터를 즉시 지우고, 늦게 온 이전 응답은 반영하지 않는다", async ({ page }) => {
  let domesticSlow = false;
  await mockAccount(page, {
    domestic_paper: () => ({ body: domestic(), delayMs: domesticSlow ? 1500 : 0 }),
    us_paper: { body: us(), delayMs: 800 },
  });
  await page.goto("/");
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
  await page.goto("/");

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
    await page.goto("/");
    await envButton(page, label).click();
    await expect(page.getByRole("heading", { name: "예수금" })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("alert")).toHaveCount(0);
    const firstStat = page.locator("main p.text-lg").first();
    await expect(firstStat).toHaveText(amountPattern);
    await page.screenshot({ path: `screenshots/live-${info.project.name}-${label}.png`, fullPage: true });
  });
}
