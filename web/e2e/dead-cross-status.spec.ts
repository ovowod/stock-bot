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
const NAVER = holding("035420", "NAVER");
const HYNIX = holding("000660", "SK하이닉스");
const KAKAO = holding("035720", "카카오");

const domestic = (environment: string, holdings: unknown[]) => ({
  environment,
  market: "domestic",
  account_no: "8135****11",
  fetched_at: "2026-11-02T01:00:00+00:00",
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

const us = (environment: string, holdings: unknown[]) => ({
  environment,
  market: "us",
  account_no: "6111****41",
  fetched_at: "2026-11-02T15:00:00+00:00",
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

const APPLE = {
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
  credit: false,
};

const ON = { start: "09:00", end: "15:30", enabled: true, bar_period: "5m", short_period: 5, long_period: 20 };
const BASIS = { bar_period: "5m", short_period: 5, long_period: 20 };

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
    recent_sells: [],
    ...extra,
  };
};

const stock = (code: string, overrides: Record<string, unknown> = {}) => ({
  code,
  status: "waiting",
  reason: null,
  ineligible_reason: null,
  short_ma: null,
  long_ma: null,
  basis: null,
  judged_bar: null,
  crossed_at: null,
  crossed_bar: null,
  auto_sell: null,
  ...overrides,
});

const crossed = (code: string, auto_sell: Record<string, unknown> | null) =>
  stock(code, {
    status: "crossed",
    short_ma: "100.00",
    long_ma: "101.00",
    basis: BASIS,
    judged_bar: "2026-11-02T00:55:00+00:00",
    crossed_at: "2026-11-02T01:00:05+00:00",
    crossed_bar: "2026-11-02T00:55:00+00:00",
    auto_sell,
  });

const attempt = (state: string, overrides: Record<string, unknown> = {}) => ({
  state,
  reason: null,
  quantity: null,
  order_no: null,
  at: "2026-11-02T01:00:06+00:00",
  ...overrides,
});

type Reply = { status?: number; body: unknown };

/** 계좌와 Dead Cross·Trailing Stop 상태를 가짜로 응답한다. 상태 요청 경로를 돌려준다. */
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
  await page.route("**/api/environments/*/dead-cross/status", (route: Route) => {
    requests.push("dead-cross");
    const environment = new URL(route.request().url()).pathname.split("/")[3];
    const { status: code = 200, body } = reply(environment);
    return route.fulfill({ status: code, json: body });
  });
  await page.route("**/api/environments/*/trailing-stop/status", (route: Route) => {
    requests.push("trailing-stop");
    const environment = new URL(route.request().url()).pathname.split("/")[3];
    return route.fulfill({
      json: {
        environment,
        market: "domestic",
        timezone: "Asia/Seoul",
        watching: false,
        not_watching_reason: "no_session",
        session: null,
        excluded: [],
        stocks: [],
      },
    });
  });
  await mockNoOpenOrders(page);
  return requests;
}

const view = (page: Page, name: string) => page.getByRole("radiogroup", { name: "보유종목 보기" }).getByRole("radio", { name });
const row = (page: Page, name: string) =>
  page.getByRole("listitem").filter({ hasText: name }).filter({ visible: true }).first();
const visible = (scope: Page | Locator, text: string) => scope.getByText(text, { exact: true }).filter({ visible: true });
const toggle = (item: Locator) => item.locator("button[aria-expanded]").filter({ visible: true });

/** 줄을 눌러 펼친 상세에서 값을 확인한다. 넓은 화면도 상세는 펼쳐야 보인다. */
async function expectFact(item: Locator, term: string, value: string) {
  if ((await toggle(item).getAttribute("aria-expanded")) === "false") await toggle(item).click();
  await expect(item.locator(`dd[data-term="${term}"]`).filter({ visible: true })).toHaveText(value);
}

/** 넓은 화면은 배지에 상태와 이유를 함께, 좁은 화면 배지는 상태 이름만 보인다. 상세의 "상태"에는 다 있다. */
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
}

// 한국 10:00. 상태의 시각이 오늘이면 날짜 없이 보인다.
const NOW = new Date("2026-11-02T01:00:00Z");

