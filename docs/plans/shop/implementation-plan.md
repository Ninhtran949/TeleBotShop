# Implementation plan và task ledger

Status: **design baseline và T01/T02 hoàn tất; T03–T18 pending**. Đã có runtime/health và database foundation local; business flows chưa triển khai. Agent chỉ làm task/phase được giao, không tự coi roadmap là authorization. Owner giao T01 rồi T02 ngày 2026-10-21; không mở rộng authorization sang T03.

## Cách nhận và bàn giao task

1. Đọc AGENTS → PROJECT → đúng spec/ADR/convention section; xác nhận dependency thật sự hoàn tất bằng code/evidence.
2. Ghi task ID đang làm, phạm vi file dự kiến và acceptance sẽ chứng minh ngay trong mục task. Nếu một task cần nhiều behavioral slices, tách child IDs như `T10.a` trước khi code, giữ acceptance/verify riêng; không đóng task cha khi mới làm một child. Không sửa/xóa work đang dở của task khác.
3. Implement một vertical slice qua domain/use case/persistence/transport cần thiết; kiểm thử và review rồi mới chuyển slice. Không dựng toàn bộ schema/layer trước feature.
4. Chỉ tick done khi AC tương ứng đạt; ghi exact commands, exit/result, environment, review findings còn mở và external checks chưa chạy tại chính mục đó. Không cần status/ledger file song song.

Check scope: **H** = hai harness commands trong PROJECT. **C** = applicable lock/lint/format/unit/contract/architecture/build gates trong CONVENTIONS sau bootstrap. **D** = PostgreSQL integration + migrations với isolated test DB. **E** = actual Telegram test-environment evidence, không suy ra từ mock. **O** = operational/restore/release checks. Không gọi gate chưa được cấu hình là pass.

## Phases và dependency order

| Phase | Tasks | Exit gate |
| --- | --- | --- |
| 0 — Design | Tài liệu hiện tại | Spec/ADR/routing nhất quán; harness/doc checks, không có runtime claim. |
| 1 — Foundation/catalog | T01-T05 | C + D; identity/permission/catalog/inventory thật, không lộ secrets. |
| 2 — Commerce/delivery | T06-T10 | C + D; stock/payment/recovery races đạt và đủ năm product types. |
| 3 — Orders/admin/support | T11-T14 | C + D + E; core customer/admin flow, support/refund và audit đạt. |
| 4 — Growth | T15-T17 | C + D; coupon/referral/opt-in delivery, external messaging riêng khi được phép. |
| 5 — Release readiness | T18 | C + D + O và explicit production authorization; readiness không tự là deploy. |

## Phase 1

