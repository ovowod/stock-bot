import { expect, test, type Page, type Route } from "@playwright/test";
import { mockNoOpenOrders, openDashboard } from "./support";

// 브라우저의 /api 요청을 가로채 가짜 응답을 준다. 키움 서버나 실전 서버는 호출되지 않는다.

type Settings = {
  environment: string;
  market: "domestic" | "us";
  timezone: string;
  regular_hours: { start: string; end: string };
  can_enable: boolean;
  updated_at: string | null;
  excluded: { code: string; name: string; exchange: string | null }[];
  sessions: unknown[];
};

const settings = (environment: string, extra: Partial<Settings> = {}): Settings => {
  const us = environment.startsWith("us");
  return {
    environment,
    market: us ? "us" : "domestic",
    timezone: us ? "America/New_York" : "Asia/Seoul",
    regular_hours: us ? { start: "09:30", end: "16:00" } : { start: "09:00", end: "15:30" },
    can_enable: !environment.endsWith("real"),
    updated_at: null,
    excluded: [],
    sessions: [],
    ...extra,
  };
};

const SAMSUNG = { code: "005930", name: "삼성전자", exchange: null };

const searchItem = (code: string, name: string, exchange: string | null = null) => ({
  code,
  name,
  english_name: null,
  exchange,
  category: exchange ? null : "코스피",
  industry: null,
  status: null,
  is_etf: null,
});

type Reply = { status?: number; body: unknown; delayMs?: number };

/** Trailing Stop·SL / TP 조회·저장과 종목 검색을 가짜로 응답한다. Trailing Stop 저장 본문을 모아 돌려준다. */
async function mockTrailingStop(
  page: Page,
  {
    load = (environment: string): Reply => ({ body: settings(environment) }),
    save = (environment: string, body: Partial<Settings>): Reply => ({
      body: { ...settings(environment), ...body, updated_at: "2026-11-01T01:00:00+00:00" },
    }),
    search = [searchItem("005930", "삼성전자"), searchItem("000660", "SK하이닉스")],
  }: {
    load?: (environment: string) => Reply;
    save?: (environment: string, body: Partial<Settings>) => Reply;
    search?: unknown[];
  } = {},
) {
  const saved: { environment: string; body: Partial<Settings> }[] = [];
  const loads: string[] = [];
  await page.route("**/api/environments/*/trailing-stop", async (route: Route) => {
    const environment = new URL(route.request().url()).pathname.split("/")[3];
    let reply: Reply;
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON();
      saved.push({ environment, body });
      reply = save(environment, body);
    } else {
      loads.push(environment);
      reply = load(environment);
    }
    if (reply.delayMs) await new Promise((resolve) => setTimeout(resolve, reply.delayMs));
    await route.fulfill({ status: reply.status ?? 200, json: reply.body }).catch(() => {});
  });
  await page.route("**/api/environments/*/sl-tp", (route) => {
    const environment = new URL(route.request().url()).pathname.split("/")[3];
    return route.fulfill({ json: settings(environment) });
  });
  await page.route("**/api/environments/*/stocks?*", (route) => {
    const url = new URL(route.request().url());
    return route.fulfill({
      json: {
        environment: url.pathname.split("/")[3],
        market: "domestic",
        query: url.searchParams.get("q"),
        total: search.length,
        truncated: false,
        list_fetched_at: "2026-11-01T00:00:00+00:00",
        items: search,
      },
    });
  });
  // 처음 화면(계좌 확인)의 요청은 이 테스트와 관계없으므로 오류로 둔다.
  await page.route("**/api/environments/*/account", (route) =>
    route.fulfill({ status: 503, json: { error: { kind: "config_error", message: "test" } } }),
  );
  await mockNoOpenOrders(page);
  return { saved, loads };
}

/** 모바일에서는 메뉴 서랍을 연 뒤 메뉴를 고른다. */
async function selectAutoTradingMenu(page: Page, name: string) {
  const menu = page.getByRole("button", { name: "메뉴 열기" });
  if (await menu.isVisible()) await menu.click();
  const group = page.getByRole("group", { name: "자동매매" }).filter({ visible: true });
  await group.getByRole("button", { name }).click();
}

