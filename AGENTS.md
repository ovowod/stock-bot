# AGENTS.md

Working rules for every AI coding agent on this project.

## Environment variables and secrets

- Do not try to read, modify, or delete `.env` at the project root. Agents have no access to it.
- Check `.env.example` for the names and format of required environment variables.
- When adding or changing features, do not introduce new variables that must be added to `.env`.
- Never hardcode secrets such as app keys, app secrets, access tokens, or account numbers in source code.
- Never expose secrets in responses, logs, error messages, test output, or client code.

## Kiwoom REST API

- When implementing or changing any Kiwoom REST API or WebSocket feature, always use the `kra-docs` skill to check the relevant TR spec.
- Even when only a feature name is given, find the relevant TR first and confirm the API ID, endpoint, real/paper trading domains, request fields, and response fields.
- Do not guess parameters, response fields, or behavior that are not in the docs. If the spec is unclear, do not implement; tell the user what could not be confirmed.

## Trading environments and order safety

- Keep the three environments distinct: real trading, domestic paper trading, and overseas paper trading.
- Use only the credentials and API/WebSocket domains of the selected environment. Never mix them across environments.
- If the environment is missing or invalid, stop. Never fall back to real trading.
- Never place real trading orders during development or testing. If a real order could possibly be sent, stop before running.
- Domestic and overseas paper trading orders are allowed for development and testing.
- Automated tests must not call Kiwoom's real servers. Use fake responses or a paper trading environment.

## Logging

- Log enough that, when something fails, the logs alone show which step ran, what was requested and processed, and where it failed.
- Logs must cover: external API requests and responses, WebSocket connect and disconnect, authentication, data processing, state changes, retries, and errors.
- Every log entry uses a consistent format with: timestamp, level, feature or module name, environment, API ID, request target, result, and error cause.
- Use a per-request ID so related requests, responses, and follow-up processing can be traced together.
- For errors, record more than the message: include error type, location, relevant state, and cause where possible.
- When logging requests and responses, remove or mask auth headers, secrets, and personal data.
- Control log level and scope for repetitive real-time data to limit volume, while keeping the flow needed for debugging.
- Prefer precise, consistent, machine-searchable formats over human-friendly prose.
- Log to stdout/stderr for live viewing via `docker logs`.
- Also write logs to files under `logs/` (mounted as a Docker volume) with date-based rotation and automatic cleanup, so history survives container restarts and redeploys.

## Verification after changes

- After changing code, run the relevant tests, lint, type checks, and build. Confirm each command actually exists in the project before running it.
- Where possible, also check the actual behavior affected by the change.
- Report failed checks with the item and cause, and skipped checks with the item and reason.
- Never claim success for anything you did not verify.

## Working style

- This is a personal project developed and maintained by a single user. Do not assume multiple developers.
- Keep work simple and direct, sized to the project. Do not add complex structure or process that the feature does not need.
- Do not overwrite or delete the user's work or changes unrelated to the current task.

## Communicating with the user

- Explain progress, changes, errors, verification results, and usage in plain Korean.
- Avoid unnecessary jargon. When a technical term is needed, explain what it means.
- The final report includes what changed, how it was verified, and any failed or skipped checks.
- If the user must do something themselves, give easy step-by-step instructions.

## Agent skills

### Issue tracker

Issues are tracked as local markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Uses the five default triage labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