- [x] **T01 — Toolchain và package baseline.** Depends: design. Scope: `pyproject.toml`, `uv.lock`, Python/uv version pin, minimal `src/telebotshop` package, test/Ruff configuration và quality-check entrypoints. Acceptance: clean locked environment; wheel/import smoke; meaningful architecture-check self-cases; harness chạy riêng được. Verify: H + applicable C, record real package/runtime versions. Không tạo placeholder business flows hoặc dependencies cho feature chưa làm.

  Hoàn tất local ngày 2026-10-21 theo yêu cầu Owner. Ba phần đã kiểm chứng: (a) toolchain/locked install/build/import; (b) AST checker + positive/negative self-cases; (c) gates/review/tài liệu. Files: `.python-version`, `pyproject.toml`, `uv.lock`, `src/telebotshop/__init__.py`, `scripts/check_architecture.py`, `tests/architecture/test_import_boundaries.py`, `.gitignore`, `README.md`, PROJECT/CONVENTIONS/ARCHITECTURE và plan này. `scripts/verify_harness.py` chỉ được Ruff format để áp dụng gate toàn repo; AST trước/sau giống nhau. `task.md`, AGENTS, ADRs, `.agents/`, attributes và harness tests không đổi.

  Environment: Windows x86_64; app CPython **3.12.15** (build 2026-10-14), **uv 0.13.4** (7456b60bc, 2026-10-20), bundled **uv_build 0.13.4**, **Pytest 9.1.1**, **Ruff 0.17.1**. `uv.lock` có 8 packages kể cả project; không có runtime dependencies. Tool cài từ official PyPI wheels, managed Python/tool/cache nằm trong workspace được ignore; không sửa Python hệ thống 3.11.9. Git đã có, branch `dev`, sạch khi bắt đầu; chưa commit/push.

  Commands dưới dùng uv đã pin (trong phiên thực thi là `.tools/uv/Scripts/uv.exe`); bootstrap/biến cache ở [README](../../../README.md). Tất cả dòng PASS có exit **0**:

  | Command / acceptance | Kết quả thực tế |
  | --- | --- |
  | `uv lock --check` | PASS; manifest và lock nhất quán. |
  | `uv sync --locked --no-build` | PASS từ `.venv` chưa tồn tại; cài 8 packages, third-party wheels, build project bằng bundled backend. Lần kiểm tra cuối: checked 8 packages. |
  | `uv run --no-sync ruff check .` | PASS; rules E/F/I/B/UP, không tắt rule để pass. |
  | `uv run --no-sync ruff format --check .` | PASS. Chỉ format verifier cũ và test mới; không sửa upstream. |
  | `uv run --no-sync pytest tests/architecture` | **47 passed**; absolute/relative/package imports, aliases, wrong-layer/external SDK imports, nested/multiple imports, missing/invalid source và CLI exit 0/1 từ fixture cô lập. |
  | `uv run --no-sync python scripts/check_architecture.py` | PASS trên source hiện có; không import/execute app modules. |
  | `uv build --no-sources` | PASS; `dist/telebotshop-0.1.0.tar.gz` và `dist/telebotshop-0.1.0-py3-none-any.whl`, wheel được build từ sdist. |
  | `uv venv --python .venv/Scripts/python.exe .cache/wheel-smoke` | PASS; môi trường riêng, không có dev dependencies. |
  | `uv pip install --python .cache/wheel-smoke/Scripts/python.exe --no-index --no-deps dist/telebotshop-0.1.0-py3-none-any.whl` | PASS. Sau khi README hoàn tất, build lại và thêm `--reinstall` để kiểm tra đúng artifact cuối. |
  | Wheel/import smoke bằng `.cache/wheel-smoke/Scripts/python.exe -I` với assert trong README | PASS; package/version 0.1.0 được đọc từ isolated `site-packages`, không từ `src` hoặc PYTHONPATH. |
  | `python scripts/verify_harness.py` | PASS; six skills/integrity/reference paths/layout, Python hệ thống 3.11.9. |
  | `python -B -m unittest discover -s tests -p test_harness.py -v` | **6 tests, OK**, chạy độc lập app environment. |
  | `git diff --check` | PASS. |

  Test-first evidence: `uv run --no-sync pytest tests/architecture -q` trên checker stub trước implementation: **29 failed, 15 passed**, exit 1 đúng kỳ vọng. Sau implementation và thêm CLI/encoding cases: 47 pass. Mutation review đảo điều kiện cho phép external library: **11 failed, 36 passed**, exit 1; đã restore nguyên bytes checker và suite 47 pass lại. Self-review correctness/simplicity/architecture/security/performance không còn Critical/High/Required trong phạm vi T01; không có test/lint rule bị disable. Import checker là static guard, không phải chứng minh toàn bộ Clean Architecture hoặc business behavior.

  Artifact review: sdist/wheel không chứa `.tools`, `.cache`, `.venv`, `.env` hoặc `.agents`; wheel chỉ có package tối thiểu và metadata, không có `Requires-Dist`. uv cảnh báo cache trong source root khi build; kiểm tra archive xác nhận cache không được đóng gói. Local-link/code-fence audit cho năm Markdown files thay đổi: PASS. Không có DB/Telegram/CI/container/Linux validation, automated dependency vulnerability audit hoặc type-check; các gate đó chưa thuộc T01. Ranh giới tiếp theo là **T02, pending**, không tạo business layers trống hoặc runtime wiring sớm.