async function openTrailingStop(page: Page) {
  await openDashboard(page);
  await selectAutoTradingMenu(page, "Trailing Stop");
  await expect(page.getByRole("heading", { name: "Trailing Stop", exact: true })).toBeVisible();
}

const excludedInput = (page: Page) => page.getByRole("searchbox", { name: "제외종목 검색어" });
const excludedList = (page: Page) => page.getByRole("list", { name: "제외종목 목록" });
const searchResult = (page: Page, name: RegExp) =>
  page.getByRole("region", { name: "제외종목 검색 결과" }).getByRole("button", { name });
const saveButton = (page: Page) => page.getByRole("button", { name: "저장", exact: true });
const sessionList = (page: Page) => page.getByRole("list", { name: "Trailing Stop 세션 목록" });
const addSession = (page: Page) => page.getByRole("button", { name: "세션 추가" });
const startInput = (page: Page, n: number) => page.getByLabel(`${n}번째 세션 시작 시각`);
const endInput = (page: Page, n: number) => page.getByLabel(`${n}번째 세션 종료 시각`);
const switchOf = (page: Page, n: number) => page.getByRole("switch", { name: `${n}번째 세션 켜기` });
const activation = (page: Page, n: number) => page.getByLabel(`${n}번째 세션 활성화 수익률(%)`);
const drop = (page: Page, n: number) => page.getByLabel(`${n}번째 세션 고점 대비 하락률(%)`);
const off = (start: string, end: string) => ({
  start,
  end,
  enabled: false,
  activation_profit: null,
  drop_from_peak: null,
});

async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

test("자동매매 → SL / TP 아래의 Trailing Stop에서 안내 문구와 빈 설정을 보여준다", async ({ page }, info) => {
  const { loads } = await mockTrailingStop(page);
  await openDashboard(page);
  const menu = page.getByRole("button", { name: "메뉴 열기" });
  if (await menu.isVisible()) await menu.click();
  const items = page.getByRole("group", { name: "자동매매" }).filter({ visible: true }).getByRole("button");
  await expect(items).toHaveText(["SL / TP", "Trailing Stop"]);
  await items.nth(1).click();

  await expect(page.getByRole("heading", { name: "Trailing Stop", exact: true })).toBeVisible();
  expect(loads).toEqual(["domestic_paper"]);
  await expect(
    page.getByText(
      "평균 매입가 대비 수익률이 활성화 수익률 이상이 되면 그때부터 고점을 따라가고, 시세 체결가가 고점 대비 하락률만큼 떨어지면 그 종목의 매도 가능 수량 전량을 시장가로 매도합니다. 제외종목은 매도하지 않습니다.",
    ),
  ).toBeVisible();
  await expect(
    page.getByText("지금은 상태만 계산하고 팔지 않습니다. 계좌 확인 화면의 Trailing Stop 보기에서 확인하세요."),
  ).toBeVisible();
  await expect(page.getByText("제외종목이 없습니다")).toBeVisible();
  await expect(page.getByText("모든 Trailing Stop 세션에 똑같이 적용됩니다")).toBeVisible();
  await expect(page.getByText("Trailing Stop 세션이 없습니다")).toBeVisible();
  await expect(page.getByText("정규장 09:00–15:30 · 한국시간(Asia/Seoul)")).toBeVisible();
  await expect(saveButton(page)).toBeDisabled();
  await expectNoHorizontalScroll(page);
  await page.screenshot({ path: `screenshots/${info.project.name}-trailing-stop.png`, fullPage: true });
});

test("제외종목을 검색해 추가·삭제하고 저장한다", async ({ page }) => {
  const { saved } = await mockTrailingStop(page);
  await openTrailingStop(page);

  await excludedInput(page).fill("삼성");
  await searchResult(page, /삼성전자/).click();
  await expect(excludedInput(page)).toHaveValue("");
  await expect(excludedList(page)).toContainText("005930");
  await excludedInput(page).fill("삼성");
  await expect(searchResult(page, /삼성전자/)).toContainText("추가됨");
  await expect(searchResult(page, /삼성전자/)).toBeDisabled();
  await searchResult(page, /SK하이닉스/).click();
  await excludedList(page).getByRole("button", { name: "SK하이닉스 삭제" }).click();
  await expect(excludedList(page)).not.toContainText("SK하이닉스");

  await saveButton(page).click();
  await expect(page.getByText("Trailing Stop 설정을 저장했습니다")).toBeVisible();
  expect(saved).toEqual([{ environment: "domestic_paper", body: { excluded: [SAMSUNG], sessions: [] } }]);
  await expect(page.getByText("저장하지 않은 변경")).toBeHidden();
});