test("세 보기를 바꾸고 새로고침해도 Dead Cross 보기로 다시 열린다", async ({ page, isMobile }) => {
  const requests = await mockScreen(page, { domestic_paper: domestic("domestic_paper", [SAMSUNG]) });
  await openDashboard(page);

  await expect(page.getByRole("radiogroup", { name: "보유종목 보기" }).getByRole("radio")).toHaveText([
    "기본",
    "Trailing Stop",
    "Dead Cross",
  ]);
  expect(requests).toEqual([]);

  await view(page, "Dead Cross").click();
  await expectStatus(row(page, "삼성전자"), isMobile, "계산 대기 · 켜진 세션 없음");
  await expect(
    page.getByText("이동평균은 완료된 봉의 종가로 계산하며 진행 중인 봉은 넣지 않습니다. 키움 차트처럼 정규장 밖 봉도 포함합니다."),
  ).toBeVisible();
  await expect(page.getByText("하향 돌파가 일어나면 매도 가능 수량 전량을 시장가로 팝니다(모의투자만).")).toBeVisible();
  await expect(page.getByText("지금 세션 09:00–15:30 · 5분봉 · 단기 5봉 · 장기 20봉")).toBeVisible();
  expect(requests).toEqual(["dead-cross"]);

  await page.reload();
  await expect(view(page, "Dead Cross")).toHaveAttribute("aria-checked", "true");
  await expectStatus(row(page, "삼성전자"), isMobile, "계산 대기 · 켜진 세션 없음");

  // Trailing Stop 보기는 그대로 동작한다.
  await view(page, "Trailing Stop").click();
  await expectStatus(row(page, "삼성전자"), isMobile, "감시 안 함 · 켜진 세션 없음");
  await view(page, "기본").click();
  await expect(visible(row(page, "삼성전자"), "845,000원")).toBeVisible();
});

test("감시 중이면 이동평균과 세 상태, 계산 대기 이유를 보인다", async ({ page, isMobile }, info) => {
  await page.clock.install({ time: NOW });
  const watching = status("domestic_paper", {
    watching: true,
    not_watching_reason: null,
    excluded: ["000660"],
    stocks: [
      stock("005930", {
        status: "crossed",
        short_ma: "61230.40",
        long_ma: "61012.75",
        basis: BASIS,
        judged_bar: "2026-11-02T00:55:00+00:00",
        crossed_at: "2026-11-02T01:00:05+00:00",
        crossed_bar: "2026-11-02T00:55:00+00:00",
      }),
      stock("035420", {
        status: "tracking",
        short_ma: "195000.00",
        long_ma: "194000.50",
        basis: BASIS,
        judged_bar: "2026-11-02T00:55:00+00:00",
      }),
      stock("000660", { reason: "excluded" }),
      stock("035720", { reason: "not_enough_bars" }),
    ],
  });
  await mockScreen(
    page,
    {
      domestic_paper: domestic("domestic_paper", [
        SAMSUNG,
        NAVER,
        HYNIX,
        KAKAO,
        holding("005930", "삼성전자 신용", { credit: true }),
      ]),
    },
    () => ({ body: watching }),
  );
  await openDashboard(page);
  await view(page, "Dead Cross").click();

  const samsung = row(page, "삼성전자");
  await expectStatus(samsung, isMobile, "데드크로스 발생");
  // 넓은 화면의 정보 줄에는 이동평균을 둔다.
  if (!isMobile) await expect(samsung.getByText("단기 61,230.40 · 장기 61,012.75", { exact: false }).filter({ visible: true }).first()).toBeVisible();
  await expectFact(samsung, "단기 이동평균", "61,230.40");
  await expectFact(samsung, "장기 이동평균", "61,012.75");
  await expectFact(samsung, "계산 기준", "5분봉 · 단기 5봉 · 장기 20봉");
  await expectFact(samsung, "지금 세션 기준", "5분봉 · 단기 5봉 · 장기 20봉");
  await expectFact(samsung, "마지막 판정 봉", "09:55");
  await expectFact(samsung, "데드크로스 발생", "10:00 · 09:55 봉");
  await expectFact(samsung, "활성 세션", "예");
  await expectFact(samsung, "제외종목", "아니요");

  await expectStatus(row(page, "NAVER"), isMobile, "추적 중");
  await expectStatus(row(page, "SK하이닉스"), isMobile, "계산 대기 · 제외종목");
  await expectFact(row(page, "SK하이닉스"), "제외종목", "예");
  await expectStatus(row(page, "카카오"), isMobile, "계산 대기 · 봉 부족");
  await expectFact(row(page, "카카오"), "단기 이동평균", "–");
  await expectStatus(row(page, "삼성전자 신용"), isMobile, "계산 대기 · 대상 아님 · 신용");

  await expectNoHorizontalScroll(page);
  await page.screenshot({ path: `screenshots/${info.project.name}-dead-cross-view.png`, fullPage: true });
});

