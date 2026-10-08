import { expect, test, type Page } from "@playwright/test";
import { mockAuth, mockNoOpenOrders, openDashboard, PASSWORD } from "./support";

// 브라우저의 /api 요청을 가로채 가짜 응답을 준다. 키움 서버나 실제 서버는 호출되지 않는다.

const ACCOUNT = {
  environment: "domestic_paper",
  market: "domestic",
  account_no: "8135****11",
  fetched_at: "2026-10-05T06:30:00+00:00",
  summary: {
    estimated_assets: 1_000,
    total_evaluation: 0,
    total_purchase: 0,
    total_profit_loss: 0,
    total_return_rate: 0,
  },
  deposit: { deposit: 1_000, orderable: 1_000, withdrawable: 1_000, d1_estimated: 1_000, d2_estimated: 1_000 },
  holdings: [],
};

type Reply = { status: number; json: unknown };

async function mockAccount(page: Page, reply: () => Reply = () => ({ status: 200, json: ACCOUNT })) {
  const requests: string[] = [];
  await page.route("**/api/environments/*/account", (route) => {
    requests.push(route.request().url());
    return route.fulfill(reply());
  });
  await mockNoOpenOrders(page);
  return requests;
}

const passwordInput = (page: Page) => page.getByLabel("비밀번호");
const loginButton = (page: Page) => page.getByRole("button", { name: "로그인" });

test("로그인하지 않고 열면 로그인 화면만 보이고 계좌를 요청하지 않는다", async ({ page }) => {
  const accountRequests = await mockAccount(page);
  await mockAuth(page);
  await page.goto("/");

  await expect(passwordInput(page)).toBeVisible();
  await expect(page.getByRole("heading", { name: "계좌 확인" })).toHaveCount(0);
  await expect(loginButton(page)).toBeDisabled();
  expect(accountRequests).toEqual([]);
});

test("맞는 비밀번호로 로그인하면 계좌 확인이 보인다", async ({ page }) => {
  const accountRequests = await mockAccount(page);
  const { loginRequests } = await mockAuth(page);
  await page.goto("/");
  await passwordInput(page).fill(PASSWORD);
  await passwordInput(page).press("Enter");

  await expect(page.getByRole("heading", { name: "계좌 확인" })).toBeVisible();
  expect(loginRequests).toEqual([PASSWORD]);
  await expect.poll(() => accountRequests.length).toBeGreaterThan(0);
});

test("틀린 비밀번호는 안내가 보이고 입력 칸이 비워진다", async ({ page }) => {
  await mockAccount(page);
  await mockAuth(page);
  await page.goto("/");
  await passwordInput(page).fill("wrong");
  await loginButton(page).click();

  await expect(page.getByRole("alert")).toHaveText("비밀번호가 올바르지 않습니다.");
  await expect(passwordInput(page)).toHaveValue("");
  await expect(page.getByRole("heading", { name: "계좌 확인" })).toHaveCount(0);
});

test("보내는 동안 로그인 버튼이 잠긴다", async ({ page }) => {
  await mockAccount(page);
  let release!: () => void;
  const held = new Promise<void>((resolve) => (release = resolve));
  await mockAuth(page);
  await page.route("**/api/auth/login", async (route) => {
    await held;
    await route.fulfill({ status: 204 });
  });
  await page.goto("/");
  await passwordInput(page).fill(PASSWORD);
  await loginButton(page).click();

  await expect(page.getByRole("button", { name: "확인 중…" })).toBeDisabled();
  release();
});

test("로그인 후 요청이 401이면 로그인 화면으로 돌아가고 만료 안내가 보인다", async ({ page }) => {
  let expired = false;
  await mockAccount(page, () =>
    expired
      ? { status: 401, json: { error: { kind: "unauthorized", message: "로그인이 필요합니다." } } }
      : { status: 200, json: ACCOUNT },
  );
  await openDashboard(page);
  await expect(page.getByRole("heading", { name: "계좌 확인" })).toBeVisible();

  expired = true;
  await page.getByRole("button", { name: "새로고침" }).click();

  await expect(passwordInput(page)).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("로그인이 만료되었습니다");
});

async function clickLogout(page: Page) {
  const menu = page.getByRole("button", { name: "메뉴 열기" });
  if (await menu.isVisible()) await menu.click();
  await page.getByRole("button", { name: "로그아웃" }).filter({ visible: true }).click();
}

test("로그인 후 새로고침하면 로그아웃하지 않고 대시보드를 유지한다", async ({ page }) => {
  await mockAccount(page);
  const { logoutRequests } = await openDashboard(page);
  const before = logoutRequests();

  await page.reload();

  await expect(page.getByRole("heading", { name: "계좌 확인" })).toBeVisible();
  expect(logoutRequests()).toBe(before);
});