- [x] **T02 — Runtime wiring, settings và database readiness.** Depends: T01. Scope: bootstrap/entrypoints, FastAPI health, PostgreSQL/async session UoW, Alembic baseline, dev/test Compose settings. Acceptance: startup fail với invalid config; ready phản ánh DB/schema; isolated test DB/migration clean/upgrade; clients/session đóng khi shutdown. Verify: C + D; integration tests chứng minh UoW rollback/no hidden commit và import matrix. Cập nhật PROJECT bằng exact commands đã tồn tại.

  Hoàn tất local ngày 2026-10-21 theo yêu cầu Owner; T01 đã có trong Git (`ea4882f`), workspace sạch lúc bắt đầu. Không thực hiện T03, không gọi Telegram hoặc tạo business tables. Chỉ thêm runtime/dev dependencies thật sự cần cho HTTP/config/database/tests; không thêm bot client, worker rỗng hoặc production deployment.

  - [x] **T02.a — Validated settings và HTTP health contract.** Files: `infrastructure/config.py`, `api/health.py`, package initializers cần dùng, `tests/unit/`, `tests/contract/`, manifest/lock/Pytest discovery. AC: missing/invalid config fail-closed và không lộ DSN; live độc lập DB; ready map đúng 200/503 qua injected callable. Verify: trước implementation, unit/contract collection fail vì module chưa có; dependency compatibility check cũng phát hiện Starlette 1.9 yêu cầu httpx2. Đã xác minh nguồn và pin dev httpx2 2.15.1; không suppress warning. `uv run --no-sync pytest tests/unit tests/contract tests/architecture -q --tb=short`: **63 passed**, exit 0; scoped Ruff check PASS. Chưa đóng T02 cha.
  - [x] **T02.b — Alembic baseline và PostgreSQL test isolation.** Files: `alembic.ini`, `migrations/`, database schema contract, `docker-compose.yml`, test-init SQL, isolated fixtures/integration tests. AC: clean/upgrade migration thật trên PostgreSQL 18; explicit test DSN/APP_ENV/allowlist + database marker trước writes; schema riêng của từng test run, kiểm tra ownership trước cleanup. Verify: Compose config/up --wait PASS, PostgreSQL **18.6** thật (official image pin digest). `uv run --no-sync pytest tests/integration -q --tb=short`: **4 passed**; normal C suite trước đó 76 passed, Ruff PASS. Fixture đã bắt database marker mismatch trước writes; đối chiếu catalog thực và sửa sang `shobj_description` cho shared database object. Không SQLite/mock cho transaction hoặc migration.
  - [x] **T02.c — UoW, DB readiness và runtime lifecycle.** Files: application UoW port, concrete async UoW/readiness, `bootstrap.py`, `__main__.py`, entrypoint config và focused tests. AC: commit explicit, exception/uncommitted work rollback, independent concurrent sessions; ready fail khi DB/schema/timeout sai; shutdown đóng pool/sessions, import không load config/network. Verify: C suite **80 passed**; D suite sau thêm commit-failure/cancel/real-pool-lifespan cases **12 passed**. Entrypoint `uv run --no-sync telebotshop` trên loopback + disposable test DB: GET live/ready đều 200 sau explicit migration; Windows console interrupt ghi `Application shutdown complete` (PTY wrapper exit 1 do interrupt, không gọi exit 0). Không Telegram client hoặc business tables.
  - [x] **T02.d — Review, gates và handoff.** Files: `.env.example`, README, PROJECT/CONVENTIONS/ARCHITECTURE, `docs/DATABASE.md`, mục T02 này. AC: exact commands/results và dependency/container pins thực tế; applicable gates pass, unavailable/external gates ghi riêng. Self-review correctness/simplicity/import boundaries/security/performance hoàn tất, không còn Critical/High/Required trong T02. Không suppress test warnings, disable rules hoặc dùng mock/SQLite để thay PostgreSQL acceptance.

  Threat notes: config và DB failures có thể lộ secrets; health chỉ trả trạng thái cố định, settings/errors được redact. Database test có thể trỏ nhầm dữ liệu thật; không fallback sang DATABASE_URL, chỉ sửa schema do run sở hữu sau allowlist/marker checks. HTTP chỉ có health, local bind mặc định; không thêm auth/webhook/payment surface trong T02. Đã khởi động Docker Desktop background, xác minh Linux Engine 29.6.2/Compose 5.3.1 và PostgreSQL 18.6. `uv audit --locked` (uv 0.13.4, command experimental) báo không có known vulnerabilities/adverse statuses trong 34 third-party packages; không thay audit bằng forced upgrade.

  Final commands/evidence (uv executable của phiên này là `.tools/uv/Scripts/uv.exe`, cache/Python env theo README; quality commands PASS có exit 0):

  | Command / gate | Kết quả |
  | --- | --- |
  | `uv lock --check` | PASS; 35 packages trong lock kể cả project; SQLAlchemy giữ 2.0.55, không đổi sang latest 2.1. |
  | `uv sync --locked --no-build` | PASS; chỉ third-party wheels và bundled project backend. Final sync: checked 34 packages. |
  | `uv run --no-sync ruff check .` | PASS; không sửa rules E/F/I/B/UP. |
  | `uv run --no-sync ruff format --check .` | PASS. |
  | `uv run --no-sync pytest tests/unit tests/contract tests/architecture` | **87 passed**; include startup/config redaction, HTTP contract, import side effects/matrix, migration-head contract và DSN port/query regression cases. |
  | `uv run --no-sync python scripts/check_architecture.py` | PASS; API không kéo DB concrete vào, application port không kéo SDK/config vào. |
  | `uv run --no-sync pytest tests/integration` | **12 passed** trên PostgreSQL 18.6 thật: marker refusal, migrations clean/base/upgrade, readiness missing/wrong/locked/unreachable, UoW explicit commit/rollback/deferred failure/cancel, hai backend connections và real pool shutdown. |
  | `docker compose -p telebotshop-t02 --profile test config --quiet` / `up -d --wait postgres-test` | PASS với Docker Linux Engine; image 18.6-bookworm pin immutable manifest digest trong Compose. Không start dev service. |
  | `uv run --no-sync alembic heads`, `upgrade head`, `current` | PASS; head/current `0001_runtime`. CLI smoke target được kiểm tra database ownership marker trước migration; app startup không auto-migrate. |
  | `uv run --no-sync telebotshop` | TCP smoke `HTTP_HOST=127.0.0.1`, `HTTP_PORT=18872`, explicit disposable DB URL: live/ready đều 200 + status ok/no-store. Native Windows console Ctrl+Break: application shutdown complete, server process finished; interrupted PTY wrapper exit 1 như đã ghi ở T02.c. |
  | `uv build --no-sources` | PASS; sdist rồi wheel `telebotshop-0.1.0`. Archive inspection không chứa tools/cache/venv/env/skills. |
  | `uv export --locked --no-dev --no-emit-project --format requirements-txt --output-file .cache/runtime-requirements.txt` | PASS; generated ignored artifact, không thêm lockfile thứ hai. |
  | Runtime wheel smoke theo README | PASS; fresh `.cache/wheel-runtime-smoke`, 22 third-party runtime packages cài bằng `--require-hashes --no-build`, local wheel cài `--no-index --no-deps`; Python `-I` import bootstrap từ isolated site-packages, không có Pytest/dev dependencies. Missing metadata fetch được thực hiện với network permission; không nới hashes/build policy. |
  | `uv pip check --python .cache/wheel-runtime-smoke/Scripts/python.exe` | PASS; 23 installed packages compatible, kể cả project. |
  | `uv audit --locked` | PASS; 34 third-party packages không có known vulnerabilities/adverse statuses theo native OSV audit lúc kiểm tra. Command experimental; không phải source/container/release security audit. |
  | `python scripts/verify_harness.py` | PASS; upstream six skills/integrity/reference paths/layout. |
  | `python -B -m unittest discover -s tests -p test_harness.py -v` | **6 tests, OK**, Python hệ thống 3.11.9, độc lập app environment. |
  | `git diff --check` và document link/fence audit | PASS; 6 changed/new Markdown files, 34 local links hợp lệ. |

  Mutation review: đảo readiness comparison làm **2 failed, 1 passed**; thêm hidden commit làm UoW regression test **1 failed**. Đã restore nguyên bytes source và chạy lại C/D pass. Final review tái hiện unsupported DSN option thoát khỏi probe bằng TypeError; trước sửa settings, regression suite **6 failed, 16 passed**. Đã chặn port ngoài phạm vi, unknown/duplicate/invalid query options tại startup và giữ supported `ssl` mode; final **C 87/D 12 pass**, rebuild/reinstall wheel và isolated import/pip check PASS. Kiểm tra sau tests: không còn schema `tbs_test_*`, HTTP smoke port đã đóng. Đã xác minh labels/project/workspace trước `docker compose -p telebotshop-t02 --profile test stop postgres-test`; test service đã dừng, không xóa dev data/volume hoặc dừng Docker Desktop toàn máy.

  Giữ nguyên AGENTS, `task.md`, ADRs, `.agents/`, attributes, Python pin, verifier/harness tests. Source changes gồm config/API/bootstrap/UoW/readiness/schema contracts và package initializers cần dùng; fixtures/tests, Alembic/Compose/init SQL, manifest/lock và docs đi cùng. Không có Git commit/push, hosted CI, Python runtime Linux/macOS, Telegram test/live, production image/deployment hoặc backup/restore acceptance. Ranh giới tiếp theo là **T03, pending**, chưa được thực hiện.

