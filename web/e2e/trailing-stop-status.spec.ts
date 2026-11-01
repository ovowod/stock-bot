import { expect, test, type Locator, type Page, type Route } from "@playwright/test";
import { mockNoOpenOrders, openDashboard } from "./support";

// 브라우저의 /api 요청을 가로채 가짜 응답을 준다. 키움 서버나 실전 서버는 호출되지 않는다.

const holding = (code: string, name: string, overrides: Record<string, unknown> = {}) => ({
  code,
  name,
  quantity: 10,
  tradable_quantity: 10,
  purchase_price: 70_000,
  current_price: 84_500,
  purchase_amount: 700_000,
  evaluation_amount: 845_000,
  profit_loss: 145_000,
  return_rate: 20.71,
  weight: 50,
  credit: false,
  ...overrides,
});

const SAMSUNG = holding("005930", "삼성전자");
const NAVER = holding("035420", "NAVER", {
  purchase_price: 200_000,
  current_price: 194_800,
  evaluation_amount: 1_948_000,
  profit_loss: -52_000,
  return_rate: -2.6,
});
const HYNIX = holding("000660", "SK하이닉스", { purchase_price: 100_000, current_price: 101_000 });

const domestic = (environment: string, holdings: unknown[]) => ({
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
  deposit: { deposit: 0, orderable: 0, withdrawable: 0, d1_estimated: 0, d2_estimated: 0 },
  holdings,
});

const usHolding = (code: string, name: string, overrides: Record<string, unknown> = {}) => ({
  code,
  name,
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
  credit: false,
  ...overrides,
});

