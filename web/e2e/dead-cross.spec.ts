import { expect, test, type Page, type Route } from "@playwright/test";
import { mockNoOpenOrders, openDashboard } from "./support";

// 브라우저의 /api 요청을 가로채 가짜 응답을 준다. 키움 서버나 실전 서버는 호출되지 않는다.
// 제외종목·세션 시각·저장 흐름은 공용 화면이라 SL / TP·Trailing Stop e2e가 자세히 본다. 여기서는 대표 경우만 본다.

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

const searchItem = (code: string, name: string) => ({
  code,
  name,
  english_name: null,
  exchange: null,
  category: "코스피",
  industry: null,
  status: null,
  is_etf: null,
});

type Reply = { status?: number; body: unknown; delayMs?: number };

/** Dead Cross·Trailing Stop 조회·저장과 종목 검색을 가짜로 응답한다. Dead Cross 저장 본문을 모아 돌려준다. */
async function mockDeadCross(
  page: Page,
  {
    load = (environment: string): Reply => ({ body: settings(environment) }),
    save = (environment: string, body: Partial<Settings>): Reply => ({
      body: { ...settings(environment), ...body, updated_at: "2026-11-02T01:00:00+00:00" },
    }),
  }: {
    load?: (environment: string) => Reply;
    save?: (environment: string, body: Partial<Settings>) => Reply;
  } = {},
) {
  const saved: { environment: string; body: Partial<Settings> }[] = [];
  const loads: string[] = [];
  await page.route("**/api/environments/*/dead-cross", async (route: Route) => {
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
  await page.route("**/api/environments/*/trailing-stop", (route) => {
    const environment = new URL(route.request().url()).pathname.split("/")[3];
    return route.fulfill({ json: settings(environment) });
  });
  await page.route("**/api/environments/*/stocks?*", (route) => {
    const url = new URL(route.request().url());
    const items = [searchItem("005930", "삼성전자"), searchItem("000660", "SK하이닉스")];
    return route.fulfill({
      json: {
        environment: url.pathname.split("/")[3],
        market: "domestic",
        query: url.searchParams.get("q"),
        total: items.length,
        truncated: false,
        list_fetched_at: "2026-11-02T00:00:00+00:00",
        items,
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

async function openDeadCross(page: Page) {
  await openDashboard(page);
  await selectAutoTradingMenu(page, "Dead Cross");
  await expect(page.getByRole("heading", { name: "Dead Cross", exact: true })).toBeVisible();
}

const excludedInput = (page: Page) => page.getByRole("searchbox", { name: "제외종목 검색어" });
const excludedList = (page: Page) => page.getByRole("list", { name: "제외종목 목록" });
const saveButton = (page: Page) => page.getByRole("button", { name: "저장", exact: true });
const sessionList = (page: Page) => page.getByRole("list", { name: "Dead Cross 세션 목록" });
const addSession = (page: Page) => page.getByRole("button", { name: "세션 추가" });
const switchOf = (page: Page, n: number) => page.getByRole("switch", { name: `${n}번째 세션 켜기` });
const barPeriod = (page: Page, n: number) => page.getByLabel(`${n}번째 세션 봉 주기`);
const shortPeriod = (page: Page, n: number) => page.getByLabel(`${n}번째 세션 단기 이동평균 기간(봉)`);
const longPeriod = (page: Page, n: number) => page.getByLabel(`${n}번째 세션 장기 이동평균 기간(봉)`);
const off = (start: string, end: string) => ({
  start,
  end,
  enabled: false,
  bar_period: null,
  short_period: null,
  long_period: null,
});

async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

test("자동매매 → Trailing Stop 아래의 Dead Cross에서 안내 문구와 빈 설정을 보여준다", async ({ page }, info) => {
  const { loads } = await mockDeadCross(page);
  await openDashboard(page);
  const menu = page.getByRole("button", { name: "메뉴 열기" });
  if (await menu.isVisible()) await menu.click();
  const items = page.getByRole("group", { name: "자동매매" }).filter({ visible: true }).getByRole("button");
  await expect(items).toHaveText(["SL / TP", "Trailing Stop", "Dead Cross"]);
  await items.nth(2).click();

  await expect(page.getByRole("heading", { name: "Dead Cross", exact: true })).toBeVisible();
  expect(loads).toEqual(["domestic_paper"]);
  await expect(
    page.getByText(
      "봉 주기마다 완료된 봉의 종가로 단기·장기 이동평균(단순 이동평균)을 계산해, 단기 이동평균이 장기 이동평균을 하향 돌파하면 그 종목의 매도 가능 수량 전량을 시장가로 매도합니다. 제외종목은 매도하지 않습니다.",
    ),
  ).toBeVisible();
  await expect(page.getByText("지금은 상태만 계산하고 팔지 않습니다. 토·일에는 동작하지 않습니다. 계좌 확인 화면의 Dead Cross 보기에서 확인하세요.")).toBeVisible();
  await expect(page.getByText("제외종목이 없습니다")).toBeVisible();
  await expect(page.getByText("모든 Dead Cross 세션에 똑같이 적용됩니다")).toBeVisible();
  await expect(page.getByText("Dead Cross 세션이 없습니다")).toBeVisible();
  await expect(saveButton(page)).toBeDisabled();
  await expectNoHorizontalScroll(page);
  await page.screenshot({ path: `screenshots/${info.project.name}-dead-cross.png`, fullPage: true });
});

test("봉 주기는 그 시장에서 키움이 주는 분봉과 일봉만 고를 수 있다", async ({ page }) => {
  await mockDeadCross(page, {
    load: (environment) => ({
      body: settings(environment, { sessions: [off(environment.startsWith("us") ? "09:30" : "09:00", "11:00")] }),
    }),
  });
  await openDeadCross(page);

  await expect(barPeriod(page, 1).getByRole("option")).toHaveText([
    "선택",
    "1분봉",
    "3분봉",
    "5분봉",
    "10분봉",
    "15분봉",
    "30분봉",
    "45분봉",
    "60분봉",
    "일봉",
  ]);
  await page.getByRole("radio", { name: "미국 모의" }).click();
  await expect(page.getByText("정규장 09:30–16:00 · 뉴욕시간(America/New_York)")).toBeVisible();
  await expect(barPeriod(page, 1).getByRole("option")).toHaveText([
    "선택",
    "1분봉",
    "3분봉",
    "5분봉",
    "10분봉",
    "15분봉",
    "30분봉",
    "40분봉",
    "60분봉",
    "120분봉",
    "일봉",
  ]);
});

test("세션을 켜고 봉 주기와 이동평균 기간을 정해 저장한다", async ({ page }, info) => {
  const { saved } = await mockDeadCross(page);
  await openDeadCross(page);

  await addSession(page).click();
  await expect(switchOf(page, 1)).toHaveAttribute("aria-checked", "false");
  await expect(barPeriod(page, 1)).toHaveValue("");
  await expect(shortPeriod(page, 1)).toHaveValue("");
  await expect(longPeriod(page, 1)).toHaveValue("");
  await addSession(page).click();
  await switchOf(page, 1).click();
  await expect(sessionList(page)).toContainText("켜진 세션에는 봉 주기와 단기·장기 이동평균 기간이 모두 있어야 합니다.");
  await expect(saveButton(page)).toBeDisabled();
  await barPeriod(page, 1).selectOption({ label: "5분봉" });
  await shortPeriod(page, 1).fill("5");
  await expect(saveButton(page)).toBeDisabled();
  await longPeriod(page, 1).fill("20");
  const first = sessionList(page).getByRole("listitem").first();
  await expect(first.getByText("봉", { exact: true })).toHaveCount(2);
  await expect(first).toContainText("5분봉 · 단기 5봉 · 장기 20봉");
  // 꺼진 세션은 봉 주기만 정해 두어도 저장된다.
  await barPeriod(page, 2).selectOption({ label: "일봉" });
  await page.screenshot({ path: `screenshots/${info.project.name}-dead-cross-sessions.png`, fullPage: true });

  await saveButton(page).click();
  await expect(page.getByText("Dead Cross 설정을 저장했습니다")).toBeVisible();
  expect(saved[0]).toEqual({
    environment: "domestic_paper",
    body: {
      excluded: [],
      sessions: [
        { start: "09:00", end: "10:00", enabled: true, bar_period: "5m", short_period: 5, long_period: 20 },
        { ...off("10:00", "11:00"), bar_period: "1d" },
      ],
    },
  });
  await expectNoHorizontalScroll(page);
});

test("잘못된 이동평균 기간은 그 칸에 안내하고 저장을 막는다", async ({ page }) => {
  await mockDeadCross(page, { load: (environment) => ({ body: settings(environment, { sessions: [off("09:00", "10:00")] }) }) });
  await openDeadCross(page);
  const shortError = "단기 이동평균 기간은 2~119 사이의 정수여야 합니다.";
  const longError = "장기 이동평균 기간은 3~120 사이의 정수여야 합니다.";
  const orderError = "단기 이동평균 기간은 장기 이동평균 기간보다 작아야 합니다.";

  await shortPeriod(page, 1).fill("1");
  await expect(shortPeriod(page, 1)).toHaveAccessibleDescription(shortError);
  await expect(saveButton(page)).toBeDisabled();
  await shortPeriod(page, 1).fill("5.5");
  await expect(shortPeriod(page, 1)).toHaveAccessibleDescription(shortError);
  // 숫자가 아닌 글자는 빈 값으로 바뀌지 않고 그대로 남아 안내된다.
  await shortPeriod(page, 1).fill("2O");
  await expect(shortPeriod(page, 1)).toHaveValue("2O");
  await expect(shortPeriod(page, 1)).toHaveAccessibleDescription(shortError);
  await shortPeriod(page, 1).fill("119");
  await expect(shortPeriod(page, 1)).toHaveAccessibleDescription("");
  await longPeriod(page, 1).fill("121");
  await expect(longPeriod(page, 1)).toHaveAccessibleDescription(longError);
  // 꺼진 세션이어도 단기가 장기 이상이면 막는다.
  await longPeriod(page, 1).fill("60");
  await expect(longPeriod(page, 1)).toHaveAccessibleDescription(orderError);
  await expect(shortPeriod(page, 1)).toHaveAccessibleDescription(orderError);
  await expect(saveButton(page)).toBeDisabled();
  await longPeriod(page, 1).fill("120");
  await expect(longPeriod(page, 1)).toHaveAccessibleDescription("");
  await expect(saveButton(page)).toBeEnabled();
  await longPeriod(page, 1).fill("");
  await expect(saveButton(page)).toBeEnabled();
});

test("실전투자에서는 스위치를 켤 수 없지만 시각·봉 주기·기간은 저장한다", async ({ page }) => {
  const { saved } = await mockDeadCross(page);
  await openDeadCross(page);
  await page.getByRole("radio", { name: "국내 실전" }).click();

  await expect(page.getByText("실전투자에서는 Dead Cross 세션을 켤 수 없습니다")).toBeVisible();
  await addSession(page).click();
  await expect(switchOf(page, 1)).toBeDisabled();
  await barPeriod(page, 1).selectOption({ label: "60분봉" });
  await shortPeriod(page, 1).fill("5");
  await longPeriod(page, 1).fill("20");
  await saveButton(page).click();
  await expect(page.getByText("Dead Cross 설정을 저장했습니다")).toBeVisible();
  expect(saved[0]).toEqual({
    environment: "domestic_real",
    body: {
      excluded: [],
      sessions: [{ ...off("09:00", "10:00"), bar_period: "60m", short_period: 5, long_period: 20 }],
    },
  });
});

test("제외종목을 추가·삭제하고, 세션을 삭제해 저장한다", async ({ page }) => {
  const { saved } = await mockDeadCross(page, {
    load: (environment) => ({ body: settings(environment, { sessions: [off("09:00", "10:00"), off("10:00", "11:00")] }) }),
  });
  await openDeadCross(page);

  await excludedInput(page).fill("삼성");
  await page.getByRole("region", { name: "제외종목 검색 결과" }).getByRole("button", { name: /삼성전자/ }).click();
  await expect(excludedList(page)).toContainText("005930");
  await excludedInput(page).fill("하이닉스");
  await page.getByRole("region", { name: "제외종목 검색 결과" }).getByRole("button", { name: /SK하이닉스/ }).click();
  await excludedList(page).getByRole("button", { name: "SK하이닉스 삭제" }).click();
  await sessionList(page).getByRole("button", { name: "1번째 세션 삭제" }).click();

  await saveButton(page).click();
  await expect(page.getByText("Dead Cross 설정을 저장했습니다")).toBeVisible();
  expect(saved[0].body).toEqual({
    excluded: [{ code: "005930", name: "삼성전자", exchange: null }],
    sessions: [off("10:00", "11:00")],
  });
});

test("저장이 실패하면 원인을 알리고 입력을 두며, 불러오기 실패면 다시 시도를 보여준다", async ({ page }) => {
  let failLoad = true;
  await mockDeadCross(page, {
    load: (environment) =>
      failLoad
        ? {
            status: 500,
            body: {
              error: {
                kind: "dead_cross_settings_unreadable",
                message: "Dead Cross 설정 파일을 읽지 못했습니다.",
                request_id: "req-1",
              },
            },
          }
        : { body: settings(environment) },
    save: () => ({
      status: 500,
      body: {
        error: {
          kind: "dead_cross_settings_write_failed",
          message: "Dead Cross 설정을 저장하지 못했습니다.",
          request_id: "req-2",
        },
      },
      delayMs: 500,
    }),
  });
  await openDeadCross(page);

  await expect(page.getByText("Dead Cross 설정 파일을 읽지 못했습니다.")).toBeVisible();
  await expect(excludedInput(page)).toBeHidden();
  failLoad = false;
  await page.getByRole("button", { name: "다시 시도" }).click();
  await addSession(page).click();
  await shortPeriod(page, 1).fill("5");
  await saveButton(page).click();
  await expect(page.getByRole("button", { name: "저장 중" })).toBeDisabled();
  await expect(barPeriod(page, 1)).toBeDisabled();
  await expect(shortPeriod(page, 1)).toBeDisabled();
  await expect(page.getByRole("alert").filter({ hasText: "Dead Cross 설정을 저장하지 못했습니다" }).first()).toBeVisible();
  await expect(shortPeriod(page, 1)).toHaveValue("5");
  await expect(page.getByText("저장하지 않은 변경")).toBeVisible();
});

const LEAVE_MESSAGE = "저장하지 않은 변경이 사라집니다. 이동할까요?";

test("저장하지 않은 변경이 있으면 Trailing Stop으로 가기 전에 묻는다", async ({ page }) => {
  await mockDeadCross(page);
  await openDeadCross(page);
  await addSession(page).click();

  const asked: string[] = [];
  page.once("dialog", (dialog) => {
    asked.push(dialog.message());
    void dialog.dismiss();
  });
  await selectAutoTradingMenu(page, "Trailing Stop");
  await expect.poll(() => asked).toEqual([LEAVE_MESSAGE]);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("heading", { name: "Dead Cross", exact: true })).toBeVisible();
  await expect(sessionList(page).getByRole("listitem")).toHaveCount(1);
});

test("가장 긴 값도 입력 칸 안에 다 보인다", async ({ page }) => {
  const longest = { ...off("09:00", "10:00"), bar_period: "60m", short_period: 119, long_period: 120 };
  await mockDeadCross(page, { load: (environment) => ({ body: settings(environment, { sessions: [longest] }) }) });
  await openDeadCross(page);

  for (const input of [barPeriod(page, 1), shortPeriod(page, 1), longPeriod(page, 1)]) {
    const [scroll, client] = await input.evaluate((el: HTMLElement) => [el.scrollWidth, el.clientWidth]);
    expect(scroll).toBeLessThanOrEqual(client);
  }
  await expectNoHorizontalScroll(page);
});