- [ ] **T03 — Identity, permissions và audit foundation.** Depends: T02. Spec: IA-01..05, AO-01. Scope: user/role/permission/audit schema + use cases, `/start`/Owner bootstrap, Owner grant/revoke UI và safe settings/admin menu foundation. Acceptance: idempotent profile; current permission/default deny kể cả direct use-case call; Owner mismatch không tự cấp quyền; transactional audit cho protected mutations. Verify: C + D, concurrent identity/permission and audit rollback tests.

- [ ] **T04 — Catalog và admin product maintenance.** Depends: T03. Spec: CI-01,04; AO-01,06. Scope: categories/products + validated five-type metadata, browse/search/pagination, staff product/price/sale-state mutations. Acceptance: dữ liệu DB thật; safe Việt navigation; owner-authorized writes có audit, read models không có secret. Verify: C + D, permission/tampered callback/pagination/price-change tests. Seed chỉ là demo/test inventory hợp pháp giả, không thay nguồn dữ liệu runtime.

- [ ] **T05 — Encrypted inventory và atomic import.** Depends: T04. Spec: CI-03, CI-04, CI-06; ADR-0003. Scope: unit storage/crypto/HMAC, CSV/TXT upload import, retire permissions và low-stock query/signal. Acceptance: duplicate/invalid/oversize batch rollback; encryption fail-closed; two-connection import không trùng; secret không lọt logs/errors. Verify: C + D + focused security review. Reservation/order FKs và last-key checkout race thêm ở T06/T08, không tạo fake orders/schema sớm để test T05.

