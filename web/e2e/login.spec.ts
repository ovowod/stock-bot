import { expect, test, type Page } from "@playwright/test";
import { mockAuth, openDashboard, PASSWORD } from "./support";

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