test("데드크로스 발생 종목에는 자동 매도가 어디까지 왔는지 보인다", async ({ page, isMobile }) => {
  await page.clock.install({ time: NOW });
  let reason: string | null = null;
  await mockScreen(
    page,
    { domestic_paper: domestic("domestic_paper", [SAMSUNG, NAVER, HYNIX, KAKAO]) },
    (environment) => ({
      body: status(environment, {
        watching: reason === null,
        not_watching_reason: reason,
        stocks: [
          crossed("005930", attempt("sending")),
          crossed("035420", attempt("accepted", { quantity: 10, order_no: "0000138" })),
          crossed("000660", attempt("failed", { reason: "rejected" })),
          // 서버는 감시가 멈추면 기억이 있는 종목의 reason에 그 이유를 담는다.
          { ...crossed("035720", null), reason },
        ],
      }),
    }),
  );
  await openDashboard(page);
  await view(page, "Dead Cross").click();

  const kakao = row(page, "카카오");
  await expectStatus(row(page, "삼성전자"), isMobile, "데드크로스 발생");
  await expectFact(row(page, "삼성전자"), "자동 매도", "보내는 중");
  await expectFact(row(page, "NAVER"), "자동 매도", "접수 · 0000138 · 10주 · 10:00");
  await expectFact(row(page, "SK하이닉스"), "자동 매도", "실패 · 다음 세션에 다시 시도 · 거부");
  await expectFact(kakao, "자동 매도", "다음 조회에서 매도");

  // 세션 밖이면 다음 세션에 판다.
  reason = "no_session";
  await page.clock.runFor(3000);
  await expectFact(kakao, "자동 매도", "세션 밖 · 다음 세션에 매도");

  // 장애로 멈추면 자동 매도 칸 없이 멈춘 이유만 보인다.
  reason = "connection_replaced";
  await page.clock.runFor(3000);
  await expectStatus(kakao, isMobile, "데드크로스 발생 · 실시간 연결이 밀려남");
  await expect(kakao.locator('dd[data-term="자동 매도"]')).toHaveCount(0);
  await expectNoHorizontalScroll(page);
});

test("갱신이 멈춘 발생 종목은 이유를 붙이고, 이전 기준 값은 따로 표시한다", async ({ page, isMobile }) => {
  await page.clock.install({ time: NOW });
  await mockScreen(page, { domestic_paper: domestic("domestic_paper", [SAMSUNG, NAVER]) }, (environment) => ({
    body: status(environment, {
      watching: true,
      not_watching_reason: null,
      session: { ...ON, bar_period: "30m" },
      stocks: [
        stock("005930", {
          status: "crossed",
          reason: "chart_failed",
          short_ma: "100.00",
          long_ma: "101.00",
          basis: BASIS,
          judged_bar: "2026-10-30T06:20:00+00:00",
          crossed_at: "2026-10-30T06:25:05+00:00",
          crossed_bar: "2026-10-30T06:20:00+00:00",
        }),
        stock("035420", { status: "ineligible", ineligible_reason: "duplicate" }),
      ],
    }),
  }));
  await openDashboard(page);
  await view(page, "Dead Cross").click();

  const samsung = row(page, "삼성전자");
  await expectStatus(samsung, isMobile, "데드크로스 발생 · 차트 조회 실패");
  await expectFact(samsung, "계산 기준", "이전 기준 값 · 5분봉 · 단기 5봉 · 장기 20봉");
  await expectFact(samsung, "지금 세션 기준", "30분봉 · 단기 5봉 · 장기 20봉");
  // 오늘이 아닌 시각에는 날짜를 붙인다.
  await expectFact(samsung, "데드크로스 발생", "10/30 15:25 · 10/30 15:20 봉");
  await expectStatus(row(page, "NAVER"), isMobile, "계산 대기 · 대상 아님 · 같은 종목 여러 줄");
});

test("세션 밖의 발생 종목은 세션 밖, 갱신 멈춤이고 실전은 계산 대기 · 실전투자다", async ({ page, isMobile }) => {
  await mockScreen(
    page,
    {
      domestic_paper: domestic("domestic_paper", [SAMSUNG]),
      domestic_real: domestic("domestic_real", [SAMSUNG]),
    },
    (environment) => ({
      body: environment.endsWith("real")
        ? status(environment, { excluded: ["005930"] })
        : status(environment, {
            session: null,
            stocks: [stock("005930", { status: "crossed", reason: "no_session", judged_bar: "2026-11-02T06:25:00+00:00" })],
          }),
    }),
  );
  await openDashboard(page);
  await view(page, "Dead Cross").click();

  const samsung = row(page, "삼성전자");
  await expectStatus(samsung, isMobile, "데드크로스 발생 · 세션 밖, 갱신 멈춤");
  await expectFact(samsung, "활성 세션", "아니요");
  await expectFact(samsung, "지금 세션 기준", "–");

  await page.getByRole("radio", { name: "국내 실전" }).click();
  await expectStatus(row(page, "삼성전자"), isMobile, "계산 대기 · 실전투자");
});