## Phase 2

- [ ] **T06 — Create order, reserve và expiry.** Depends: T05. Spec: OP-01, OP-02, OP-07; CI-02, CI-05. Scope: order/item snapshots và reservation FK migration, action idempotency, finite-stock reservation, invoice intent/Stars adapter, guarded expiry/cancel. Acceptance: quantity-one checkout/price snapshot; no network under DB lock; double-click/last-key/expiry races giữ stock đúng. Verify: C + D, mocked invoice outcomes gồm uncertain và price change, hai independent DB connections.

- [ ] **T07 — Pre-checkout boundary.** Depends: T06. Spec: OP-02,07. Scope: Telegram pre-checkout parsing/response + application validation/serialized approval. Acceptance: owner/currency/amount/payload/status/stock checks; duplicate query idempotent, query khác không double-approve; bounded DB/API deadline đáp ứng giới hạn Telegram. Verify: C + D và controlled timeout/contention tests; approval không được mark PAID.

- [ ] **T08 — Durable payment receipt và fulfillment intent.** Depends: T07. Spec: OP-03..05,07. Scope: payments/fulfillments contract, successful-payment handler và atomic UoW. Acceptance: duplicate/conflicting/extra/late/unknown receipts được giữ đúng; matched payment + allocation + work atomic; webhook không ack trước durable outcome. Verify: C + D, concurrent duplicates, injected commit failure và payment-vs-expiry race; no delivery call trong handler.

- [ ] **T09 — Key/license worker và recovery.** Depends: T08. Spec: FD-01..03,06; CI-05. Scope: work claim/lease/CAS, delivery attempts, unique-key/license sending, retry/uncertain/alerts. Acceptance: một active sender, stale token bị chặn, crash sau send không cấp key mới và không auto-send uncertain. Verify: C + D, two-worker races và fault injection trước/sau từng durable/send boundary; focused security review.

