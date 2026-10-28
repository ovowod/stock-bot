import { expect, type Page } from "@playwright/test";

export const PASSWORD = "e2e-password";

/** 서버의 주문 허용 기본값. 실전은 꺼져 있다. */
const ORDERS_ALLOWED: Record<string, boolean> = {
  domestic_real: false,
  us_real: false,
  domestic_paper: true,
  us_paper: true,
};

const environmentReplies = new WeakMap<Page, Record<string, boolean> | "fail" | Promise<void>>();

/**
 * /api/environments의 응답을 바꾼다. 로그인하기(openDashboard·mockAuth) 전에 부른다.
 * "fail"이면 500이고, Promise면 그것이 끝날 때 기본값으로 답한다.
 */
export function setEnvironments(page: Page, reply: Partial<Record<string, boolean>> | "fail" | Promise<void>): void {
  environmentReplies.set(page, reply === "fail" || reply instanceof Promise ? reply : { ...ORDERS_ALLOWED, ...reply });
}

async function mockEnvironments(page: Page): Promise<void> {
  await page.route("**/api/environments", async (route) => {
    let reply = environmentReplies.get(page) ?? ORDERS_ALLOWED;
    if (reply instanceof Promise) {
      await reply;
      reply = ORDERS_ALLOWED;
    }
    if (reply === "fail") {
      return route.fulfill({ status: 500, json: { error: { kind: "unknown", message: "test" } } });
    }
    return route.fulfill({
      json: Object.entries(reply).map(([value, allowed]) => ({ value, orders_allowed: allowed })),
    });
  });
}

/**
 * /api/auth/*와 로그인 뒤 부르는 /api/environments를 가짜로 응답하게 한다. 로그인하기 전 세션 확인은 401, 맞는 비밀번호로 로그인한 뒤는 200이다.
 * 다른 /api 경로보다 나중에 등록해야 /api/**를 통째로 가로채는 가짜 응답보다 먼저 받는다.
 */
export async function mockAuth(page: Page): Promise<{ loginRequests: string[]; logoutRequests: () => number }> {
  const loginRequests: string[] = [];
  let logouts = 0;
  let loggedIn = false;
  await mockEnvironments(page);
  await page.route("**/api/auth/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/login") {
      const password = route.request().postDataJSON()?.password;
      loginRequests.push(password);
      if (password !== PASSWORD) {
        return route.fulfill({
          status: 401,
          json: { error: { kind: "invalid_password", message: "비밀번호가 올바르지 않습니다.", request_id: "req-auth" } },
        });
      }
      loggedIn = true;
      return route.fulfill({ status: 204 });
    }
    if (path === "/api/auth/session") {
      return loggedIn
        ? route.fulfill({ json: { authenticated: true } })
        : route.fulfill({ status: 401, json: { error: { kind: "unauthorized", message: "로그인이 필요합니다." } } });
    }
    if (path === "/api/auth/logout") {
      logouts += 1;
      loggedIn = false;
    }
    return route.fulfill({ status: 204 });
  });
  return { loginRequests, logoutRequests: () => logouts };
}

/** 대시보드를 열고 로그인 화면에서 로그인한다. 가짜 /api 응답은 이 함수를 부르기 전에 등록한다. */
export async function openDashboard(page: Page): ReturnType<typeof mockAuth> {
  const auth = await mockAuth(page);
  await loginThroughScreen(page, PASSWORD);
  return auth;
}

/** 실제 서버에 로그인한다. `PASSWORD` 환경변수로 비밀번호를 받는다(`pnpm test:live`). */
export async function openLiveDashboard(page: Page): Promise<void> {
  const password = process.env.PASSWORD;
  if (!password) throw new Error("PASSWORD 환경변수를 설정한 뒤 실행하세요.");
  await loginThroughScreen(page, password);
}

async function loginThroughScreen(page: Page, password: string): Promise<void> {
  await page.goto("/");
  await page.getByLabel("비밀번호").fill(password);
  await page.getByRole("button", { name: "로그인" }).click();
  // 로그인 직후에는 대시보드가 아직 그려지지 않았다. 다음 단계가 화면을 찾기 전에 기다린다.
  await expect(page.getByRole("radiogroup", { name: "투자 환경" }).first()).toBeVisible();
}

/** 계좌 확인이 함께 부르는 미체결 주문 요청에 빈 목록을 준다. 미체결 주문을 다루지 않는 테스트용이다. */
export async function mockNoOpenOrders(page: Page): Promise<void> {
  await page.route("**/api/environments/*/open-orders", (route) =>
    route.fulfill({ json: { orders: [], fetched_at: "2026-10-05T06:30:00+00:00" } }),
  );
}