const us = (environment: string, holdings: unknown[]) => ({
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

const ON = { start: "09:00", end: "15:30", enabled: true, activation_profit: "5", drop_from_peak: "3" };

const status = (environment: string, extra: Record<string, unknown> = {}) => {
  const isUs = environment.startsWith("us");
  return {
    environment,
    market: isUs ? "us" : "domestic",
    timezone: isUs ? "America/New_York" : "Asia/Seoul",
    watching: false,
    not_watching_reason: environment.endsWith("real") ? "real" : "no_session",
    session: ON,
    excluded: [] as string[],
    stocks: [],
    ...extra,
  };
};

type Reply = { status?: number; body: unknown };

/** 계좌와 Trailing Stop 상태를 가짜로 응답한다. 상태 요청 수를 돌려준다. */
async function mockScreen(
  page: Page,
  accounts: Record<string, unknown>,
  reply: (environment: string) => Reply = (environment) => ({ body: status(environment) }),
) {
  const requests: string[] = [];
  await page.route("**/api/environments/*/account", (route: Route) => {
    const environment = new URL(route.request().url()).pathname.split("/")[3];
    const body = accounts[environment];
    if (!body) return route.fulfill({ status: 500, json: { error: { kind: "test", message: "unmocked" } } });
    return route.fulfill({ json: body });
  });
  await page.route("**/api/environments/*/trailing-stop/status", (route: Route) => {
    const environment = new URL(route.request().url()).pathname.split("/")[3];
    requests.push(environment);
    const { status: code = 200, body } = reply(environment);
    return route.fulfill({ status: code, json: body });
  });
  await mockNoOpenOrders(page);
  return requests;
}

const view = (page: Page, name: string) => page.getByRole("radiogroup", { name: "보유종목 보기" }).getByRole("radio", { name });
const envButton = (page: Page, label: string) => page.getByRole("radio", { name: label });
const row = (page: Page, name: string) =>
  page.getByRole("listitem").filter({ hasText: name }).filter({ visible: true }).first();
const visible = (scope: Page | Locator, text: string) => scope.getByText(text, { exact: true }).filter({ visible: true });

/** 화면 폭에 맞는 줄 열기 버튼. 넓은 화면과 좁은 화면 줄이 따로 있고 하나만 보인다. */
const toggle = (item: Locator) => item.locator("button[aria-expanded]").filter({ visible: true });

/** 줄을 눌러 펼친 상세에서 값을 확인한다. 넓은 화면도 상세는 펼쳐야 보인다. */
async function expectFact(item: Locator, term: string, value: string) {
  if ((await toggle(item).getAttribute("aria-expanded")) === "false") await toggle(item).click();
  await expect(item.locator(`dd[data-term="${term}"]`).filter({ visible: true })).toHaveText(value);
}

/**
 * 넓은 화면은 배지에 상태와 이유를 함께 보인다. 좁은 화면 배지는 상태 이름만 한 줄로 보인다.
 * 이유까지 담은 상태는 펼친 상세의 "상태"에 있다.
 */
async function expectStatus(item: Locator, mobile: boolean, text: string) {
  const [name, ...reasons] = text.split(" · ");
  const shown = () => toggle(item).evaluate((el) => (el as HTMLElement).innerText);
  if (mobile) {
    await expect.poll(shown).toContain(name);
    if (reasons.length > 0) expect(await shown()).not.toContain(reasons.join(" · "));
  } else {
    await expect.poll(shown).toContain(text);
  }
  await expectFact(item, "상태", text);
}

async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  expect(await page.evaluate(() => window.innerWidth)).toBe(page.viewportSize()!.width);
}

test("보기를 바꾸고 새로고침해도 고른 보기로 다시 열린다", async ({ page, isMobile }) => {
  const requests = await mockScreen(page, { domestic_paper: domestic("domestic_paper", [SAMSUNG]) });
  await openDashboard(page);

  // 기본 보기는 지금과 같고 상태를 받지 않는다.
  await expect(view(page, "기본")).toHaveAttribute("aria-checked", "true");
  await expect(visible(row(page, "삼성전자"), "845,000원")).toBeVisible();
  expect(requests).toEqual([]);

  await view(page, "Trailing Stop").click();
  await expectStatus(row(page, "삼성전자"), isMobile, "감시 안 함 · 켜진 세션 없음");
  await expect(visible(row(page, "삼성전자"), "845,000원")).toHaveCount(0);
  await expect(
    page.getByText("수익률은 시세 체결가 기준이며 수수료·세금은 빼지 않습니다. 고점은 켜진 세션 동안의 체결가로 정합니다."),
  ).toBeVisible();
  expect(requests).toEqual(["domestic_paper"]);

  await page.reload();
  await expect(view(page, "Trailing Stop")).toHaveAttribute("aria-checked", "true");
  await expectStatus(row(page, "삼성전자"), isMobile, "감시 안 함 · 켜진 세션 없음");

  await view(page, "기본").click();
  await expect(visible(row(page, "삼성전자"), "845,000원")).toBeVisible();
  await expect(page.getByText("수수료·세금은 빼지 않습니다", { exact: false })).toHaveCount(0);
});

test("브라우저 저장소를 쓸 수 없으면 기본 보기로 연다", async ({ page, isMobile }) => {
  await page.addInitScript(() => {
    Storage.prototype.getItem = () => {
      throw new Error("blocked");
    };
    Storage.prototype.setItem = () => {
      throw new Error("blocked");
    };
  });
  await mockScreen(page, { domestic_paper: domestic("domestic_paper", [SAMSUNG]) });
  await openDashboard(page);

  await expect(view(page, "기본")).toHaveAttribute("aria-checked", "true");
  // 저장하지 못해도 고른 보기는 지금 화면에서 동작한다.
  await view(page, "Trailing Stop").click();
  await expectStatus(row(page, "삼성전자"), isMobile, "감시 안 함 · 켜진 세션 없음");
});

test("수익률, 도달 여부, 제외종목, 감시 안 함을 보여준다", async ({ page, isMobile }, info) => {
  await mockScreen(page, { domestic_paper: domestic("domestic_paper", [SAMSUNG, NAVER, HYNIX]) }, (environment) => ({
    body: status(environment, { excluded: ["000660"] }),
  }));
  await openDashboard(page);
  await view(page, "Trailing Stop").click();

  // 84,500 / 70,000 = +20.71%: 기준 5% 이상이지만 감시하지 않아 활성화되지 않았다.
  const samsung = row(page, "삼성전자");
  await expectStatus(samsung, isMobile, "감시 안 함 · 켜진 세션 없음");
  await expect(visible(samsung, "+20.71%")).toBeVisible();
  await expectFact(samsung, "도달 여부", "도달 (세션 밖)");
  await expectFact(samsung, "제외종목", "아니요");

  const naver = row(page, "NAVER");
  await expect(visible(naver, "−2.60%")).toBeVisible();
  await expectFact(naver, "도달 여부", "미도달 (기준 5%)");

  const hynix = row(page, "SK하이닉스");
  await expect(visible(hynix, "제외종목").first()).toBeVisible();
  await expect(visible(hynix, "+1.00%")).toBeVisible();
  await expectFact(hynix, "제외종목", "예");

  // 매도 버튼은 그대로 둔다.
  if (isMobile) await expect(hynix.getByRole("button", { name: "SK하이닉스 매도" })).toBeVisible();
  else await expect(page.getByRole("button", { name: "삼성전자 매도" }).filter({ visible: true })).toBeVisible();
  await expectNoHorizontalScroll(page);
  await page.screenshot({ path: `screenshots/${info.project.name}-trailing-stop-view.png`, fullPage: true });
});

test("지금 세션이 없거나 그 세션에 활성화 수익률이 없으면 도달 여부는 기준 없음이다", async ({ page, isMobile }) => {
  let session: unknown = null;
  await mockScreen(page, { domestic_paper: domestic("domestic_paper", [SAMSUNG]) }, (environment) => ({
    body: status(environment, { session }),
  }));
  await openDashboard(page);
  await view(page, "Trailing Stop").click();

  const samsung = row(page, "삼성전자");
  await expect(visible(samsung, "+20.71%")).toBeVisible();
  await expectFact(samsung, "도달 여부", "기준 없음 (세션 없음)");

  // 꺼진 세션은 기준이 비어 있을 수 있다.
  session = { start: "09:00", end: "15:30", enabled: false, activation_profit: null, drop_from_peak: null };
  await page.reload();
  await expectFact(row(page, "삼성전자"), "도달 여부", "기준 없음");
});

test("평균 매입가나 가격이 없거나 0이면 수익률과 도달 여부는 –다", async ({ page, isMobile }) => {
  await mockScreen(page, {
    domestic_paper: domestic("domestic_paper", [
      holding("005930", "삼성전자", { purchase_price: 0 }),
      holding("035420", "NAVER", { current_price: null }),
    ]),
  });
  await openDashboard(page);
  await view(page, "Trailing Stop").click();

  for (const name of ["삼성전자", "NAVER"]) {
    const item = row(page, name);
    // 좁은 화면은 expectStatus가 상세를 펼치므로 그 전에 목록 줄의 수익률을 본다.
    await expect(visible(item, "–")).toBeVisible();
    await expectStatus(item, isMobile, "감시 안 함 · 켜진 세션 없음");
    await expectFact(item, "도달 여부", "–");
  }
});

test("실전 환경은 제외종목도 감시 안 함 · 실전투자이고 제외종목 여부는 따로 보인다", async ({ page, isMobile }) => {
  await mockScreen(
    page,
    {
      domestic_paper: domestic("domestic_paper", []),
      domestic_real: domestic("domestic_real", [SAMSUNG, HYNIX]),
    },
    (environment) => ({ body: status(environment, { session: { ...ON, enabled: false }, excluded: ["000660"] }) }),
  );
  await openDashboard(page);
  await envButton(page, "국내 실전").click();
  await view(page, "Trailing Stop").click();

  // 실전은 제외종목도 배지가 감시 안 함이다. expectStatus가 상세를 펼쳐 "제외종목" 칸 이름이 보이기 전에 센다.
  await expect(page.getByText("제외종목", { exact: true }).filter({ visible: true })).toHaveCount(0);
  for (const name of ["삼성전자", "SK하이닉스"]) {
    await expectStatus(row(page, name), isMobile, "감시 안 함 · 실전투자");
  }
  await expect(visible(row(page, "삼성전자"), "+20.71%")).toBeVisible();
  await expectFact(row(page, "SK하이닉스"), "제외종목", "예");
  // 실전은 세션 밖이 아니라 감시하지 않는 것이다.
  await expectFact(row(page, "삼성전자"), "도달 여부", "도달 (감시 안 함)");
});

test("같은 종목의 신용 줄은 대상 아님 · 신용이고 현금 줄은 따로 판정한다", async ({ page, isMobile }) => {
  await mockScreen(page, {
    domestic_paper: domestic("domestic_paper", [
      SAMSUNG,
      holding("005930", "삼성전자", { credit: true, quantity: 5, tradable_quantity: 5 }),
    ]),
  });
  await openDashboard(page);
  await view(page, "Trailing Stop").click();

  const rows = page.getByRole("listitem").filter({ hasText: "삼성전자" });
  await expectStatus(rows.nth(0), isMobile, "감시 안 함 · 켜진 세션 없음");
  await expectStatus(rows.nth(1), isMobile, "대상 아님 · 신용");
});

test("열려 있고 탭이 보이는 동안 3초마다 상태를 다시 받고, 탭이 숨으면 멈춘다", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-11-02T01:00:00Z") });
  const requests = await mockScreen(page, { domestic_paper: domestic("domestic_paper", [SAMSUNG]) });
  await openDashboard(page);
  // 화면의 시계를 멈추고 runFor로만 흘려, 간격을 정확히 확인한다.
  await page.clock.pauseAt(new Date("2026-11-02T01:01:00Z"));
  await view(page, "Trailing Stop").click();
  await expect.poll(() => requests.length).toBe(1);

  /** 화면 시계를 ms만큼 흘리고, 그 사이 나간 요청이 화면 밖까지 닿을 시간을 준다.
   * 요청이 늘어야 하는 곳은 부하가 큰 전체 실행에서도 기다리도록 expect.poll로 본다. */
  const advance = async (ms: number) => {
    await page.clock.runFor(ms);
    await new Promise((resolve) => setTimeout(resolve, 200));
  };
  await advance(2900);
  expect(requests.length).toBe(1);
  await advance(100);
  await expect.poll(() => requests.length).toBe(2);
  await advance(3000);
  await expect.poll(() => requests.length).toBe(3);

  const setVisibility = (state: "hidden" | "visible") =>
    page.evaluate((value) => {
      Object.defineProperty(document, "visibilityState", { value, configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
    }, state);
  await setVisibility("hidden");
  await advance(10_000);
  expect(requests.length).toBe(3);

  // 탭이 다시 보이면 기다리지 않고 바로 받는다.
  await setVisibility("visible");
  await expect.poll(() => requests.length).toBe(4);
  await advance(3000);
  await expect.poll(() => requests.length).toBe(5);

  // 기본 보기로 바꾸면 받지 않는다.
  await view(page, "기본").click();
  await advance(10_000);
  expect(requests.length).toBe(5);
});

test("조회가 실패하면 마지막 상태를 두고 실패 안내를 보인다", async ({ page, isMobile }) => {
  await page.clock.install();
  let failing = false;
  await mockScreen(page, { domestic_paper: domestic("domestic_paper", [SAMSUNG]) }, (environment) =>
    failing
      ? { status: 500, body: { error: { kind: "internal_error", message: "서버 내부 오류가 발생했습니다." } } }
      : { body: status(environment) },
  );
  await openDashboard(page);
  await view(page, "Trailing Stop").click();
  await expectStatus(row(page, "삼성전자"), isMobile, "감시 안 함 · 켜진 세션 없음");

  failing = true;
  await page.clock.runFor(3000);
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("Trailing Stop 상태를 불러오지 못했습니다. 마지막으로 받은 상태를 보여줍니다.");
  await expect(alert).toContainText("서버 내부 오류가 발생했습니다.");
  await expectStatus(row(page, "삼성전자"), isMobile, "감시 안 함 · 켜진 세션 없음");

  // 다시 받으면 안내를 지운다.
  failing = false;
  await page.clock.runFor(3000);
  await expect(alert).toHaveCount(0);
});

test("한 번도 받지 못했으면 상태 칸을 –로 둔다", async ({ page, isMobile }) => {
  await mockScreen(page, { domestic_paper: domestic("domestic_paper", [SAMSUNG]) }, () => ({
    status: 500,
    body: { error: { kind: "internal_error", message: "서버 내부 오류가 발생했습니다." } },
  }));
  await openDashboard(page);
  await view(page, "Trailing Stop").click();

  await expect(page.getByRole("alert")).toContainText("Trailing Stop 상태를 불러오지 못했습니다.");
  await expect(page.getByRole("alert")).not.toContainText("마지막으로 받은 상태");
  const samsung = row(page, "삼성전자");
  // 수익률은 계좌 응답으로 계산할 수 있다. 상태와 도달 여부, 제외종목 여부는 모른다.
  await expect(visible(samsung, "+20.71%")).toBeVisible();
  await expect(visible(samsung, "–")).toBeVisible();
  await expectFact(samsung, "도달 여부", "–");
  await expectFact(samsung, "제외종목", "–");
});

test("미국 계좌는 외화 표기를 그대로 두고 거래소 확인 불가 안내도 유지한다", async ({ page, isMobile }) => {
  await mockScreen(
    page,
    {
      domestic_paper: domestic("domestic_paper", []),
      us_paper: us("us_paper", [
        usHolding("AAPL", "애플"),
        usHolding("EWZ", "MSCI 브라질", { exchange: null, purchase_price: 30, current_price: 31.5 }),
      ]),
    },
    (environment) => ({ body: status(environment, { session: { ...ON, start: "09:30", end: "16:00" } }) }),
  );
  await openDashboard(page);
  await envButton(page, "미국 모의").click();
  await view(page, "Trailing Stop").click();

  // 275.24 / 282.1603 = −2.45%
  const apple = row(page, "애플");
  await expect(visible(apple, "−2.45%")).toBeVisible();
  await expectStatus(apple, isMobile, "감시 안 함 · 켜진 세션 없음");
  // 원화 평가금액 줄은 Trailing Stop 보기에 두지 않는다. 수량·현재가 줄은 그대로다.
  await expect(visible(apple, "165,743,335원")).toHaveCount(0);
  await expectFact(apple, "도달 여부", "미도달 (기준 5%)");
  if (!isMobile) await expect(visible(apple, "395주 · 현재가 $275.24")).toBeVisible();

  // 31.5 / 30 = +5.00%: 기준과 같으면 도달이다.
  const ewz = row(page, "MSCI 브라질");
  await expect(visible(ewz, "+5.00%")).toBeVisible();
  await expectFact(ewz, "도달 여부", "도달 (세션 밖)");
  await expect(visible(ewz, "앱에서 매도")).toBeVisible();
  await expect(page.getByRole("button", { name: "MSCI 브라질 매도" })).toHaveCount(0);
});

test("좁은 화면에서도 긴 상태가 잘리지 않고 가로로 넘치지 않는다", async ({ page }, info) => {
  await page.setViewportSize({ width: 360, height: 800 });
  await mockScreen(
    page,
    { domestic_paper: domestic("domestic_paper", [holding("069500", "KODEX 200 미국채혼합 아주 긴 이름의 ETF")]) },
    (environment) => ({ body: status(environment, { not_watching_reason: "settings_unreadable" }) }),
  );
  await openDashboard(page);
  await view(page, "Trailing Stop").click();

  const badge = visible(row(page, "KODEX"), "감시 안 함 · 설정 파일을 읽지 못함");
  await expect(badge).toBeVisible();
  const clipped = await badge.evaluate((el) => el.scrollWidth > el.clientWidth || el.scrollHeight > el.clientHeight);
  expect(clipped).toBe(false);
  await expectNoHorizontalScroll(page);
  await page.screenshot({ path: `screenshots/${info.project.name}-trailing-stop-narrow.png`, fullPage: true });
});

const stock = (code: string, overrides: Record<string, unknown> = {}) => ({
  code,
  status: "waiting",
  ineligible_reason: null,
  purchase_price: null,
  last_price: null,
  last_price_at: null,
  activated_at: null,
  peak: null,
  peak_at: null,
  triggered_at: null,
  triggered_price: null,
  ...overrides,
});

test("감시 중이면 상태·고점·하락률·시각을 보이고 서버의 매입가와 가격으로 수익률을 계산한다", async ({
  page,
  isMobile,
}, info) => {
  // 한국시간 2026-11-02 11:15. 오늘이 아닌 시각에는 날짜를 붙인다.
  await page.clock.install({ time: new Date("2026-11-02T02:15:00Z") });
  const LG = holding("051910", "LG화학", { purchase_price: 300_000, current_price: 320_000 });
  const KAKAO = holding("035720", "카카오", { purchase_price: 0 });
  await mockScreen(
    page,
    { domestic_paper: domestic("domestic_paper", [SAMSUNG, NAVER, HYNIX, LG, KAKAO]) },
    (environment) => ({
      body: status(environment, {
        watching: true,
        not_watching_reason: null,
        stocks: [
          // 추가 매수로 계좌 응답(70,000원)보다 새로운 매입가를 서버가 쓰고 있다.
          stock("005930", {
            status: "tracking",
            purchase_price: "60000",
            last_price: "61200",
            last_price_at: "2026-11-02T02:11:00+00:00",
            activated_at: "2026-11-02T01:03:00+00:00",
            peak: "61800",
            peak_at: "2026-11-02T02:10:12+00:00",
          }),
          stock("035420", { purchase_price: "200000", last_price: "194800" }),
          stock("000660", {
            status: "triggered",
            purchase_price: "100000",
            last_price: "107000",
            activated_at: "2026-11-01T01:03:00+00:00",
            peak: "110000",
            peak_at: "2026-11-01T05:20:00+00:00",
            triggered_at: "2026-11-02T01:30:00+00:00",
            triggered_price: "106700",
          }),
          // 시세 체결을 아직 받지 못해 계좌 현재가(+6.67%)로 계산한다.
          stock("051910", { purchase_price: "300000" }),
          stock("035720", { status: "ineligible", ineligible_reason: "no_purchase_price" }),
        ],
      }),
    }),
  );
  await openDashboard(page);
  await view(page, "Trailing Stop").click();
  await expect(page.getByText("지금 세션 09:00–15:30 · 활성화 +5% · 고점 대비 −3%")).toBeVisible();

  // 61,200 / 60,000 = +2.00%, 61,200 / 61,800 = −0.97%
  const samsung = row(page, "삼성전자");
  await expect(visible(samsung, "고점 추적 중")).toBeVisible();
  await expect(visible(samsung, "+2.00%")).toBeVisible();
  if (!isMobile) {
    // 넓은 화면 정보 줄은 핵심 하나만 둔다. 고점과 시각은 줄을 눌러 펼친 상세에 있다.
    expect(await toggle(samsung).innerText()).toContain("005930 · 고점 대비 −1.0%");
    expect(await toggle(samsung).innerText()).not.toContain("61,800원");
  }
  await expectFact(samsung, "도달 여부", "도달 · 10:03");
  await expectFact(samsung, "고점 대비", "−1.0%");
  await expectFact(samsung, "고점", "61,800원");
  await expectFact(samsung, "고점 시각", "11:10");
  await expectFact(samsung, "활성화 시각", "10:03");
  await expectFact(samsung, "매도 조건 충족", "–");
  await expectFact(samsung, "지금 세션 기준", "09:00–15:30 · 활성화 +5% · 고점 대비 −3%");

  const naver = row(page, "NAVER");
  await expect(visible(naver, "활성화 대기")).toBeVisible();
  await expect(visible(naver, "−2.60%")).toBeVisible();
  await expectFact(naver, "도달 여부", "미도달 (기준 5%)");
  await expectFact(naver, "고점 대비", "–");

  // 107,000 / 100,000 = +7.00%, 107,000 / 110,000 = −2.73%
  const hynix = row(page, "SK하이닉스");
  await expect(visible(hynix, "매도 조건 충족")).toBeVisible();
  await expect(visible(hynix, "+7.00%")).toBeVisible();
  await expectFact(hynix, "매도 조건 충족", "10:30 · 106,700원");

  const lg = row(page, "LG화학");
  await expect(visible(lg, "활성화 대기")).toBeVisible();
  await expectFact(lg, "도달 여부", "도달 (판정 전)");

  const kakao = row(page, "카카오");
  await expectStatus(kakao, isMobile, "대상 아님 · 매입가 없음");
  await expectFact(kakao, "도달 여부", "–");

  await expectNoHorizontalScroll(page);
  await page.screenshot({ path: `screenshots/${info.project.name}-trailing-stop-watching.png`, fullPage: true });
});

test("장애로 멈추면 활성화된 종목은 고점 추적 중 · <멈춘 이유>이고 계좌 값으로 수익률을 계산한다", async ({
  page,
  isMobile,
}) => {
  await page.clock.install({ time: new Date("2026-11-02T02:15:00Z") });
  let reason = "connection_replaced";
  await mockScreen(page, { domestic_paper: domestic("domestic_paper", [SAMSUNG, NAVER, HYNIX]) }, (environment) => ({
    body: status(environment, {
      watching: false,
      not_watching_reason: reason,
      // 감시 중이 아니면 기억이 있는 종목만 오고, 판정에 쓰는 매입가와 가격은 비어 있다.
      stocks: [
        stock("005930", {
          status: "tracking",
          activated_at: "2026-11-02T01:03:00+00:00",
          peak: "90000",
          peak_at: "2026-11-02T02:00:00+00:00",
        }),
        stock("000660", {
          status: "triggered",
          activated_at: "2026-11-02T01:03:00+00:00",
          peak: "110000",
          peak_at: "2026-11-02T01:10:00+00:00",
          triggered_at: "2026-11-02T01:30:00+00:00",
          triggered_price: "106700",
        }),
      ],
    }),
  }));
  await openDashboard(page);
  await view(page, "Trailing Stop").click();

  const samsung = row(page, "삼성전자");
  await expectStatus(samsung, isMobile, "고점 추적 중 · 실시간 연결이 밀려남");
  // 84,500 / 70,000 = +20.71%, 84,500 / 90,000 = −6.11%
  await expect(visible(samsung, "+20.71%")).toBeVisible();
  await expectFact(samsung, "고점 대비", "−6.1%");
  await expectStatus(row(page, "SK하이닉스"), isMobile, "매도 조건 충족 · 실시간 연결이 밀려남");
  await expectStatus(row(page, "NAVER"), isMobile, "감시 안 함 · 실시간 연결이 밀려남");

  reason = "holdings_pending";
  await page.clock.runFor(3000);
  await expectStatus(row(page, "삼성전자"), isMobile, "고점 추적 중 · 보유종목 확인 중");
  await expectStatus(row(page, "NAVER"), isMobile, "감시 안 함 · 보유종목 확인 중");
});

test("세션 밖에서도 기억한 종목은 세션 밖, 갱신 멈춤이고 고점 기록을 읽지 못하면 그 이유로 보인다", async ({
  page,
  isMobile,
}) => {
  // 한국시간 2026-11-02 18:00. 활성화와 트리거는 오늘·어제 세션에서 일어났다.
  await page.clock.install({ time: new Date("2026-11-02T09:00:00Z") });
  let reason = "no_session";
  await mockScreen(page, { domestic_paper: domestic("domestic_paper", [SAMSUNG, NAVER, HYNIX]) }, (environment) => ({
    body: status(environment, {
      watching: false,
      not_watching_reason: reason,
      session: null,
      stocks: [
        stock("005930", {
          status: "tracking",
          activated_at: "2026-11-01T01:03:00+00:00",
          peak: "90000",
          peak_at: "2026-11-02T02:00:00+00:00",
        }),
        stock("000660", {
          status: "triggered",
          activated_at: "2026-11-02T01:03:00+00:00",
          peak: "110000",
          peak_at: "2026-11-02T01:10:00+00:00",
          triggered_at: "2026-11-02T01:30:00+00:00",
          triggered_price: "106700",
        }),
      ],
    }),
  }));
  await openDashboard(page);
  await view(page, "Trailing Stop").click();

  const samsung = row(page, "삼성전자");
  await expectStatus(samsung, isMobile, "고점 추적 중 · 세션 밖, 갱신 멈춤");
  // 84,500 / 90,000 = −6.11%. 세션 밖에서는 계좌 현재가로 계산한다.
  await expectFact(samsung, "고점 대비", "−6.1%");
  const hynix = row(page, "SK하이닉스");
  await expectStatus(hynix, isMobile, "매도 조건 충족 · 세션 밖, 갱신 멈춤");
  await expectFact(hynix, "매도 조건 충족", "10:30 · 106,700원");
  await expectStatus(row(page, "NAVER"), isMobile, "감시 안 함 · 켜진 세션 없음");

  // 고점 기록을 읽지 못해 멈춘 것은 세션 밖과 구분해 멈춘 이유를 붙인다.
  reason = "state_unreadable";
  await page.clock.runFor(3000);
  await expectStatus(row(page, "삼성전자"), isMobile, "고점 추적 중 · 고점 기록을 읽지 못함");
  await expectStatus(row(page, "SK하이닉스"), isMobile, "매도 조건 충족 · 고점 기록을 읽지 못함");
  await expect(page.getByText("세션 밖, 갱신 멈춤", { exact: false }).filter({ visible: true })).toHaveCount(0);
});

test("미국 감시 종목은 외화로 고점을 보이고 뉴욕시간으로 시각을 보인다", async ({ page, isMobile }) => {
  // 뉴욕 2026-11-02 10:00(서머타임 끝).
  await page.clock.install({ time: new Date("2026-11-02T15:00:00Z") });
  await mockScreen(
    page,
    { domestic_paper: domestic("domestic_paper", []), us_paper: us("us_paper", [usHolding("AAPL", "애플")]) },
    (environment) => ({
      body: status(environment, {
        watching: true,
        not_watching_reason: null,
        session: { ...ON, start: "09:30", end: "16:00" },
        stocks: [
          stock("AAPL", {
            status: "tracking",
            purchase_price: "282.1603",
            last_price: "300.5",
            activated_at: "2026-11-02T14:35:00+00:00",
            peak: "305.25",
            peak_at: "2026-11-02T14:50:00+00:00",
          }),
        ],
      }),
    }),
  );
  await openDashboard(page);
  await envButton(page, "미국 모의").click();
  await view(page, "Trailing Stop").click();

  // 300.5 / 282.1603 = +6.50%, 300.5 / 305.25 = −1.56%
  const apple = row(page, "애플");
  await expect(visible(apple, "고점 추적 중")).toBeVisible();
  await expect(visible(apple, "+6.50%")).toBeVisible();
  await expectFact(apple, "고점", "$305.25");
});