test("열려 있고 탭이 보이는 동안 3초마다 받고, 실패하면 마지막 상태와 안내를 보인다", async ({ page, isMobile }) => {
  await page.clock.install({ time: NOW });
  let failing = false;
  const requests = await mockScreen(page, { domestic_paper: domestic("domestic_paper", [SAMSUNG]) }, (environment) =>
    failing
      ? { status: 500, body: { error: { kind: "internal_error", message: "서버 내부 오류가 발생했습니다." } } }
      : { body: status(environment) },
  );
  await openDashboard(page);
  await page.clock.pauseAt(new Date("2026-11-02T01:01:00Z"));
  await view(page, "Dead Cross").click();
  await expectStatus(row(page, "삼성전자"), isMobile, "계산 대기 · 켜진 세션 없음");
  const count = () => requests.filter((r) => r === "dead-cross").length;
  await expect.poll(count).toBe(1);

  const advance = async (ms: number) => {
    await page.clock.runFor(ms);
    await new Promise((resolve) => setTimeout(resolve, 200));
  };
  await advance(3000);
  await expect.poll(count).toBe(2);

  const setVisibility = (state: "hidden" | "visible") =>
    page.evaluate((value) => {
      Object.defineProperty(document, "visibilityState", { value, configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
    }, state);
  await setVisibility("hidden");
  await advance(10_000);
  expect(count()).toBe(2);
  await setVisibility("visible");
  await expect.poll(count).toBe(3);

  failing = true;
  await advance(3000);
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("Dead Cross 상태를 불러오지 못했습니다. 마지막으로 받은 상태를 보여줍니다.");
  await expectStatus(row(page, "삼성전자"), isMobile, "계산 대기 · 켜진 세션 없음");
  failing = false;
  await advance(3000);
  await expect(alert).toHaveCount(0);
});

test("미국 계좌는 이동평균을 외화 소수 넷째 자리로 보인다", async ({ page, isMobile }) => {
  await mockScreen(page, { us_paper: us("us_paper", [APPLE]) }, (environment) => ({
    body: status(environment, {
      watching: true,
      not_watching_reason: null,
      session: { ...ON, start: "09:30", end: "16:00", bar_period: "1m" },
      stocks: [
        stock("AAPL", {
          status: "tracking",
          short_ma: "195.2500",
          long_ma: "196.8333",
          basis: { bar_period: "1m", short_period: 5, long_period: 20 },
          judged_bar: "2026-11-02T14:59:00+00:00",
        }),
      ],
    }),
  }));
  await openDashboard(page);
  await page.getByRole("radio", { name: "미국 모의" }).click();
  await view(page, "Dead Cross").click();

  const apple = row(page, "애플");
  await expectStatus(apple, isMobile, "추적 중");
  await expectFact(apple, "단기 이동평균", "$195.2500");
  await expectFact(apple, "장기 이동평균", "$196.8333");
  await expectFact(apple, "계산 기준", "1분봉 · 단기 5봉 · 장기 20봉");
});

test("좁은 화면에서도 긴 상태가 잘리지 않고 가로로 넘치지 않는다", async ({ page }, info) => {
  // 360px 폭은 좁은 화면 배치다. 배지에는 상태 이름만 보인다.
  await page.setViewportSize({ width: 360, height: 800 });
  await mockScreen(
    page,
    { domestic_paper: domestic("domestic_paper", [holding("069500", "KODEX 200 미국채혼합 아주 긴 이름의 ETF")]) },
    (environment) => ({ body: status(environment, { not_watching_reason: "state_unreadable" }) }),
  );
  await openDashboard(page);
  await view(page, "Dead Cross").click();

  await expectStatus(row(page, "KODEX"), true, "계산 대기 · 판정 기록을 읽지 못함");
  await expectNoHorizontalScroll(page);
  await page.screenshot({ path: `screenshots/${info.project.name}-dead-cross-narrow.png`, fullPage: true });
});
