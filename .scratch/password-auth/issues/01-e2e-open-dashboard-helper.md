# 01: e2e에서 대시보드를 여는 공용 함수

**What to build:** e2e 테스트가 대시보드를 여는 방법을 공용 함수 `openDashboard(page)` 하나로 모은다. 지금은 각 테스트가 `page.goto("/")`를 직접 부른다. 이 작업에서는 동작이 바뀌지 않고, 이후 로그인 단계를 이 함수 한 곳에만 넣을 수 있게 된다.

Spec: `.scratch/password-auth/spec.md` (Testing Decisions — 화면)

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [x] 대시보드를 처음 여는 모든 e2e 테스트가 `openDashboard(page)`를 거친다. `page.goto("/")`를 직접 부르는 곳이 남지 않는다(`@live` 포함).
- [x] 함수는 페이지를 열기만 한다. 가짜 `/api` 응답을 등록하는 순서는 기존 테스트와 같다(함수를 부르기 전에 등록).
- [x] `page.reload()`를 쓰는 테스트는 그대로 둔다.
- [x] `pnpm --dir web typecheck`, `pnpm --dir web test:e2e`가 기존과 같은 개수로 통과한다.