- [ ] **T10 — File, reusable và manual fulfillment.** Depends: T09. Spec: CI-04, FD-04,06. Scope: từng typed dispatch adapter và manual work completion có permission/audit. Acceptance: mỗi loại giao đúng payload cho đúng buyer; unsupported/corrupt payload fail-closed; MANUAL không giả delivered khi payment tới. Verify: C + D + mocked message/document contracts; tách T10.a/b/c nếu cần, child nào xong không thay thế coverage cả ba loại.

## Phase 3

- [ ] **T11 — Order history và resend an toàn.** Depends: T10. Spec: FD-05, IA-02,05; AO-02,06. Scope: customer/staff order views và authorized entitlement reader/resend actions. Acceptance: buyer/staff scopes đúng, customer không mở order của người khác, resend cùng entitlement và không mở lại refunded order; staff dashboard/order counts dùng DB thật. Verify: C + D, IDOR/callback replay/resend state tests.

- [ ] **T12 — Support và payment support.** Depends: T11. Spec: AO-02,03. Scope: tickets, staff assignment/scoped replies, `/paysupport`, terms/support navigation. Acceptance: đúng owner/ticket permission; lỗi paid/uncertain có support path; real terms/support identity là Owner input, tests dùng nội dung giả rõ ràng. Verify: C + D, cross-user/ticket isolation và sanitized output contracts.

- [ ] **T13 — Refund và privileged operations.** Depends: T12. Spec: OP-06, AO-01,06; FD-06. Scope: persisted refund intents, action confirmation/dedup, grant-aware admin refund UI và uncertain reconciliation path. Acceptance: only authorized full refund; no refund-vs-send race, no automatic uncertain retry/restock; audit/business state atomic. Verify: C + D và focused security review với refund timeout/replay/worker concurrency. Actual real-account refund không được thực hiện từ việc task code được giao.

- [ ] **T14 — Core integration và Telegram test acceptance.** Depends: T13. Scope: complete customer/admin navigation/contracts, operational alerts, BotFather/test-webhook setup instructions và end-to-end evidence. Acceptance: đủ năm types và customer/admin menus thuộc core hoạt động trên DB thật; test Stars checkout/delivery/refund được quan sát; không Critical/High còn mở trong core. Verify: C + D + E theo matrix dưới; thiếu external credentials/setup thì ghi gate chưa verify và không tick task done. Không cần credentials để hoàn tất các automated tests độc lập.

## Phase 4

- [ ] **T15 — Coupons.** Depends: T14. Spec: GR-01,02. Scope: coupons/redemptions + admin/customer flows và checkout integration. Acceptance: đúng scope/expiry/caps, integer price snapshot và transactional redemption; last-use race/duplicate/late payment không over-redeem. Verify: C + D, concurrency tests. Không đổi snapshot của invoice đã phát.

- [ ] **T16 — Referral.** Depends: T15. Spec: GR-03. Scope: referral deep links/attribution/stats. Acceptance: new-user attribution immutable, không self-referral, paid/refunded reporting đúng; không commission payout. Verify: C + D, concurrent/replayed attribution và refund-adjusted stats.

- [ ] **T17 — Opt-in marketing, channel và reports.** Depends: T16. Spec: GR-04,05; AO-06. Scope: subscription/notification intents, restock alerts/channel posts, bounded reports và admin controls. Acceptance: consent/opt-out/dedup/rate limits, valid product links, không auto paid broadcast; reports từ DB thật. Verify: C + D + mocked external contracts; actual channel messages chỉ khi explicit authorization và ghi external evidence riêng.

## Phase 5

- [ ] **T18 — Operational/release readiness.** Depends: T17. Spec: AO-04,05 và mọi core AC. Scope: Docker production image/config, migration/rollout runbook, monitoring, graceful shutdown, backup/key restore rehearsal, dependency/security review và retained evidence. Acceptance: clean build/start/readiness; test restore dùng DB + key versions; all required AC đạt hoặc rõ blocker, Owner policies và production config được chuẩn bị. Verify: H + C + D + O và E evidence phù hợp phiên bản release. Production deployment, live payments, credential/account changes cần authorization riêng; không tick deploy chỉ vì local tests xanh.

## Nghiệm thu bắt buộc từ task.md §11