test("미국 환경은 제외종목에 거래소를, 정규장은 뉴욕시간으로 보여준다", async ({ page }) => {
  const { saved } = await mockTrailingStop(page, { search: [searchItem("AAPL", "애플", "NASDAQ")] });
  await openTrailingStop(page);
  await page.getByRole("radio", { name: "미국 모의" }).click();

  await expect(page.getByText("정규장 09:30–16:00 · 뉴욕시간(America/New_York)")).toBeVisible();
  await excludedInput(page).fill("apple");
  await searchResult(page, /애플/).click();
  await expect(excludedList(page)).toContainText("AAPL · NASDAQ");
  await addSession(page).click();
  await expect(startInput(page, 1)).toHaveValue("09:30");
  await expect(endInput(page, 1)).toHaveValue("10:30");
  await saveButton(page).click();
  await expect(page.getByText("Trailing Stop 설정을 저장했습니다")).toBeVisible();
  expect(saved[0]).toEqual({
    environment: "us_paper",
    body: { excluded: [{ code: "AAPL", name: "애플", exchange: "NASDAQ" }], sessions: [off("09:30", "10:30")] },
  });
  await expectNoHorizontalScroll(page);
});

test("Trailing Stop 세션을 정규장 안에서 이어 붙여 추가·삭제한다", async ({ page }) => {
  const { saved } = await mockTrailingStop(page);
  await openTrailingStop(page);

  await addSession(page).click();
  await expect(startInput(page, 1)).toHaveValue("09:00");
  await expect(endInput(page, 1)).toHaveValue("10:00");
  await expect(switchOf(page, 1)).toHaveAttribute("aria-checked", "false");
  await expect(activation(page, 1)).toHaveValue("");
  await expect(drop(page, 1)).toHaveValue("");
  await addSession(page).click();
  await expect(startInput(page, 2)).toHaveValue("10:00");
  await endInput(page, 2).fill("15:30");
  await expect(addSession(page)).toBeDisabled();
  await expect(page.getByText("정규장 끝까지 Trailing Stop 세션이 있습니다")).toBeVisible();

  await sessionList(page).getByRole("button", { name: "1번째 세션 삭제" }).click();
  await expect(sessionList(page).getByRole("listitem")).toHaveCount(1);
  await saveButton(page).click();
  await expect(page.getByText("Trailing Stop 설정을 저장했습니다")).toBeVisible();
  expect(saved[0].body.sessions).toEqual([off("10:00", "15:30")]);
});

test("세션 시각이 잘못되면 그 세션에 안내하고 저장을 막는다", async ({ page }) => {
  await mockTrailingStop(page, {
    load: (environment) => ({ body: settings(environment, { sessions: [off("09:00", "10:00"), off("10:00", "11:00")] }) }),
  });
  await openTrailingStop(page);
  const items = sessionList(page).getByRole("listitem");

  await startInput(page, 2).fill("09:30");
  await expect(items.nth(1)).toContainText("시작 시각이 앞 세션의 종료 시각보다 이릅니다");
  await expect(saveButton(page)).toBeDisabled();
  await startInput(page, 2).fill("10:00");
  await endInput(page, 2).fill("15:40");
  await expect(items.nth(1)).toContainText("정규장(09:00–15:30) 안에서만 정할 수 있습니다");
  await endInput(page, 2).fill("15:30");
  await expect(saveButton(page)).toBeEnabled();
});

