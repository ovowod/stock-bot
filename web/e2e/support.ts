import type { Page } from "@playwright/test";

// 대시보드를 연다. 가짜 /api 응답은 이 함수를 부르기 전에 등록한다.
export async function openDashboard(page: Page): Promise<void> {
  await page.goto("/");
}