| Case | AC / task sở hữu |
| --- | --- |
| Mua còn hàng / hết hàng | CI-01,02; OP-01,02; T05-T08 |
| Hai khách mua key cuối | CI-02; OP-03; T06,T08 |
| Payment callback lặp | OP-03,04; T08 |
| Paid rồi gửi hàng lỗi | FD-02,03; OP-07; T09 |
| Restart giữa fulfillment | FD-01,02; T09 |
| Không đủ điều kiện pre-checkout | OP-02; T07 |
| Giá đổi khi order chờ | OP-02; T06,T07 |
| Xem đơn người khác | IA-05, FD-05; T11 |
| User thường gọi admin | IA-02,03, AO-01; T03,T13 |
| Admin refund | OP-06, AO-01; T13,T14 |
| Coupon invalid/expired/over-limit | GR-01,02; T15 |
| Import lỗi/trùng key | CI-03,06; T05 |
| Bổ sung failure cases cho thiết kế | Late/extra/conflicting payment, expiry-vs-pay, stale lease, ambiguous send/refund, restore keys: OP-03..06, FD-01..06, AO-05 |

## Deliverables và canonical paths

`task.md` dùng một số tên tài liệu ở root; baseline này đặt tài liệu chi tiết dưới `docs/` để không có hai bản chuẩn. Không tạo redirect/copy cùng nội dung chỉ để có cả hai paths.

| Deliverable | Canonical path / task |
| --- | --- |
| App source, tests, fake seeds | `src/telebotshop/`, `tests/`, seed entrypoint thuộc infrastructure khi T04/T05 cần |
| Hướng dẫn chạy | `README.md`, thêm khi bootstrap; PROJECT ghi trạng thái và actual commands |
| Architecture | `docs/ARCHITECTURE.md` đã thiết kế; cập nhật theo implementation evidence |
| Database/schema/migrations | `docs/DATABASE.md`, `migrations/`; cập nhật T02 và từng schema slice |
| Security | `docs/SECURITY.md`; controls thực tế từ T03/T05, review hoàn thiện T18 |
| BotFather/test environment | `docs/BOTFATHER-SETUP.md`; T14, không chứa token thật |
| Deployment/backup/restore | `docs/DEPLOYMENT.md`; T18, explicit production gate |
| Environment/build/runtime | `.env.example`, `Dockerfile`, `docker-compose.yml`, lock; T01/T02 và hoàn thiện T18 |
| Specs/plan/ADRs | Các tài liệu hiện tại; không tạo root PLAN.md/SPEC.md trùng lặp |

## Bằng chứng của bước thiết kế

Ngày 2026-10-21, chỉ verification cho tài liệu/harness:

- `python scripts/verify_harness.py`: PASS, six skills/integrity/reference paths/layout.
- `python -B -m unittest discover -s tests -p test_harness.py -v`: 6 tests, OK. Không thay test hoặc verifier để đạt kết quả này.
- Standard-library document audit: 18 authored Markdown files, 58 local links hợp lệ, code fences đóng đủ; 6 capabilities không có dependency cycle; 35 unique AC IDs, không có reference AC không tồn tại; 18 application tasks vẫn pending. Task-dependency/preservation/format checks được rà soát lại sau self-review.
- Baseline hash comparison: chỉ `AGENTS.md` và docs được thêm/sửa; `task.md`, toàn bộ `.agents/`, verifier/tests và ignore/attributes giữ nguyên. Không tạo source, manifest, Git metadata, credential hoặc app configuration.
- Instruction self-review: trivial docs trực tiếp; code dùng đúng capability spec; payment/stock/security thêm tests/review; upstream auto-commit/router/templates không override project authorization/path rules. Extra/late charge, refund primary-vs-extra, uncertain delivery, staged schema và Owner-only operational settings được rà lại trong thiết kế.
- Official sources được đối chiếu: Telegram Stars/Bot API, PostgreSQL locks, SQLAlchemy 2.0 async, uv locking/options, cryptography AEAD; link đặt tại spec/ADR tương ứng. Đây là design research, không phải external smoke.
- `git status --short --branch` vẫn báo workspace không phải Git repository. Không commit/push/deploy, không runtime/DB/live check. App gates và compatibility evidence vẫn là việc của T01 trở đi; những điều Owner cần chốt trước mở bán nằm trong PROJECT.