test("새로고침이 아니라 다시 열면 로그인 세션을 끝내고 비밀번호를 다시 묻는다", async ({ page }) => {
  await mockAccount(page);
  const { logoutRequests } = await openDashboard(page);
  const before = logoutRequests();

  await page.goto("/");

  await expect(passwordInput(page)).toBeVisible();
  await expect.poll(logoutRequests).toBe(before + 1);
  await expect(page.getByRole("heading", { name: "계좌 확인" })).toHaveCount(0);
});

test("뒤로가기로 저장된 페이지가 다시 보이면 로그인 세션을 끝낸다", async ({ page }) => {
  await mockAccount(page);
  const { logoutRequests } = await openDashboard(page);
  const before = logoutRequests();

  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));

  await expect(passwordInput(page)).toBeVisible();
  await expect.poll(logoutRequests).toBe(before + 1);
});

test("로그아웃 버튼을 누르면 로그인 화면으로 가고 투자 환경 선택은 남는다", async ({ page }) => {
  await mockAccount(page, () => ({ status: 200, json: { ...ACCOUNT, environment: "us_paper" } }));
  const { logoutRequests } = await openDashboard(page);
  await page.getByRole("radio", { name: "미국 모의" }).filter({ visible: true }).click();
  const before = logoutRequests();

  await clickLogout(page);

  await expect(passwordInput(page)).toBeVisible();
  await expect.poll(logoutRequests).toBe(before + 1);
  expect(await page.evaluate(() => localStorage.getItem("stock-bot:environment"))).toBe("us_paper");
});

test("같은 브라우저에서 새 탭을 열면 로그인 세션이 끝나고, 다시 로그인하면 탭들이 함께 쓴다", async ({ context }) => {
  // 서버처럼 브라우저(컨텍스트) 하나에 로그인 세션 하나를 둔다.
  let loggedIn = false;
  const logouts: string[] = [];
  await context.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/login") {
      loggedIn = true;
      return route.fulfill({ status: 204 });
    }
    if (path === "/api/auth/logout") {
      logouts.push(path);
      loggedIn = false;
      return route.fulfill({ status: 204 });
    }
    if (!loggedIn) {
      return route.fulfill({ status: 401, json: { error: { kind: "unauthorized", message: "로그인이 필요합니다." } } });
    }
    if (path === "/api/auth/session") return route.fulfill({ json: { authenticated: true } });
    if (path.endsWith("/open-orders")) return route.fulfill({ json: { orders: [], fetched_at: ACCOUNT.fetched_at } });
    return route.fulfill({ json: ACCOUNT });
  });
  const login = async (page: Page) => {
    await passwordInput(page).fill(PASSWORD);
    await loginButton(page).click();
    await expect(page.getByRole("heading", { name: "계좌 확인" })).toBeVisible();
  };

  const first = await context.newPage();
  await first.goto("/");
  await login(first);

  const second = await context.newPage();
  await second.goto("/");
  await expect(passwordInput(second)).toBeVisible();
  expect(logouts).toHaveLength(2);

  await first.getByRole("button", { name: "새로고침" }).click();
  await expect(passwordInput(first)).toBeVisible();
  await expect(first.getByRole("alert")).toContainText("로그인이 만료되었습니다");

  await login(second);
  await first.reload();
  await expect(first.getByRole("heading", { name: "계좌 확인" })).toBeVisible();
});

test("30분 동안 입력이 없으면 로그인 화면으로 바뀌고 만료 안내가 보인다", async ({ page }) => {
  await page.clock.install();
  await mockAccount(page);
  const { logoutRequests } = await openDashboard(page);
  const before = logoutRequests();

  await page.clock.fastForward("29:50");
  await expect(page.getByRole("heading", { name: "계좌 확인" })).toBeVisible();
  await page.clock.fastForward("00:20");

  await expect(passwordInput(page)).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("로그인이 만료되었습니다");
  await expect.poll(logoutRequests).toBe(before + 1);
});

test("입력이 있으면 30분을 다시 센다", async ({ page }) => {
  await page.clock.install();
  await mockAccount(page);
  await openDashboard(page);

  await page.clock.fastForward("20:00");
  await page.keyboard.press("Shift");
  await page.clock.fastForward("20:00");

  await expect(page.getByRole("heading", { name: "계좌 확인" })).toBeVisible();
  await expect(passwordInput(page)).toHaveCount(0);
});

test("시도 제한에 걸리면 남은 시간을 보여준다", async ({ page }) => {
  await mockAccount(page);
  await mockAuth(page);
  await page.route("**/api/auth/login", (route) =>
    route.fulfill({
      status: 429,
      json: {
        error: {
          kind: "login_locked",
          message: "로그인 시도가 너무 많습니다. 15분 후 다시 시도하세요.",
          retry_after_seconds: 900,
        },
      },
    }),
  );
  await page.goto("/");
  await passwordInput(page).fill(PASSWORD);
  await loginButton(page).click();

  await expect(page.getByRole("alert")).toHaveText("로그인 시도가 너무 많습니다. 15분 후 다시 시도하세요.");
  await expect(page.getByRole("heading", { name: "계좌 확인" })).toHaveCount(0);
});