test("세션을 켜고 활성화 수익률과 고점 대비 하락률을 정해 저장한다", async ({ page }, info) => {
  const { saved } = await mockTrailingStop(page);
  await openTrailingStop(page);

  await addSession(page).click();
  await addSession(page).click();
  await switchOf(page, 1).click();
  await expect(sessionList(page)).toContainText(
    "켜진 세션에는 활성화 수익률과 고점 대비 하락률이 모두 있어야 합니다.",
  );
  await expect(saveButton(page)).toBeDisabled();
  await activation(page, 1).fill("5");
  await expect(saveButton(page)).toBeDisabled();
  await drop(page, 1).fill("2.5");
  const first = sessionList(page).getByRole("listitem").first();
  // 보이는 칸 이름도 용어집의 이름을 쓴다("활성화"는 칸이 아니라 종목이 그 수익률에 닿은 일이다).
  await expect(first.getByText("활성화 수익률", { exact: true })).toBeVisible();
  await expect(first.getByText("하락률", { exact: true })).toBeVisible();
  await expect(first).toContainText("+5%부터 고점을 따라감");
  await expect(first).toContainText("고점 대비 −2.5%에서 매도");
  // 하락률이 활성화 수익률보다 커도 받는다.
  await activation(page, 2).fill("3");
  await drop(page, 2).fill("5");
  await page.screenshot({ path: `screenshots/${info.project.name}-trailing-stop-sessions.png`, fullPage: true });

  await saveButton(page).click();
  await expect(page.getByText("Trailing Stop 설정을 저장했습니다")).toBeVisible();
  expect(saved[0].body.sessions).toEqual([
    { start: "09:00", end: "10:00", enabled: true, activation_profit: "5", drop_from_peak: "2.5" },
    { start: "10:00", end: "11:00", enabled: false, activation_profit: "3", drop_from_peak: "5" },
  ]);
  await expectNoHorizontalScroll(page);
});

test("잘못된 활성화 수익률·하락률은 그 칸에 안내하고 저장을 막는다", async ({ page }) => {
  await mockTrailingStop(page, { load: (environment) => ({ body: settings(environment, { sessions: [off("09:00", "10:00")] }) }) });
  await openTrailingStop(page);
  const activationError = "활성화 수익률은 0.01~1000 사이, 소수점 둘째 자리까지의 숫자여야 합니다.";
  const dropError = "고점 대비 하락률은 0.01~99.99 사이, 소수점 둘째 자리까지의 숫자여야 합니다.";

  await activation(page, 1).fill("0");
  await expect(activation(page, 1)).toHaveAccessibleDescription(activationError);
  await expect(saveButton(page)).toBeDisabled();
  await activation(page, 1).fill("1000");
  await expect(activation(page, 1)).toHaveAccessibleDescription("");
  await drop(page, 1).fill("1.234");
  await expect(drop(page, 1)).toHaveAccessibleDescription(dropError);
  await drop(page, 1).fill("100");
  await expect(drop(page, 1)).toHaveAccessibleDescription(dropError);
  await drop(page, 1).fill("");
  await expect(saveButton(page)).toBeEnabled();
});

test("실전투자에서는 스위치를 켤 수 없지만 시각과 값은 저장한다", async ({ page }) => {
  const { saved } = await mockTrailingStop(page);
  await openTrailingStop(page);
  await page.getByRole("radio", { name: "국내 실전" }).click();

  await expect(page.getByText("실전투자에서는 Trailing Stop 세션을 켤 수 없습니다")).toBeVisible();
  await addSession(page).click();
  await expect(switchOf(page, 1)).toBeDisabled();
  await activation(page, 1).fill("5");
  await saveButton(page).click();
  await expect(page.getByText("Trailing Stop 설정을 저장했습니다")).toBeVisible();
  expect(saved[0]).toEqual({
    environment: "domestic_real",
    body: {
      excluded: [],
      sessions: [{ start: "09:00", end: "10:00", enabled: false, activation_profit: "5", drop_from_peak: null }],
    },
  });
});

