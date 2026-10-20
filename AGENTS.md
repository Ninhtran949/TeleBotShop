# TeleBotShop engineering rules

## Start and scope

- Think and plan before acting. Inspect relevant files and Git status; preserve existing source, configuration, ADRs, and uncommitted changes. Report when Git is unavailable.
- Read [PROJECT](docs/PROJECT.md) for current state and task routing. Then read only the relevant sections of [ARCHITECTURE](docs/ARCHITECTURE.md), [CONVENTIONS](docs/CONVENTIONS.md), and the [shop specs/plan](docs/plans/shop/README.md). Do not preload every document or skill.
- The current user request authorizes work. `task.md` owns the original product requirements; accepted ADRs own technical decisions; capability specs own detailed contracts; the plan owns task status. Surface conflicts instead of silently changing scope. A roadmap or unchecked task is not permission to execute it.
- The project has a selected design baseline and harness, but no application implementation yet. A chosen design, passing harness check, and working application are different claims.

## Smallest complete change

- Reuse existing code, platform features, and installed dependencies. Make the smallest complete change; no unrelated refactors, speculative layers, duplicated rules, unused scaffolding, or features outside the assigned scope.
- Work one verifiable slice at a time. Update callers, fixtures, contracts, migrations, and relevant docs together. File-size thresholds are review signals, not automatic refactoring work.
- Technical choices follow the accepted ADRs. Material changes require a new/superseding ADR; business scope, retention/refund policy, or integration changes outside existing authorization require an Owner decision.

## Architecture and critical invariants

- Use `src/telebotshop/` as a modular monolith. Domain imports only the standard library; application imports domain and its own ports; adapters import inward. Only the composition root wires concrete implementations. Follow the import matrix in ARCHITECTURE.
- Telegram/FastAPI/admin handlers validate transport input and invoke use cases. Authorization, ownership, price, payment validation, and transaction decisions belong in application/domain code, including calls that bypass handlers.
- One use case owns its transaction. Repositories never commit; no Telegram/network calls inside database transactions; no shared AsyncSession across concurrent tasks. Return explicit results, not live ORM objects.
- Stars amounts are integers. Snapshot order prices; deduplicate payments by charge ID; protect stock with PostgreSQL locks and constraints. When fulfillment is possible, persist payment, allocation, and durable work atomically; preserve late/unmatched receipts for reconciliation.
- A paid order never silently becomes unpaid or releases its key. Ambiguous Telegram sends/refunds require reconciliation, not blind retries. Reuse the existing entitlement when resending; never allocate another key to repair a delivery error.

## Conditional skills

Use native `.agents/skills/` discovery; load a skill body/references only for the current task.

| Task | Activate proportionally |
| --- | --- |
| Trivial fix, documentation, static configuration | Direct change and focused checks; no mandatory new spec, plan, or TDD ceremony. Cover changed behavior when applicable. |
| Feature or substantive behavior | Reuse the relevant capability spec; `spec-driven-development` if requirements are missing, `planning-and-task-breakdown` if sequencing is needed, `incremental-implementation` for multiple slices, `test-driven-development` for logic. |
| Payments, stock concurrency, authorization, sensitive data, migrations, external effects | Add threat/abuse considerations, focused failure/race tests, and `security-and-hardening`. |
| Requested review, completed substantive code, pre-merge assessment | `code-review-and-quality`; requested reviews remain report-only unless fixes are authorized. |

Repository paths, scope, and authorization override upstream workflow defaults. Specs and task checklists live under `docs/plans/`, not duplicate root specs or `tasks/` files. Reuse explicit authorization; do not impose repeated approvals for the same work. A request to author design/spec/plan documents authorizes those artifacts, not product implementation. Other upstream skill mentions are optional guidance, not installed dependencies. Keep the six original skills, shared references, license, and `.agents/upstream.json` unchanged unless an upstream update is requested. Upstream `skills/<name>/SKILL.md` denotes the installed counterpart; generic examples do not select this project's stack.

## Checks and completion

- Use the actual commands in PROJECT; application gates in CONVENTIONS become executable during bootstrap. For new logic/bug fixes, write a focused failing test first where practical, then implement and verify. PostgreSQL concurrency/integrity tests must use PostgreSQL, not SQLite or mocks.
- Never invent test results, weaken tests, disable lint rules to obtain a pass, or call local/mocked checks live validation. Run applicable gates, inspect the final change, and label unavailable checks precisely.
- Mark a plan task done only with its acceptance evidence. Update current status, report changed files, exact commands/results, risks/blockers, and the next authorized boundary. Stop when the assigned scope is complete.

## Safety

- Validate untrusted data at boundaries. Telegram IDs and server-side checks establish permissions; callback fields and usernames do not. Default-deny access and redact secret/PII payloads from logs and errors.
- Never expose or commit tokens, `.env` contents, encryption keys, real inventory, customer data, or delivery payloads. Use fake fixtures and environment-based secrets; do not execute unreviewed third-party scripts.
- Do not discard others' work, rewrite history, or run destructive operations against unverified targets. Tests use explicitly isolated databases; production data, live payments/refunds/messages, deployments, commits, and pushes require explicit authorization. Existing authorization remains valid within its scope.
