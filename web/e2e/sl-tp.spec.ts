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

/** SL / TP 조회·저장과 종목 검색을 가짜로 응답한다. 저장 요청 본문을 모아 돌려준다. */
async function mockSlTp(
  page: Page,
  {
    load = (environment: string): Reply => ({ body: settings(environment) }),
    save = (environment: string, body: Partial<Settings>): Reply => ({
      body: { ...settings(environment), ...body, updated_at: "2026-10-24T01:00:00+00:00" },
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
  await page.route("**/api/environments/*/sl-tp", async (route: Route) => {
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
  await page.route("**/api/environments/*/stocks?*", (route) => {
    const url = new URL(route.request().url());
    return route.fulfill({
      json: {
        environment: url.pathname.split("/")[3],
        market: "domestic",
        query: url.searchParams.get("q"),
        total: search.length,
        truncated: false,
        list_fetched_at: "2026-10-24T00:00:00+00:00",
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

/** 모바일에서는 메뉴 서랍을 연 뒤 SL / TP를 고른다. */
async function selectSlTpMenu(page: Page) {
  const menu = page.getByRole("button", { name: "메뉴 열기" });
  if (await menu.isVisible()) await menu.click();
  const group = page.getByRole("group", { name: "자동매매" }).filter({ visible: true });
  await group.getByRole("button", { name: "SL / TP" }).click();
}

async function openSlTp(page: Page) {
  await openDashboard(page);
  await selectSlTpMenu(page);
  await expect(page.getByRole("heading", { name: "SL / TP" })).toBeVisible();
}

const excludedInput = (page: Page) => page.getByRole("searchbox", { name: "제외종목 검색어" });
const excludedList = (page: Page) => page.getByRole("list", { name: "제외종목 목록" });
const searchResult = (page: Page, name: RegExp) =>
  page.getByRole("region", { name: "제외종목 검색 결과" }).getByRole("button", { name });
const saveButton = (page: Page) => page.getByRole("button", { name: "저장", exact: true });

async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

test("자동매매 → SL / TP에서 안내 문구와 빈 설정을 보여준다", async ({ page }, info) => {
  const { loads } = await mockSlTp(page);
  await openSlTp(page);

  expect(loads).toEqual(["domestic_paper"]);
  await expect(page.getByText("평균 매입가 대비 수익률이 익절 기준 이상이거나 손절 기준 이하가 되면")).toBeVisible();
  await expect(page.getByText("자동매도는 아직 동작하지 않습니다. 지금은 설정만 저장됩니다.")).toBeVisible();
  await expect(page.getByText("제외종목이 없습니다")).toBeVisible();
  await expect(page.getByText("모든 SL/TP 세션에 똑같이 적용됩니다")).toBeVisible();
  await expect(saveButton(page)).toBeDisabled();
  await expectNoHorizontalScroll(page);
  await page.screenshot({ path: `screenshots/${info.project.name}-sl-tp.png`, fullPage: true });
});

test("제외종목을 검색해 추가·삭제하고 저장한다", async ({ page }) => {
  const { saved } = await mockSlTp(page);
  await openSlTp(page);

  await excludedInput(page).fill("삼성");
  await searchResult(page, /삼성전자/).click();
  await expect(excludedInput(page)).toHaveValue("");
  await expect(excludedList(page)).toContainText("삼성전자");
  await expect(excludedList(page)).toContainText("005930");
  await expect(page.getByText("저장하지 않은 변경")).toBeVisible();

  // 이미 추가된 종목은 "추가됨"으로 보이고 다시 추가되지 않는다.
  await excludedInput(page).fill("삼성");
  const added = searchResult(page, /삼성전자/);
  await expect(added).toContainText("추가됨");
  await expect(added).toBeDisabled();
  await searchResult(page, /SK하이닉스/).click();
  await excludedList(page).getByRole("button", { name: "SK하이닉스 삭제" }).click();
  await expect(excludedList(page)).not.toContainText("SK하이닉스");

  await saveButton(page).click();
  await expect(page.getByText("SL / TP 설정을 저장했습니다")).toBeVisible();
  expect(saved).toEqual([{ environment: "domestic_paper", body: { excluded: [SAMSUNG], sessions: [] } }]);
  await expect(page.getByText("저장하지 않은 변경")).toBeHidden();
  await expect(saveButton(page)).toBeDisabled();
});

test("미국 환경은 제외종목에 거래소를 보여주고 함께 저장한다", async ({ page }) => {
  const { saved } = await mockSlTp(page, { search: [searchItem("AAPL", "애플", "NASDAQ")] });
  await openSlTp(page);
  await page.getByRole("radio", { name: "미국 모의" }).click();

  await excludedInput(page).fill("apple");
  await searchResult(page, /애플/).click();
  await expect(excludedList(page)).toContainText("AAPL · NASDAQ");
  await saveButton(page).click();
  await expect(page.getByText("SL / TP 설정을 저장했습니다")).toBeVisible();
  expect(saved[0]).toEqual({
    environment: "us_paper",
    body: { excluded: [{ code: "AAPL", name: "애플", exchange: "NASDAQ" }], sessions: [] },
  });
});

test("저장에 실패하면 원인을 알리고 입력을 그대로 둔다", async ({ page }) => {
  await mockSlTp(page, {
    save: () => ({
      status: 500,
      body: { error: { kind: "sl_tp_settings_write_failed", message: "SL / TP 설정을 저장하지 못했습니다.", request_id: "req-1" } },
    }),
  });
  await openSlTp(page);

  await excludedInput(page).fill("삼성");
  await searchResult(page, /삼성전자/).click();
  await saveButton(page).click();

  await expect(page.getByRole("alert").filter({ hasText: "SL / TP 설정을 저장하지 못했습니다" }).first()).toBeVisible();
  await expect(excludedList(page)).toContainText("삼성전자");
  await expect(page.getByText("저장하지 않은 변경")).toBeVisible();
  await expect(saveButton(page)).toBeEnabled();
});

test("저장하는 동안에는 저장 버튼과 제외종목 편집이 막힌다", async ({ page }) => {
  await mockSlTp(page, {
    save: (environment, body) => ({ body: { ...settings(environment), ...body }, delayMs: 1000 }),
  });
  await openSlTp(page);

  await excludedInput(page).fill("삼성");
  await searchResult(page, /삼성전자/).click();
  await saveButton(page).click();

  await expect(page.getByRole("button", { name: "저장 중" })).toBeDisabled();
  await expect(excludedInput(page)).toBeDisabled();
  await expect(excludedList(page).getByRole("button", { name: "삼성전자 삭제" })).toBeDisabled();
  await expect(page.getByText("SL / TP 설정을 저장했습니다")).toBeVisible();
  await expect(excludedInput(page)).toBeEnabled();
});

test("설정을 불러오지 못하면 원인과 다시 시도를 보여주고, 다시 불러온다", async ({ page }) => {
  let fail = true;
  const { loads } = await mockSlTp(page, {
    load: (environment) =>
      fail
        ? {
            status: 500,
            body: { error: { kind: "sl_tp_settings_unreadable", message: "SL / TP 설정 파일을 읽지 못했습니다.", request_id: "req-2" } },
          }
        : { body: settings(environment, { excluded: [SAMSUNG] }) },
  });
  await openSlTp(page);

  await expect(page.getByText("SL / TP 설정 파일을 읽지 못했습니다.")).toBeVisible();
  await expect(excludedInput(page)).toBeHidden();
  fail = false;
  await page.getByRole("button", { name: "다시 시도" }).click();
  await expect(excludedList(page)).toContainText("삼성전자");
  expect(loads).toHaveLength(2);
});

test("투자 환경을 바꾸면 그 환경의 설정을 새로 불러온다", async ({ page }) => {
  const { loads } = await mockSlTp(page, {
    load: (environment) => ({
      body: settings(environment, { excluded: environment === "domestic_real" ? [SAMSUNG] : [] }),
    }),
  });
  await openSlTp(page);

  await page.getByRole("radio", { name: "국내 실전" }).click();
  await expect(excludedList(page)).toContainText("삼성전자");
  expect(loads).toEqual(["domestic_paper", "domestic_real"]);
});

const sessionList = (page: Page) => page.getByRole("list", { name: "SL/TP 세션 목록" });
const addSession = (page: Page) => page.getByRole("button", { name: "세션 추가" });
const startInput = (page: Page, n: number) => page.getByLabel(`${n}번째 세션 시작 시각`);
const endInput = (page: Page, n: number) => page.getByLabel(`${n}번째 세션 종료 시각`);
const off = (start: string, end: string) => ({ start, end, enabled: false, take_profit: null, stop_loss: null });

test("SL/TP 세션을 정규장 안에서 이어 붙여 추가·삭제하고 저장한다", async ({ page }) => {
  const { saved } = await mockSlTp(page);
  await openSlTp(page);

  await expect(page.getByText("SL/TP 세션이 없습니다")).toBeVisible();
  await expect(page.getByText("정규장 09:00–15:30 · 한국시간(Asia/Seoul)")).toBeVisible();

  await addSession(page).click();
  await expect(startInput(page, 1)).toHaveValue("09:00");
  await expect(endInput(page, 1)).toHaveValue("10:00");
  await addSession(page).click();
  await expect(startInput(page, 2)).toHaveValue("10:00");
  await expect(endInput(page, 2)).toHaveValue("11:00");

  await endInput(page, 2).fill("15:00");
  await addSession(page).click();
  // 정규장 끝을 넘지 않게 자른다.
  await expect(startInput(page, 3)).toHaveValue("15:00");
  await expect(endInput(page, 3)).toHaveValue("15:30");
  await expect(addSession(page)).toBeDisabled();
  await expect(page.getByText("정규장 끝까지 SL/TP 세션이 있습니다")).toBeVisible();

  await sessionList(page).getByRole("button", { name: "2번째 세션 삭제" }).click();
  await expect(sessionList(page).getByRole("listitem")).toHaveCount(2);
  await expect(startInput(page, 2)).toHaveValue("15:00");

  await saveButton(page).click();
  await expect(page.getByText("SL / TP 설정을 저장했습니다")).toBeVisible();
  expect(saved[0].body.sessions).toEqual([off("09:00", "10:00"), off("15:00", "15:30")]);
});

test("세션 시각이 잘못되면 그 세션에 안내하고 저장을 막는다", async ({ page }) => {
  await mockSlTp(page, {
    load: (environment) => ({ body: settings(environment, { sessions: [off("09:00", "10:00"), off("10:00", "11:00")] }) }),
  });
  await openSlTp(page);
  const items = sessionList(page).getByRole("listitem");

  await startInput(page, 2).fill("09:30");
  await expect(items.nth(1)).toContainText("시작 시각이 앞 세션의 종료 시각보다 이릅니다");
  await expect(saveButton(page)).toBeDisabled();

  await startInput(page, 2).fill("10:00");
  await endInput(page, 2).fill("10:00");
  await expect(items.nth(1)).toContainText("시작 시각이 종료 시각보다 빨라야 합니다");

  await endInput(page, 2).fill("15:40");
  await expect(items.nth(1)).toContainText("정규장(09:00–15:30) 안에서만 정할 수 있습니다");
  await expect(saveButton(page)).toBeDisabled();

  // 맞닿은 세션은 허용한다.
  await endInput(page, 2).fill("15:30");
  await expect(items.nth(1)).not.toContainText("정규장");
  await expect(saveButton(page)).toBeEnabled();
});

test("미국 환경은 뉴욕시간 정규장으로 세션을 만든다", async ({ page }) => {
  await mockSlTp(page);
  await openSlTp(page);
  await page.getByRole("radio", { name: "미국 모의" }).click();

  await expect(page.getByText("정규장 09:30–16:00 · 뉴욕시간(America/New_York)")).toBeVisible();
  await addSession(page).click();
  await expect(startInput(page, 1)).toHaveValue("09:30");
  await expect(endInput(page, 1)).toHaveValue("10:30");
  await startInput(page, 1).fill("09:29");
  await expect(sessionList(page)).toContainText("정규장(09:30–16:00) 안에서만 정할 수 있습니다");
  await expectNoHorizontalScroll(page);
});

test("저장하는 동안에는 세션 추가·삭제·시각 편집이 막힌다", async ({ page }) => {
  await mockSlTp(page, {
    save: (environment, body) => ({ body: { ...settings(environment), ...body }, delayMs: 1000 }),
  });
  await openSlTp(page);

  await addSession(page).click();
  await saveButton(page).click();
  await expect(page.getByRole("button", { name: "저장 중" })).toBeDisabled();
  await expect(addSession(page)).toBeDisabled();
  await expect(startInput(page, 1)).toBeDisabled();
  await expect(sessionList(page).getByRole("button", { name: "1번째 세션 삭제" })).toBeDisabled();
  await expect(page.getByText("SL / TP 설정을 저장했습니다")).toBeVisible();
  await expect(startInput(page, 1)).toBeEnabled();
});

const switchOf = (page: Page, n: number) => page.getByRole("switch", { name: `${n}번째 세션 켜기` });
const takeProfit = (page: Page, n: number) => page.getByLabel(`${n}번째 세션 익절 기준(%)`);
const stopLoss = (page: Page, n: number) => page.getByLabel(`${n}번째 세션 손절 기준(%)`);

test("세션마다 켜고 익절·손절 기준을 정해 저장한다", async ({ page }, info) => {
  const { saved } = await mockSlTp(page);
  await openSlTp(page);

  await addSession(page).click();
  await addSession(page).click();
  await expect(switchOf(page, 1)).toHaveAttribute("aria-checked", "false");
  await expect(takeProfit(page, 1)).toHaveValue("");

  await switchOf(page, 1).click();
  await expect(switchOf(page, 1)).toHaveAttribute("aria-checked", "true");
  await expect(sessionList(page)).toContainText("켜진 세션에는 익절 기준이나 손절 기준이 하나 이상 있어야 합니다.");
  await expect(saveButton(page)).toBeDisabled();

  await takeProfit(page, 1).fill("5");
  await stopLoss(page, 1).fill("2.5");
  await expect(sessionList(page).getByRole("listitem").first()).toContainText("+5%");
  await expect(sessionList(page).getByRole("listitem").first()).toContainText("−2.5%");
  // 꺼진 세션은 손절 기준만 정해도 된다.
  await stopLoss(page, 2).fill("3");
  await page.screenshot({ path: `screenshots/${info.project.name}-sl-tp-sessions.png`, fullPage: true });

  await saveButton(page).click();
  await expect(page.getByText("SL / TP 설정을 저장했습니다")).toBeVisible();
  expect(saved[0].body.sessions).toEqual([
    { start: "09:00", end: "10:00", enabled: true, take_profit: "5", stop_loss: "2.5" },
    { start: "10:00", end: "11:00", enabled: false, take_profit: null, stop_loss: "3" },
  ]);
});

test("잘못된 익절·손절 기준은 안내하고 저장을 막는다", async ({ page }) => {
  await mockSlTp(page, { load: (environment) => ({ body: settings(environment, { sessions: [off("09:00", "10:00")] }) }) });
  await openSlTp(page);
  const item = sessionList(page).getByRole("listitem").first();

  await takeProfit(page, 1).fill("1.234");
  await expect(item).toContainText("익절 기준은 0.01~1000 사이, 소수점 둘째 자리까지의 숫자여야 합니다.");
  await expect(saveButton(page)).toBeDisabled();
  await takeProfit(page, 1).fill("1000");
  await stopLoss(page, 1).fill("100");
  await expect(item).toContainText("손절 기준은 0.01~99.99 사이, 소수점 둘째 자리까지의 숫자여야 합니다.");
  await stopLoss(page, 1).fill("");
  await expect(saveButton(page)).toBeEnabled();
});

test("실전투자에서는 스위치를 켤 수 없지만 시각과 기준은 저장한다", async ({ page }) => {
  const { saved } = await mockSlTp(page);
  await openSlTp(page);
  await page.getByRole("radio", { name: "국내 실전" }).click();

  await expect(page.getByText("실전투자에서는 SL/TP 세션을 켤 수 없습니다")).toBeVisible();
  await addSession(page).click();
  await expect(switchOf(page, 1)).toBeDisabled();
  await expect(switchOf(page, 1)).toHaveAttribute("aria-checked", "false");
  await takeProfit(page, 1).fill("5");
  await saveButton(page).click();
  await expect(page.getByText("SL / TP 설정을 저장했습니다")).toBeVisible();
  expect(saved[0]).toEqual({
    environment: "domestic_real",
    body: { excluded: [], sessions: [{ start: "09:00", end: "10:00", enabled: false, take_profit: "5", stop_loss: null }] },
  });
});

test("저장하는 동안에는 스위치와 기준 입력도 막힌다", async ({ page }) => {
  await mockSlTp(page, {
    save: (environment, body) => ({ body: { ...settings(environment), ...body }, delayMs: 1000 }),
  });
  await openSlTp(page);

  await addSession(page).click();
  await saveButton(page).click();
  await expect(switchOf(page, 1)).toBeDisabled();
  await expect(takeProfit(page, 1)).toBeDisabled();
  await expect(stopLoss(page, 1)).toBeDisabled();
  await expect(page.getByText("SL / TP 설정을 저장했습니다")).toBeVisible();
  await expect(switchOf(page, 1)).toBeEnabled();
  await expectNoHorizontalScroll(page);
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

/** 확인 창이 뜨면 실패하게 한다. */
function failOnDialog(page: Page): string[] {
  const messages: string[] = [];
  page.on("dialog", (dialog) => {
    messages.push(dialog.message());
    void dialog.dismiss();
  });
  return messages;
}

async function makeChange(page: Page) {
  await addSession(page).click();
  await expect(page.getByText("저장하지 않은 변경")).toBeVisible();
}

const beforeUnloadBlocked = (page: Page) =>
  page.evaluate(() => {
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  });

async function clickMenu(page: Page, name: string) {
  const menu = page.getByRole("button", { name: "메뉴 열기" });
  if (await menu.isVisible()) await menu.click();
  await page.getByRole("button", { name, exact: true }).filter({ visible: true }).click();
}

test("저장하지 않은 변경이 있으면 투자 환경을 바꾸기 전에 묻는다", async ({ page }) => {
  const { loads } = await mockSlTp(page);
  await openSlTp(page);
  await makeChange(page);

  let asked = answerNextConfirm(page, false);
  await page.getByRole("radio", { name: "미국 모의" }).click();
  await expect.poll(() => asked).toEqual([LEAVE_MESSAGE]);
  await expect(page.getByRole("radio", { name: "국내 모의" })).toHaveAttribute("aria-checked", "true");
  await expect(startInput(page, 1)).toHaveValue("09:00");

  asked = answerNextConfirm(page, true);
  await page.getByRole("radio", { name: "미국 모의" }).click();
  await expect(page.getByRole("radio", { name: "미국 모의" })).toHaveAttribute("aria-checked", "true");
  await expect(page.getByText("SL/TP 세션이 없습니다")).toBeVisible();
  expect(asked).toEqual([LEAVE_MESSAGE]);
  expect(loads).toEqual(["domestic_paper", "us_paper"]);
});

test("저장하지 않은 변경이 있으면 다른 메뉴로 가기 전에 묻는다", async ({ page }) => {
  await mockSlTp(page);
  await openSlTp(page);
  await makeChange(page);

  answerNextConfirm(page, false);
  await clickMenu(page, "계좌 확인");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("heading", { name: "SL / TP" })).toBeVisible();
  await expect(startInput(page, 1)).toHaveValue("09:00");

  answerNextConfirm(page, true);
  await clickMenu(page, "계좌 확인");
  await expect(page.getByRole("heading", { name: "계좌 확인" })).toBeVisible();
});

test("저장하지 않은 변경이 있으면 로그아웃 전에 묻는다", async ({ page }) => {
  await mockSlTp(page);
  const auth = await openDashboard(page);
  await selectSlTpMenu(page);
  await makeChange(page);
  // 대시보드를 열 때 이전 로그인 세션을 끝내는 요청이 이미 한 번 나간다.
  const before = auth.logoutRequests();

  answerNextConfirm(page, false);
  await clickMenu(page, "로그아웃");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("heading", { name: "SL / TP" })).toBeVisible();
  expect(auth.logoutRequests()).toBe(before);

  answerNextConfirm(page, true);
  await clickMenu(page, "로그아웃");
  await expect(page.getByLabel("비밀번호")).toBeVisible();
  expect(auth.logoutRequests()).toBe(before + 1);
});

test("새로고침·탭 닫기는 변경이 있을 때만 브라우저가 경고한다", async ({ page }) => {
  await mockSlTp(page);
  await openSlTp(page);

  expect(await beforeUnloadBlocked(page)).toBe(false);
  await makeChange(page);
  await expect.poll(() => beforeUnloadBlocked(page)).toBe(true);
  await saveButton(page).click();
  await expect(page.getByText("SL / TP 설정을 저장했습니다")).toBeVisible();
  await expect.poll(() => beforeUnloadBlocked(page)).toBe(false);
});

test("변경이 없거나 저장한 뒤에는 묻지 않고 이동한다", async ({ page }) => {
  await mockSlTp(page);
  const asked = failOnDialog(page);
  await openSlTp(page);

  await page.getByRole("radio", { name: "미국 모의" }).click();
  await expect(page.getByText("정규장 09:30–16:00 · 뉴욕시간(America/New_York)")).toBeVisible();
  await makeChange(page);
  await saveButton(page).click();
  await expect(page.getByText("SL / TP 설정을 저장했습니다")).toBeVisible();
  await clickMenu(page, "계좌 확인");
  await expect(page.getByRole("heading", { name: "계좌 확인" })).toBeVisible();
  expect(asked).toEqual([]);
});

test("로그인이 만료되면 묻지 않고 저장하지 않은 입력을 버린다", async ({ page }) => {
  await mockSlTp(page, {
    save: () => ({ status: 401, body: { error: { kind: "unauthorized", message: "로그인이 필요합니다." } } }),
  });
  const asked = failOnDialog(page);
  await openSlTp(page);
  await makeChange(page);

  await saveButton(page).click();
  await expect(page.getByLabel("비밀번호")).toBeVisible();
  await expect(page.getByText("로그인이 만료되었습니다. 다시 로그인하세요.")).toBeVisible();
  await expect.poll(() => beforeUnloadBlocked(page)).toBe(false);
  expect(asked).toEqual([]);
});