test("저장하는 동안에는 편집이 막히고, 실패하면 원인을 알리고 입력을 둔다", async ({ page }) => {
  let fail = false;
  await mockTrailingStop(page, {
    save: (environment, body) =>
      fail
        ? {
            status: 500,
            body: {
              error: {
                kind: "trailing_stop_settings_write_failed",
                message: "Trailing Stop 설정을 저장하지 못했습니다.",
                request_id: "req-1",
              },
            },
          }
        : { body: { ...settings(environment), ...body }, delayMs: 1000 },
  });
  await openTrailingStop(page);

  await addSession(page).click();
  await saveButton(page).click();
  await expect(page.getByRole("button", { name: "저장 중" })).toBeDisabled();
  await expect(excludedInput(page)).toBeDisabled();
  await expect(addSession(page)).toBeDisabled();
  await expect(switchOf(page, 1)).toBeDisabled();
  await expect(activation(page, 1)).toBeDisabled();
  await expect(drop(page, 1)).toBeDisabled();
  await expect(page.getByText("Trailing Stop 설정을 저장했습니다")).toBeVisible();

  fail = true;
  await drop(page, 1).fill("3");
  await saveButton(page).click();
  await expect(page.getByRole("alert").filter({ hasText: "Trailing Stop 설정을 저장하지 못했습니다" }).first()).toBeVisible();
  await expect(drop(page, 1)).toHaveValue("3");
  await expect(page.getByText("저장하지 않은 변경")).toBeVisible();
});

test("설정을 불러오지 못하면 원인과 다시 시도를 보여준다", async ({ page }) => {
  let fail = true;
  const { loads } = await mockTrailingStop(page, {
    load: (environment) =>
      fail
        ? {
            status: 500,
            body: {
              error: {
                kind: "trailing_stop_settings_unreadable",
                message: "Trailing Stop 설정 파일을 읽지 못했습니다.",
                request_id: "req-2",
              },
            },
          }
        : { body: settings(environment, { excluded: [SAMSUNG] }) },
  });
  await openTrailingStop(page);

  await expect(page.getByText("Trailing Stop 설정 파일을 읽지 못했습니다.")).toBeVisible();
  await expect(excludedInput(page)).toBeHidden();
  fail = false;
  await page.getByRole("button", { name: "다시 시도" }).click();
  await expect(excludedList(page)).toContainText("삼성전자");
  expect(loads).toHaveLength(2);
});

const LEAVE_MESSAGE = "저장하지 않은 변경이 사라집니다. 이동할까요?";

/** 다음 확인 창의 문구를 기록하고 accept면 확인, 아니면 취소를 누른다. */
function answerNextConfirm(page: Page, accept: boolean): string[] {
  const messages: string[] = [];
  page.once("dialog", (dialog) => {
    messages.push(dialog.message());
    void (accept ? dialog.accept() : dialog.dismiss());
  });
  return messages;
}

test("저장하지 않은 변경이 있으면 SL / TP로 가거나 환경을 바꾸기 전에 묻는다", async ({ page }) => {
  await mockTrailingStop(page);
  await openTrailingStop(page);
  await addSession(page).click();
  await expect(page.getByText("저장하지 않은 변경")).toBeVisible();

  let asked = answerNextConfirm(page, false);
  await page.getByRole("radio", { name: "미국 모의" }).click();
  await expect.poll(() => asked).toEqual([LEAVE_MESSAGE]);
  await expect(page.getByRole("radio", { name: "국내 모의" })).toHaveAttribute("aria-checked", "true");

  asked = answerNextConfirm(page, false);
  await selectAutoTradingMenu(page, "SL / TP");
  await expect.poll(() => asked).toEqual([LEAVE_MESSAGE]);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("heading", { name: "Trailing Stop", exact: true })).toBeVisible();
  await expect(startInput(page, 1)).toHaveValue("09:00");

  asked = answerNextConfirm(page, true);
  await selectAutoTradingMenu(page, "SL / TP");
  await expect(page.getByRole("heading", { name: "SL / TP" })).toBeVisible();
  expect(asked).toEqual([LEAVE_MESSAGE]);
});

test("가장 긴 값도 입력 칸 안에 다 보인다", async ({ page }) => {
  const longest = { ...off("09:00", "10:00"), activation_profit: "999.99", drop_from_peak: "99.99" };
  await mockTrailingStop(page, { load: (environment) => ({ body: settings(environment, { sessions: [longest] }) }) });
  await openTrailingStop(page);

  for (const input of [activation(page, 1), drop(page, 1)]) {
    const [scroll, client] = await input.evaluate((el: HTMLInputElement) => [el.scrollWidth, el.clientWidth]);
    expect(scroll).toBeLessThanOrEqual(client);
  }
  await expectNoHorizontalScroll(page);
});
