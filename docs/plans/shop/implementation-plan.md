# Implementation plan và task ledger

Status: **T01–T10 hoàn tất; T11–T18 pending**. Owner giao T10 tường minh; hoàn tất local 2026-10-22. HEAD `6582dd0` (T09), index trống; agent không stage/commit/push hoặc tự mở rộng T11.

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

- [x] **T03 — Identity, permissions và audit foundation.** Depends: T02. Spec: IA-01..05, AO-01. Scope: user/role/permission/audit schema + use cases, `/start`/Owner bootstrap, Owner grant/revoke UI và safe settings/admin menu foundation. Acceptance: idempotent profile; current permission/default deny kể cả direct use-case call; Owner mismatch không tự cấp quyền; transactional audit cho protected mutations. Verify: C + D, concurrent identity/permission and audit rollback tests.

  - [x] **T03.a — Identity và Owner bootstrap.** Domain DTO/validation, application ports/use cases, PostgreSQL repositories/UoW, additive Alembic revision, aiogram `/start` và private profile/Home navigation. Chứng minh concurrent upsert giữ UUID/registration, không tăng quyền; bootstrap serialize qua persisted singleton, mismatch/missing persisted Owner role fail-closed, không tự restore quyền. Thêm SDK đúng aiogram 3.x trong manifest/lock, không đổi stack ADR.
  - [x] **T03.b — Permissions, audit và confirmation.** Roles là capability ceilings, từng staff chỉ có explicit grants; Owner-only quản lý quyền/settings. Direct calls đọc quyền hiện tại trong transaction; SUPPORT phải có ticket assignment từ dữ liệu server. PostgreSQL confirmation token ràng buộc actor/target/version/expiry, dedup và audit append-only cùng transaction. Tests grant/revoke/race/stale/replay, audit/commit failure rollback, constraints và customer/staff scope. Chưa tạo orders/tickets/refunds để giả nghiệm thu các task sau.
  - [x] **T03.c — Admin/settings UI và runtime wiring.** `/admin`, Owner grant/revoke confirmation, non-secret support username setting (mặc định chưa cấu hình), Back/Home/Cancel, guards cho private chat/callback/actor và bounded single-process rate limit. Settings không chứa secret hoặc owner transfer. Development polling là entrypoint riêng; không delete webhook/drop pending updates, không chạy bot thật; HTTP health giữ độc lập. Adapter contracts mock Telegram, integration dùng PostgreSQL thật.
  - [x] **T03.d — Review, gates và bàn giao.** C + D + H, migration từ T02/head và metadata drift, installed-wheel/import/dependency checks, diff/security self-review; cập nhật PROJECT/ARCHITECTURE/DATABASE/SECURITY/README/env example và evidence ngay tại đây. Chỉ đóng T03 khi đủ các child; T04 chưa được thực hiện.

  Threat/failure focus: forged username/actor/callback, group/inaccessible/inline message, cached roles sau revoke, concurrent bootstrap/grant/revoke và stale confirmation, audit tampering/failure, secret/profile leakage trong SDK/config/DB errors. Telegram sends luôn sau khi đóng UoW; database exceptions chỉ hiện thông báo vận hành chung. Owner transfer/live setup/production authorization và customer order/ticket integration thuộc task riêng.


  **Acceptance/evidence T03 (2026-10-21, Windows x86_64, CPython 3.12.15, PostgreSQL 18.6):**

  T03.a: concurrent upsert giữ UUID/registration và update profile không tăng quyền; cùng/different Owner IDs serialize vào một Owner/audit. Restart/Owner mismatch hoặc missing persisted role fail-closed, không regrant. Private profile use case xác minh ownership cả khi repository trả sai profile. Bootstrap không tự bịa username/display name của Owner chưa `/start`.

  T03.b: ADMIN/SUPPORT role là ceiling, authority cần explicit grants; Owner access/settings permissions không delegate. SUPPORT cần server-loaded assignment. Confirm ràng buộc actor/target/version/expiry; same-token race/replay chỉ một mutation/audit, competing grants yêu cầu confirmation mới; replay grant sau revoke không phục hồi quyền. FK/unique constraints chặn second Owner/unknown/duplicate grants. Audit BEFORE INSERT failure và deferred commit failure đều rollback role/grant/version/action; append-only trigger chặn UPDATE/DELETE/TRUNCATE. Read permission SHARE lock giữ tới hết use-case transaction, concurrent revoke timeout rồi thành công sau khi release.

  T03.c: `/start`, profile, Home/Back/Cancel; `/admin`, Owner `/grant`/`/revoke` và `/setsupport` tạo confirmation, không sửa ngay. Staff menu chỉ hiển thị grants hiện tại; không có mock dashboard/catalog/refund controls. Guard chặn group/bot/mismatched private ID/inline/inaccessible callbacks; unknown/tampered callback không mutate. Single-ingress rate cap và secret/PII redaction có tests. Dispatcher → use cases → PostgreSQL thật → Telegram mock được kiểm tra không còn checked-out connection khi send/edit/ack. Polling lifecycle mock xác minh existing webhook refusal (không delete/drop), Owner bootstrap, sequential updates và close client/pool cả khi API failure/cancellation. HTTP config vẫn độc lập BOT_TOKEN/OWNER_ID.

  Files: thêm `domain/errors.py`, `domain/models/{identity,access,admin}.py`, application identity/access ports/services và UoW factory contract; SQLAlchemy Core models/repositories, `0002_identity.py`/`0003_access.py`, metadata/head; customer/admin routers, ingress/logging controls và composition/config/CLI. Cập nhật manifest/lock, isolated fixtures và unit/contract/PostgreSQL tests. Docs: README, PROJECT, ARCHITECTURE, CONVENTIONS, DATABASE, SECURITY, identity/admin specs, ledger và `.env.example` (bot fields trống). Giữ nguyên `0001_runtime`, AGENTS, `task.md`, accepted ADRs, `.agents/`, attributes, Python/uv pins, architecture checker và harness.

  Dependency compatibility: official aiogram 3.31.0 metadata yêu cầu Pydantic `<2.14`; pin Pydantic **2.13.5** thay T02 2.14.1, giữ Settings 2.16.1 và các direct runtime pins khác. `uv.lock` có 47 packages gồm project; wheel runtime có 34 third-party packages. Sources/constraint links trong README; không đổi stack/ADR hoặc chạy third-party source build.

  `uv` dưới đây là workspace-local `.tools/uv/Scripts/uv.exe` 0.13.4; cache/interpreter environment theo README. Tất cả kết quả cuối exit 0:

  | Command/check | Result |
  | --- | --- |
  | `uv lock --check`; `uv sync --locked --no-build` | PASS; 47 resolved, 46 installed dependencies checked. |
  | `uv run --no-sync ruff check .`; `uv run --no-sync ruff format --check .` | PASS; 95 owned Python files formatted, không sửa upstream skills hoặc nới rules. |
  | `uv run --no-sync pytest tests/unit tests/contract tests/architecture -q --tb=short` | **164 passed**, gồm T02 regression contracts dưới Pydantic 2.13.5. |
  | `uv run --no-sync pytest tests/integration -q --tb=short` | **40 passed** trên PostgreSQL 18.6 thật; explicit APP_ENV/test DSN/DB marker và per-test owned schema. |
  | `uv run --no-sync python scripts/check_architecture.py` | PASS; import matrix/domain/application/adapters. |
  | Migration tests trong D | PASS; clean/base/T02→identity→access upgrade, preserve profile/Owner/registration, metadata/defaults diff rỗng, seed role-permission matrix khớp domain, exact head `0003_access`. |
  | `uv build --no-sources` | PASS; sdist→wheel 0.1.0. Inspect hai archives: đúng 38 package Python sources, khớp bytes working tree, không tools/cache/venv/env/skills. |
  | `uv export --locked --no-dev --no-emit-project --format requirements-txt --output-file .cache/t03/runtime-requirements.txt` | PASS; artifact ignored, vẫn chỉ một lockfile. |
  | `uv venv --python .venv/Scripts/python.exe .cache/t03-wheel-runtime`; runtime install theo README | PASS; 34 deps cài `--require-hashes --no-build`; wheel cài `--no-index --no-deps`; không dev dependencies. |
  | `.cache/t03-wheel-runtime/Scripts/python.exe -I` import smoke | PASS; all package modules import từ isolated wheel, poisoned settings không được load và socket-connect stub không bị gọi; không Pytest. |
  | `uv pip check --python .cache/t03-wheel-runtime/Scripts/python.exe` | PASS; 35 packages kể cả project compatible. |
  | `uv audit --locked` | PASS; 46 third-party packages không có known vulnerabilities/adverse statuses theo OSV tại lúc chạy. Native command experimental; chưa là source/container/production security audit. |
  | `python scripts/verify_harness.py` | PASS; six upstream skills/hash/reference paths/layout giữ nguyên. |
  | `python -B -m unittest discover -s tests -p test_harness.py -v` | **6 tests, OK**, system Python 3.11.9 độc lập app environment. |
  | `git diff --check`; document/status audit | PASS; 9 changed/new Markdown files, 45 local links hợp lệ, fences đóng đủ; chỉ T01/T02/T03 và bốn T03 child được tick done. |

  Review/evidence quality: tạm bỏ current permission check làm regression **1 failed**; tạm bỏ audit làm failure/rollback tests **2 failed**. Đã khôi phục nguyên bytes source, rồi chạy final C/D pass như trên. Profile malformed-type regression trước fix **3 failed, 12 passed**; wrong-profile ownership regression trước fix **1 failed, 15 passed**; sau fix final C164/D40. Focused self-review đi qua handler→use case→repo/locks/audit/commit→send, không còn Critical/High được phát hiện trong scope. Một lần aggregate D lỗi 14 fixture connections vì container đã dừng, đã xác minh/start lại đúng service và chạy final D thật; không đổi/skip tests để che lỗi môi trường.

  Cleanup: xác minh container labels project `telebotshop-t02`, service `postgres-test`, absolute workspace; query thấy **0 `tbs_test_*` schemas và 0 other test connections**, rồi `docker compose -p telebotshop-t02 --profile test stop postgres-test` thành công. Không drop DB/dev volume hoặc dừng Docker Desktop toàn máy.

  Giới hạn: rate counters là single-process, access/settings lock bảo thủ; expired confirmations/audit chưa auto-delete khi chưa có Owner retention policy; production role separation/restore chưa kiểm chứng. Telegram API/polling lifecycle dùng mock, không BotFather/test/live messages, webhook changes, native polling signal smoke, hosted CI, Linux/macOS runtime, production image/deploy hoặc real payments/refunds. IA-05/AO-01 ở đây là identity/permission/settings foundation; actual order/ticket/refund/catalog flows còn theo task sở hữu, không claim toàn capability. Không commit/push. **T03 hoàn tất, dừng tại ranh giới T04 pending.**

- [x] **T04 — Catalog và admin product maintenance.** Depends: T03. Spec: CI-01,04; AO-01,06. Scope: categories/products + validated five-type metadata, browse/search/pagination, staff product/price/sale-state mutations. Acceptance: dữ liệu DB thật; safe Việt navigation; owner-authorized writes có audit, read models không có secret. Verify: C + D, permission/tampered callback/pagination/price-change tests. Seed chỉ là demo/test inventory hợp pháp giả, không thay nguồn dữ liệu runtime.

  - [x] **T04.a — Category/product contracts và protected persistence.** Thêm domain catalog/typed public metadata, application port/use cases, SQLAlchemy tables/repository/UoW và migration mới. Category code/SKU immutable unique; root/subcategory hai cấp. Cả năm type được validate riêng: key không nhận payload; license có public instructions; file có filename/MIME; reusable có TEXT/DOCUMENT format; manual có customer notice. Delivery key/file_id/content không được nhận trong metadata public; encryption/payload thuộc T05/T10. TDD validation/wrong-type, real PostgreSQL constraints, category/product creation, current permission và audit rollback.
  - [x] **T04.b — Admin maintenance có confirmation.** Người có `catalog.manage` tạo category, tạo/sửa sản phẩm, giá integer Stars, hidden/active và ảnh công khai bằng Telegram photo file_id. Reuse current-permission UoW lock, audit table và ActionStatus; catalog sở hữu pending-action table riêng để không nới contract Owner grants của T03. Actor/target/version/expiry binding, cancel/dedup; test concurrent price edits, revoked actor, stale/tampered/replayed actions và audit/commit failure. SKU/type không đổi; không hard-delete.
  - [x] **T04.c — Customer catalog/search/navigation và wiring.** PostgreSQL-backed category/subcategory/product pages, bounded search và pagination, chi tiết/ảnh/giá/metadata công khai; chỉ thấy active products. Menu `/start` thêm Cửa hàng/Tìm kiếm, `/admin` thêm Products theo current permission. UI context chỉ giữ navigation, bounded/TTL/actor-bound trong router, không cache catalog/quyền/stock; restart/expiry hướng dẫn mở lại. Không số tồn kho giả hoặc Buy button trước T05/T06; chưa có delivery payload thì không claim purchasable. Contract mocks Telegram và integration end-to-end PostgreSQL, đảm bảo send ngoài transaction.
  - [x] **T04.d — Review/gates/docs.** C + D + H; migration từ T03, metadata drift, meaningful fault/race and secret-safe tests; build/wheel/import, final diff, cập nhật PROJECT/DATABASE/SECURITY/README/spec/ledger. Chỉ đóng T04 khi đủ children; dừng trước T05.

  Threat/failure focus: forged actor/role/product/price callbacks, cached permission sau revoke, hidden products qua direct lookup/search, negative/bool/float price, malformed/wrong-type/secret-bearing metadata, arbitrary image URL/file paths, SQL search wildcard/injection, pagination bounds/expired cross-user navigation, concurrent SKU/price edits và rollback audit/state/action. Dữ liệu demo/test là giả và không tự seed runtime; không bot thật, checkout, inventory hoặc production changes.

  Hoàn tất local ngày **2026-10-21**. Files: domain `models/catalog.py`, `models/catalog_action.py`, errors; application `ports/catalog.py`, `ports/uow.py`, `services/catalog.py`; infrastructure catalog models/repository, UoW/schema; migration `0004_catalog.py` và Alembic metadata wiring. Transport: `admin/catalog.py`, customer catalog router/keyboards, identity menus/guard và bootstrap/callers. Tests: shared fake catalog/photo fixtures, unit metadata/form/navigation, contract admin/customer, PostgreSQL read/failure/race/bot/migration suites và T03 regression callers. Docs: README, PROJECT/ARCHITECTURE/CONVENTIONS/DATABASE/SECURITY, hai catalog/admin specs và ledger này. Không thay dependencies, old migrations, ADRs, AGENTS, upstream skills hoặc `task.md`; không tự seed runtime.

  Environment giữ nguyên T03: Windows; app CPython **3.12.15**, uv/uv_build **0.13.4**, Pytest **9.1.1**, Ruff **0.17.1**, aiogram **3.31.0**, Pydantic **2.13.5**, PostgreSQL **18.6** thật qua test service đã pin. System Python **3.11.9** dùng riêng cho H. Commands dùng uv workspace; tất cả dòng PASS có exit **0**:

  | Command/check | Kết quả thực tế |
  | --- | --- |
  | `uv lock --check`; `uv sync --locked --no-build` | PASS; 47 resolved, 46 installed dependencies checked, không thêm package. |
  | `uv run --no-sync ruff check .`; `uv run --no-sync ruff format --check .` | PASS; 116 Python files formatted, không nới rules. |
  | `uv run --no-sync pytest tests/unit tests/contract tests/architecture -q --tb=short` | **211 passed**; final run 20.23s. |
  | `uv run --no-sync pytest tests/integration -q --tb=short` | **60 passed**; final run 23.08s, real isolated PostgreSQL 18.6, explicit APP_ENV/test DSN/DB marker và owned schema per test. |
  | `uv run --no-sync python scripts/check_architecture.py` | PASS; domain/application/adapters/import matrix, concrete wiring chỉ ở composition root. |
  | Migration/read/write acceptance trong D | PASS; head `0004_catalog`, drift rỗng; T03 profile/Owner/audit/pending Owner confirmation được giữ và còn thực thi; constraints/identity triggers, literal search/hidden filtering, current-permission locks, optimistic version và replay dedup. |
  | PostgreSQL + dispatcher end-to-end trong D | PASS; staff tạo/publish/ảnh/đổi giá/cancel/hide, customer browse và forged admin denial; callback cũ đọc giá mới và không mở hidden product. Mọi mock Telegram send/edit/ack đều assert không có DB connection đang checkout. |
  | `uv build --no-sources`; wheel/sdist inspection | PASS; sdist→wheel 0.1.0, đúng **48** Python package sources khớp bytes working tree, không tools/cache/venv/env/skills. |
  | `uv export --locked --no-dev --no-emit-project --format requirements-txt --output-file .cache/t04/runtime-requirements.txt` | PASS; ignored runtime artifact từ lock duy nhất. |
  | `uv venv --python .venv/Scripts/python.exe .cache/t04-wheel-runtime`; runtime install theo README | PASS; 34 deps cài offline từ reviewed cache bằng `--require-hashes --no-build`; wheel cài `--no-index --no-deps`, không dev deps. |
  | `.cache/t04-wheel-runtime/Scripts/python.exe -I` import smoke; `uv pip check --python .cache/t04-wheel-runtime/Scripts/python.exe` | PASS; all modules import từ isolated wheel khi settings poisoned/socket-connect stub, không Pytest; 35 packages compatible. |
  | `uv audit --locked` | PASS; 46 third-party packages không có known vulnerabilities/adverse statuses tại lúc chạy; native command experimental, chưa thay thế production security review. |
  | `python scripts/verify_harness.py`; `python -B -m unittest discover -s tests -p test_harness.py -v` | PASS; six upstream skills/hash/layout giữ nguyên; **6 tests, OK** trên system Python độc lập. |
  | `git diff --check`; `git diff --cached --check`; document/status audit | PASS; 9 changed Markdown files, 45 local links hợp lệ, fences đóng đủ; chỉ T01–T04 và bốn T04 children done, T05 pending. Không sửa T03 index, protected baseline hoặc Git history; HEAD vẫn `6f21cec`. |

  Evidence quality/review: focused tests fail trước implementation; regression cho blank description **1 failed, 5 passed** trước fix parser, optional-photo failure **1 failed** trước fix callback acknowledgement. Mutation probes bỏ current permission, audit, hidden filter lần lượt bị tests bắt (**1**, **2**, **1 failed**); khôi phục nguyên bytes rồi final C211/D60. DB photo regex `{16,512}` bị PostgreSQL từ chối vì bounded repetition max 255; đã reproduce bằng valid photo test và sửa new migration/model thành charset check + LENGTH, không đổi migration cũ. Self-review handler→use case→locks/audit/commit→send chưa phát hiện Critical/High còn mở trong scope.

  Một lượt D bị gián đoạn do Docker Engine không chạy; đã dừng đúng pytest session, khởi động Docker Desktop installed ở background, verify test container project/service/absolute workspace, rồi chạy final D60 trên DB thật. Sau final tests, query thấy **0 `tbs_test_*` schemas và 0 other test connections**, dừng riêng `postgres-test` thành công. Không xóa DB/volume hoặc dừng Docker Desktop.

  Giới hạn: T04 chỉ hoàn tất catalog/public metadata thuộc CI-01/04 và product maintenance thuộc AO-01/06, không claim toàn capability. Active là hiển thị công khai; UI ghi **Chưa nhận đơn**, không số tồn kho hoặc Buy button giả. Navigation chỉ giữ view params, tối đa 4096, TTL 600s và mất khi restart; permissions/product/price luôn đọc DB. Staff phải giữ public fields không chứa secrets; actual encrypted payload/stock thuộc T05/T10 và checkout thuộc T06. Telegram sends chỉ mock; chưa Telegram test/live, hosted CI, production/restore hoặc payments/refunds. Không commit/push. **T04 hoàn tất; T05 pending, cần giao riêng.**

- [x] **T05 — Encrypted inventory và atomic import.** Depends: T04. Spec: CI-03, CI-04, CI-06; ADR-0003. Scope: unit storage/crypto/HMAC, CSV/TXT upload import, retire permissions và low-stock query/signal. Acceptance: duplicate/invalid/oversize batch rollback; encryption fail-closed; two-connection import không trùng; secret không lọt logs/errors. Verify: C + D + focused security review. Reservation/order FKs và last-key checkout race thêm ở T06/T08, không tạo fake orders/schema sớm để test T05.

  - [x] **T05.a — Typed key/license import và AEAD.** Domain payloads/parser giới hạn UTF-8 CSV/TXT 1 MiB, 1.000 rows; giữ nguyên key material, row/error-code không echo secrets. Application-owned crypto port, AES-GCM 256-bit/96-bit nonce, AAD stable product/SKU/unit/type; separate HMAC dedup key; versioned environment settings fail closed. Unit tests wrong type, bounds, duplicate semantics, tamper/wrong AAD/key và old-version read; không expose decrypt/export use case trước entitlement.
  - [x] **T05.b — Atomic protected persistence.** Migration mới cho units và actor-bound encrypted import/retire confirmations; chỉ AVAILABLE/RETIRED trước reservation slice. Current permission, unique per-SKU fingerprint, two-connection races, cancel/expiry/replay, immutable identity/ciphertext và audit/commit rollback. Pending import chỉ chứa encrypted envelopes, chưa thành stock; clear pending envelopes khi apply/cancel. Không sửa migrations cũ hoặc dựng orders sớm.
  - [x] **T05.c — Telegram upload/stock/retire/low-stock.** Staff có inventory.manage upload TXT/CSV qua document caption; bounded memory-only download từ Telegram SDK, không arbitrary URL/path hoặc plaintext temp file. Server-side confirmation, unit-ID retirement, secret-free stock pagination và committed low-stock query/signal. Reuse current ingress/private/rate controls và admin navigation; mọi network I/O ngoài UoW. Wiring/config/callers/tests cập nhật cùng nhau.
  - [x] **T05.d — Review và completion evidence.** C + D + H, clean/T04 upgrade + metadata drift, failure/race/security tests, build/isolated wheel, dependency audit, final diff/docs/status. Cập nhật README/PROJECT/DATABASE/SECURITY/catalog spec; dừng trước T06, không live Telegram/payments/deployment.

  Threat focus: unauthorized upload/download/decrypt, malicious metadata/encoding/oversize rows, duplicate material và reversed-order concurrent imports, partial insert/audit/commit failure, cross-actor/revoked/stale confirmations, ciphertext/AAD swapping, missing/wrong/rotated keys và log/exception leaks. T05 sở hữu finite UNIQUE_KEY/LICENSE secrets; file/reusable/manual secret payloads thuộc T10. Low-stock trả committed counts/flag, chưa tự gửi alerts hoặc bật checkout.


  Hoàn tất local ngày **2026-10-21**. Files: domain `models/inventory.py`; application parser,
  `ports/inventory.py`, `ports/inventory_crypto.py`, `services/inventory.py`, UoW port;
  infrastructure `crypto.py`, versioned InventorySettings, inventory models/repository/UoW,
  schema head và frozen `0005_inventory.py`/Alembic wiring. Telegram: `admin/inventory.py`,
  Inventory menu, guard, bootstrap và mọi dispatcher/polling callers. Tests: shared fake
  inventory/document helpers, parser/crypto/settings/startup/upload contracts, PostgreSQL
  import/failure/read/retire/bot suites và migration/polling regressions. Docs: README,
  PROJECT/ARCHITECTURE/CONVENTIONS/DATABASE/SECURITY, catalog/admin specs, `.env.example`
  và ledger. Manifest/lock chỉ thêm cryptography + cffi/pycparser; old versions giữ nguyên.
  Không thay old migrations, ADRs, AGENTS, original task, upstream skills hoặc Git index/history.

  Environment: Windows; CPython **3.12.15**, uv/uv_build **0.13.4**, Pytest **9.1.1**,
  Ruff **0.17.1**, PostgreSQL **18.6** qua isolated service/digest T02, Docker Engine **29.6.2**.
  Cryptography **50.0.2**, cffi **2.1.1**, pycparser **3.11**; reviewed PyPI wheels, hash-pinned
  lock, `--no-build`, không source build bên thứ ba. System Python **3.11.9** chạy riêng H.
  Commands dùng uv workspace như README; các dòng PASS có exit **0**:

  | Command/check | Kết quả thực tế |
  | --- | --- |
  | `uv lock --check`; `uv sync --locked --no-build` | PASS; 50 resolved, 49 installed dependencies checked; mọi prior locked version giữ nguyên. |
  | `uv run --no-sync ruff check .`; `uv run --no-sync ruff format --check .` | PASS; 136 owned Python files formatted, không nới rules hoặc sửa upstream. |
  | `uv run --no-sync python scripts/check_architecture.py` | PASS; domain/application/adapters/import matrix. |
  | `uv run --no-sync pytest tests/unit tests/contract tests/architecture -q --tb=short` | **256 passed**, final 20.51s; gồm toàn bộ T03/T04 regressions và 45 T05 cases. |
  | `uv run --no-sync pytest tests/integration -q --tb=short` | **86 passed**, final 48.33s, real PostgreSQL 18.6; explicit APP_ENV/test DSN/DB marker, schema per test. Không SQLite/mock database hoặc skip tests. |
  | Domain/crypto/config acceptance | PASS; exact case/space/Unicode preservation, typed license/key, invalid UTF-8/control/header/column/multiline/size/rows; fresh nonce, wrong key/AAD/type/nonce/ciphertext từ chối; old-version read/dedup stable; missing/malformed/reused/changed/removed keys fail closed. |
  | Stock/race/failure acceptance trong D | PASS; full 1.000-row batch, pending không tính stock, per-SKU uniqueness gồm retired material, reverse-order parallel confirms với **2 physical DB connections**, replay/retire/cancel/expiry/revoked actor, immediate/deferred audit rollback và direct-SQL immutable/type/unique constraints. |
  | PostgreSQL dispatcher/startup trong D | PASS; upload/confirm/pages/retire/cancel/low-stock và revoke ngay trong download; mọi mock download/send/edit/ack assert không checked-out DB connection. Key mismatch fail trước Bot initialization, resources đóng sau failure. |
  | Migration tests trong D | PASS; head `0005_inventory`, drift rỗng; clean/base/T02/T03 và T04→head, giữ product identity/price/audit/pending catalog confirmation hoạt động. Không sửa `0001_runtime`–`0004_catalog`. |
  | `uv build --no-sources`; wheel/sdist inspection | PASS; sdist→wheel 0.1.0, đúng **57** package Python sources khớp bytes working tree; không tools/cache/venv/env/skills. Migrations vẫn chạy từ checkout theo README. |
  | `uv export --locked --no-dev --no-emit-project --format requirements-txt --output-file .cache/t05/runtime-requirements.txt` | PASS; runtime requirements/hash artifact ignored, vẫn một lockfile. |
  | `uv venv --python .venv/Scripts/python.exe .cache/t05-wheel-runtime`; `uv pip install --python .cache/t05-wheel-runtime/Scripts/python.exe --require-hashes --no-build -r .cache/t05/runtime-requirements.txt` | PASS; 37 runtime deps, không dev deps; wheel cài `--no-index --no-deps`. Offline attempt thiếu cached crypto artifacts nên fail; đã cài locked hashes từ PyPI, không đổi lock/hash hoặc bật source builds. |
  | `.cache/t05-wheel-runtime/Scripts/python.exe -I .cache/t05/runtime_smoke.py`; `uv pip check --python .cache/t05-wheel-runtime/Scripts/python.exe` | PASS; all modules từ isolated wheel, settings poisoned/socket-connect denied, không Pytest; native AES-GCM round-trip; 38 packages compatible. |
  | `uv audit --locked` | PASS; 49 third-party packages không có known vulnerabilities/adverse statuses tại lúc chạy. Native command experimental; chưa là full source/container/production audit. |
  | `python scripts/verify_harness.py`; `python -B -m unittest discover -s tests -p test_harness.py -v` | PASS; six upstream skills/hash/layout giữ nguyên; **6 tests, OK**, system Python độc lập app. |
  | `git diff --check`; document/status audit | PASS; 9 changed Markdown files, 45 local links hợp lệ, fences đóng đủ; chỉ T01–T05 và bốn T05 children done. Index rỗng, protected baseline không đổi, HEAD vẫn `9492004`; T06 pending. |

  Review/evidence quality: focused tests RED trước implementation; callback error regression
  **1 failed, 10 passed** trước fix không nuốt InventoryImportError; JSON-array keyring regression
  **1 failed, 10 passed** trước strict-object fix; cancel-applied regression **1 failed, 15 passed**
  trước fix từ chối undo. Mutation probes bỏ current confirm permission, bỏ audit, nới byte cap
  bị tests bắt lần lượt **1**, **4**, **1 failed**; source khôi phục nguyên bytes rồi final C256/D86.
  Self-review theo code-review-and-quality/security đi qua input→permission→seal→pending→locks/
  unique/audit/commit→network; chưa phát hiện Critical/High còn mở trong scope.

  Cleanup: verify container labels project `telebotshop-t02`, service `postgres-test`, absolute
  workspace. Sau final tests, query có **0 `tbs_test_*` schemas, 0 other test connections**;
  stop riêng service đã start cho T05 thành công. Không xóa DB/volume, không dừng Docker Desktop.

  Giới hạn: finite UNIQUE_KEY/LICENSE storage/import và crypto/low-stock parts của CI-03/04/06
  được chứng minh; không claim toàn CI hoặc release. Application crypto port không có decrypt;
  entitled read/delivery nối ở T09/T10. File/reusable/manual secrets thuộc T10, reservation/orders
  và last-key race thuộc T06/T08. Low-stock là committed query/flag, chưa auto-dispatch alert;
  customer UI vẫn **Chưa nhận đơn**. Key-registration lock serialize inventory writes; đo
  contention trước khi thu hẹp. Không bulk re-encryption/removal/restore; mất key không phục
  hồi kho chỉ từ DB. Expired encrypted pending envelopes/audit chưa có retention job khi chưa
  có Owner policy. Telegram API/download chỉ mock; chưa real Telegram/test Stars, hosted CI,
  Linux/macOS runtime, production image/deploy, backup/restore hoặc payments/refunds.
  Không stage/commit/push. **T05 hoàn tất; T06 pending, cần giao riêng.**

## Phase 2

- [x] **T06 — Create order, reserve và expiry.** Depends: T05. Spec: OP-01, OP-02, OP-07; CI-02, CI-05. Scope: order/item snapshots và reservation FK migration, action idempotency, finite-stock reservation, invoice intent/Stars adapter, guarded expiry/cancel. Acceptance: quantity-one checkout/price snapshot; no network under DB lock; double-click/last-key/expiry races giữ stock đúng. Verify: C + D, mocked invoice outcomes gồm uncertain và price change, hai independent DB connections.

  - [x] **T06.a — Quantity-one contracts và atomic create/reserve.** Domain order/invoice states/read DTOs, application-owned ports/use cases, SQLAlchemy order/item/invoice tables và migration `0006_orders`. Buyer + action UUID unique; immutable public price/type/title snapshot; same action đổi product bị chặn. Customer identity từ DB; lock buyer/product rồi AVAILABLE unit, FK ràng buộc unit↔item/product. TTL 15 phút theo DB time, bounded lock/statement timeout; contention không giả hết hàng. Không đọc/decrypt inventory payload.
  - [x] **T06.b — Guarded cancel/expiry và inventory callers.** Chỉ AWAITING_PAYMENT → EXPIRED rồi release đúng reservation của order; order trước inventory lock, audit cùng commit. RESERVED là state thật; stock read/render bổ sung reserved count, retire không chạm reserved unit. Expiry batch bounded, locks theo ID, idempotent/cross-buyer/expiry-vs-reallocation tests; không giả PAID/payment tables để claim T08 race sớm.
  - [x] **T06.c — Durable invoice intent và Stars adapter.** Claim READY→SENDING/attempt trước network, typed SENT/REJECTED/UNCERTAIN outcome sau network, không auto-resend SENDING/UNCERTAIN. Snapshot XTR/one price/provider token rỗng/nonempty start parameter; body không có inventory/PII tùy ý. Crash/stale claims chuyển uncertain qua bounded recovery; known rejection close/release unpaid order an toàn. Telegram API mock, pool assertions và commit/cancel/network failure tests. Polling chưa mở Buy/invoice trước pre-checkout + durable receipt T07/T08.
  - [x] **T06.d — Runtime maintenance/review/gates/docs.** Composition root/UoW/migrations/callers/fixtures, explicit `expire-orders` CLI chạy một batch expire/recover, không Bot hoặc encryption key dependency. C + D + H; T05 upgrade preservation, metadata drift, mutation probes, build/wheel/native runtime smoke/audit và final docs/status. Dừng trước T07; không real Stars/Telegram/production writes.

  Threat/failure focus: forged buyer/product/price/action, repeated/different-product action, last-key competition/retire/timeout, partial order/item/unit/invoice/audit commit, cross-owner cancel, old expiry sau reallocation, invoice send-vs-cancel/expiry, crash/uncertain/lost post-send commit và blind resend. T06 chỉ finite UNIQUE_KEY/LICENSE; nonfinite secret payload/availability còn T10. Giữ receipt-ready order/payload history khi expired/rejected; không mark PAID từ invoice result. Customer checkout activation đợi T07/T08, tránh invoice khi chưa có payment intake.

  Hoàn tất 2026-10-21. Thay đổi: `domain/models/orders.py`, orders/invoices ports và
  OrderService/InvoiceService; SQL orders repository/Core metadata, `0006_orders`, UoW/schema/
  Alembic wiring; inventory RESERVED/count/render; `bootstrap.py` DB-only maintenance CLI.
  Tests mới: `tests/orders.py`, unit order/expiry CLI, invoice SDK contract và PostgreSQL
  order/invoice suites; bổ sung migration preservation/downgrade. README, PROJECT,
  ARCHITECTURE, CONVENTIONS, DATABASE, SECURITY và capability specs cập nhật cùng slice.
  Không thêm dependency/config/feature flag; migration `0001`–`0005` giữ nguyên.

  TDD evidence: checkout suite fail import OutOfStock/order module trước implementation;
  invoice contract/service fail missing adapters/services; expiry CLI fail missing entrypoint.
  Sau implementation, focused suites green rồi chạy final gates dưới đây (đều exit 0):

  ```text
  uv lock --check                                    # 50 packages resolved
  uv sync --locked --no-build                        # 49 packages checked, lock unchanged
  uv run --no-sync ruff check .                      # pass
  uv run --no-sync ruff format --check .             # 151 owned Python files
  uv run --no-sync pytest tests/unit tests/contract tests/architecture -q --tb=short
                                                     # 297 passed, 15.96s
  uv run --no-sync python scripts/check_architecture.py
  uv build --no-sources                              # sdist + wheel
  uv run --no-sync pytest tests/integration -q --tb=short
                                                     # 127 passed, 58.63s, PostgreSQL 18.6
  python scripts/verify_harness.py                   # six skills/hashes/references intact
  python -B -m unittest discover -s tests -p test_harness.py -v
                                                     # 6 passed, Python 3.11.9
  uv audit --locked                                 # 49 deps, no known advisories/adverse status
  ```

  Runtime CPython 3.12.15, uv/uv_build 0.13.4, Ruff 0.17.1, Pytest 9.1.1; dependencies
  giữ nguyên T05. D dùng explicit allowlisted TEST_DATABASE_URL/APP_ENV=test, per-test
  owned schemas/markers. Last-key test giữ product lock, xác nhận **hai backend PIDs**
  khác nhau trong pg_locks trước release barrier; double-click chỉ một order/intent/reservation.
  Price edit giữ snapshot; hidden/unregistered/nonfinite fail-closed; LICENSE reserve thật.
  Cancel/expiry races, old expiry sau reallocation, retire reserved, bounded lock timeout,
  immediate SQL guards và deferred audit/commit failure giữ stock/order/audit atomic.
  Expiry/recovery dùng valid historical rows + DB time, không sửa clock/disable triggers.
  T05 upgrade giữ ciphertext/proof/pending import/audit; metadata drift rỗng, empty downgrade/
  re-upgrade pass; downgrade có order history bị từ chối.

  Mocked Stars request kiểm XTR/one integer price/blank provider/nonempty start parameter,
  bounded text/10s transport/no PII request. Known API reject khác network/server/decode/timeout
  uncertain. Claim committed trước send và pool checkout = 0 ở send; concurrent/cancel/expiry/
  task interruption/post-send commit failure không resend hoặc mark PAID. Recovery đánh dấu
  stale SENDING thành UNCERTAIN, không gọi network. Real DB maintenance entrypoint pass.

  Self-review theo code-review-and-quality/security-and-hardening: đã đọc connected paths,
  ownership/locks/FKs/immutable snapshots, transaction/audit rollback, secret redaction và
  durable external-effect boundaries; không còn Critical/High trong T06 scope. Ba mutation
  probes invert buyer ownership, bỏ create audit, bỏ READY resend guard: mỗi probe có đúng
  một focused test fail; restore source byte-for-byte rồi final C/D green.

  Wheel gate theo README: `uv export --locked --no-dev --no-emit-project --format requirements-txt`,
  fresh isolated venv, `uv pip install --offline --require-hashes --no-build` runtime deps,
  `uv pip install --no-index --no-deps` wheel. **65 wheel source files** khớp working tree;
  sdist/wheel không có tools/cache/secrets. Python -I import cả 65 modules với poisoned
  settings, network bị chặn, không pytest; native AES-GCM roundtrip pass.
  Native uv advisory audit là experimental; không thay source/container/release assessment.

  Final static checks: **35 T06 files (15 new)**, 9 Markdown files/48 local links,
  balanced fences, `git diff --check` exit 0; không trailing whitespace kể cả new files.
  SHA-256 của staged binary diff khớp lúc bắt đầu T06; protected files không đổi.

  Cleanup: verify project/service/absolute workspace labels, query **0 tbs_test_* schemas,
  0 other test connections**, rồi stop riêng postgres-test đã start cho T06; không xóa volume
  hoặc dừng Docker Desktop. T05 staged content/index giữ nguyên; không stage/commit/push.

  Giới hạn: OP-01/02/07 và CI-02/05 chỉ trong finite pre-payment T06 scope; pre-checkout,
  approval extension, receipt/PAID allocation, payment-vs-expiry và entitlement vẫn T07–T10.
  Public polling chưa Buy/invoice trước T07/T08; không mở live payment bằng flag. Nonfinite
  secret availability còn T10. Maintenance là một batch operator command, chưa scheduler;
  uncertain invoice không có automatic/manual resend command, cần reconciliation evidence.
  Chưa Telegram test/live, hosted CI, Linux/macOS, production image/deploy hoặc backup/restore.
  **T06 hoàn tất; T07 pending, cần được giao riêng.**

- [x] **T07 — Pre-checkout boundary.** Depends: T06. Spec: OP-02,07. Scope: Telegram pre-checkout parsing/response + application validation/serialized approval. Acceptance: owner/currency/amount/payload/status/stock checks; duplicate query idempotent, query khác không double-approve; bounded DB/API deadline đáp ứng giới hạn Telegram. Verify: C + D và controlled timeout/contention tests; approval không được mark PAID.

  - [x] **T07.a — Contracts và approval schema.** Bounded opaque query ID, registered buyer, XTR/integer snapshot amount và exact payload; `0007_pre_checkout` lưu một immutable approval/order và unique query ID. Mở guard cho một lần gia hạn order + exact reserved unit, giữ nguyên migrations 0001–0006 và encrypted stock/history.
  - [x] **T07.b — Serialized application approval.** Lock order→active product→exact reservation, đọc DB time sau locks; only unpaid/unexpired/SENDING|SENT|UNCERTAIN invoice. Same query idempotent không kéo dài TTL/audit, query khác không double-approve; new approval + max(old expiry, approval+15m) + unit expiry + audit cùng commit. Bounded total DB budget và race/rollback tests. Invoice reject sau approval giữ uncertain, không auto-release vì kết quả mâu thuẫn.
  - [x] **T07.c — Telegram boundary và runtime safety.** PreCheckoutQuery parsing/use-case/AnswerPreCheckoutQuery, Vietnamese reject, bounded DB/API budgets, no network trong UoW, no blind answer retry. Polling nhận pre_checkout_query và trả unavailable tới khi T08 có durable receipt; không mở Buy hoặc thêm flag bỏ qua gate. Mock adapter và real PostgreSQL dispatcher tests cho success/reject/replay/contention/API uncertainty.
  - [x] **T07.d — Review, regressions và evidence.** C+D+H, T06 upgrade preservation/metadata/downgrade guards, focused mutation probes, isolated wheel/runtime/audit, caller/docs/status và Git preservation. Dừng trước T08; không real Stars/messages/production writes.

  Polling dùng native concurrent update tasks, cap 32, để pre-checkout không chờ sau import
  download; mỗi update vẫn có UoW/session riêng. Kiểm tra SDK shutdown/drain cùng thay đổi.

  Threat/failure focus: wrong buyer/amount/currency/payload, guessed/cross-order/replayed query,
  stale/inactive/expired/reallocated stock, concurrent approval/cancel/expiry/product hide,
  partial extension/audit or ambiguous commit, slow locks/pool/network and lost API answer.
  Approval không mark PAID, không allocate/decrypt/send hàng; durable query binding không
  reset sau timeout hoặc expiry. T08 vẫn phải lưu late/extra/unmatched receipts.

  Hoàn tất 2026-10-21. Thay đổi: domain PreCheckout, callable port/4s DB budget,
  PreCheckoutService, orders repository/port/model và migration `0007_pre_checkout`;
  infrastructure Telegram router/3s answer budget; InvoiceService giữ UNCERTAIN nếu known
  rejection tới sau approval. Bootstrap thêm fail-closed query handler và native concurrent
  polling cap 32 + bounded drain; cập nhật Telegram fixtures/polling/migration callers.
  Tests mới: unit validation, SDK contract, PostgreSQL approval/races/adapter/pool/deadline.
  PROJECT/README/architecture/conventions/database/security và capability specs cập nhật.
  Không thêm dependencies/config flag; migrations 0001–0006, ADRs, AGENTS/task/upstream giữ nguyên.

  TDD evidence: missing PreCheckoutService làm focused suite fail trước implementation;
  invoice reject sau approval làm order EXPIRED (test fail) trước guard UNCERTAIN;
  runtime query trước wiring không có answer (test fail); approval TRUNCATE chưa bị chặn
  (test fail) trước statement trigger. Contract phát hiện query.answer shortcut không áp
  request_timeout; dùng Bot.answer_pre_checkout_query trực tiếp và verify timeout=3.

  Final C+D+H (đều exit 0; CPython 3.12.15/uv+uv_build 0.13.4/Ruff 0.17.1/Pytest 9.1.1):

  ```text
  uv lock --check                                    # 50 resolved, lock unchanged
  uv sync --locked --no-build                        # 49 checked
  uv run --no-sync ruff check .                      # pass
  uv run --no-sync ruff format --check .             # 161 owned Python files
  uv run --no-sync pytest tests/unit tests/contract tests/architecture -q --tb=short
                                                     # 332 passed, 17.19s
  uv run --no-sync python scripts/check_architecture.py
  uv build --no-sources                              # sdist + wheel
  uv run --no-sync pytest tests/integration -q --tb=short
                                                     # 160 passed, 98.32s, PostgreSQL 18.6
  python scripts/verify_harness.py                   # six skills/hashes/references intact
  python -B -m unittest discover -s tests -p test_harness.py -v
                                                     # 6 passed, Python 3.11.9
  uv audit --locked                                 # 49 deps, no known advisory/adverse status
  ```

  Ruff cache chuyển vào ignored `.cache/ruff-t07` qua RUFF_CACHE_DIR khi default cache có
  Windows access warning; không sửa config/lint rules. D dùng APP_ENV=test và explicit
  allowlisted TEST_DATABASE_URL/per-test schema markers, không production/default DB.
  Duplicate approval barrier xác nhận **hai backend PIDs** trước release order lock;
  same query không kéo dài TTL/audit, different/global reused query không double-approve.
  Buyer/amount/payload/unsent/inactive/expired/wrong reservation fail closed; current catalog
  price không thay snapshot. Approval-vs-cancel/expiry, old reservation/reallocation, DB time
  sau lock wait, immediate/deferred audit failure và direct-SQL guards được kiểm chứng.
  Approval/TTL trên order/unit/audit rollback cùng nhau; UPDATE/DELETE/TRUNCATE approval bị chặn.
  T06 upgrade giữ order/invoice/reservation/ciphertext; metadata drift rỗng; downgrade không
  approval giữ orders, downgrade có approval history bị từ chối.

  SDK/real DB adapter kiểm tra commit trước answer/pool không giữ use-case connection,
  wrong query safe Vietnamese reject, lost/network/decode/timeout answer không clear binding
  hoặc blind retry. Real order lock 2s và exhausted pool total 4s trả answer trong <8s local;
  không suy local measurement thành Telegram delivery SLO. Concurrent native dispatcher trả
  pre-checkout khi message task đang chờ; shutdown normal/error/cancel/deadline drain hoặc
  cancel+rollback task trước Bot/engine close. Không shared AsyncSession hoặc network trong UoW.

  Self-review code-review-and-quality/security-and-hardening: ownership/snapshots/unique
  query binding, lock order, atomic extension/audit, expiry and ambiguous API boundaries đã
  đọc cùng callers; không còn Critical/High trong T07 scope. Bốn mutation probes invert buyer,
  bỏ amount check, cho different query pass, mở runtime gate: mỗi probe đúng một focused test
  fail; restore source byte-for-byte rồi chạy final C/D. SDK drain dùng private pending-task
  set của pinned aiogram 3.31.0 vì chưa có public API; có `shortcut:` và lifecycle regression
  tests, phải review lại khi nâng SDK. Saturated/live polling load chưa được nghiệm thu.

  Wheel gate theo README: locked runtime export, fresh isolated venv, offline hash-checked
  binary dependencies, no-index/no-deps install wheel. **69 wheel source modules** khớp
  working tree; sdist/wheel không có tools/cache/secrets. Python -I import cả package với
  poisoned settings/no network/no pytest và native AES-GCM roundtrip pass. Native uv audit
  còn experimental, không thay source/container/production review.

  Final static checks: **28 T07 files (10 new)**, 9 Markdown files/48 local links, balanced
  fences và `git diff --check` exit 0; không trailing whitespace kể cả new files. Starting
  staged diff/HEAD/branch và protected files giữ nguyên; chỉ T01–T07 được tick done.

  Cleanup: verify labels project/service/absolute workspace, query **0 tbs_test_* schemas,
  0 other test connections**, stop riêng postgres-test đã start cho T07. Không xóa volume
  hoặc dừng Docker Desktop; không stage/commit/push.

  Giới hạn: OP-02/07 chỉ finite pre-payment validation/approval T07; chưa receipt/PAID,
  allocation/fulfillment/payment-vs-expiry hoặc public Buy. Runtime router luôn reject tới
  T08 có durable intake; không bật approval bằng flag và không claim đang nhận tiền.
  Approval không chứng minh đã thanh toán; same query replay vẫn phải còn current-valid,
  không reset history sau timeout/cancel/expiry. Telegram test/live, hosted CI, Linux/macOS,
  production image/deploy/restore và live latency/load chưa kiểm chứng.
  **T07 hoàn tất; T08 pending, cần được giao riêng.**

- [x] **T08 — Durable payment receipt và fulfillment intent.** Depends: T07. Spec: OP-03..05,07. Scope: payments/fulfillments contract, successful-payment handler và atomic UoW. Acceptance: duplicate/conflicting/extra/late/unknown receipts được giữ đúng; matched payment + allocation + work atomic; webhook không ack trước durable outcome. Verify: C + D, concurrent duplicates, injected commit failure và payment-vs-expiry race; no delivery call trong handler.

  - [x] **T08.a — Receipt ledger và typed committed work.** Minimal bounded successful-payment DTO, immutable payments/conflicting observations, MATCHED/REQUIRES_REVIEW, nullable unknown order/payer, unique charge và conditional primary matched/order; PAID/attention, permanent ALLOCATED unit và READY finite fulfillment intent. Migration 0008_payments, no plaintext/raw Update or fake worker states.
  - [x] **T08.b — Atomic payment use case.** Charge-scoped transaction lock → order → exact unit; duplicates no-op, conflicting content durable review, extra charge preserved. First valid buyer/XTR/amount/payload receipt MATCHED + PAID; still-owned reservation allocated + one work atomically, otherwise PAID + attention/no READY work. Ledger/conflicts là append-only financial audit; matched order transition thêm redacted general audit. Rollback/commit/race tests, no network/decrypt.
  - [x] **T08.c — Durable Telegram intake và wiring.** Shared successful-payment parser/handler bypass ordinary message admission/error swallowing; DB failure propagates. Pinned SDK polling wrapper persists financial receipt before yielding update/advancing offset; direct feed/webhook caller returns only after commit. Runtime pre-checkout uses T07 service once receipt handler registered. Production webhook/auth/body cap còn T14/T18; HTTP error/ack contract tested through an ASGI test harness, no live Telegram calls. No Buy before worker T09; no delivery in receipt handler.
  - [x] **T08.d — Review/regressions/evidence.** UoW/schema/stock callers include ALLOCATED count; T07 upgrade/ciphertext/approval preservation, metadata and guarded downgrade; two-connection duplicate/extra/conflict and payment-vs-expiry tests, fault injection, C+D+H/build/isolated wheel/audit/docs and staged-index preservation. Stop before T09.

  Clarification theo ADR-0002: TTL tự nó không giải phóng key; receipt thắng order lock khi
  exact reservation còn thuộc order vẫn có thể allocate an toàn dù deadline vừa qua. Khi
  guarded expiry/cancel đã release/reallocate, valid late receipt vẫn PAID + RESERVATION_LOST,
  không lấy AVAILABLE unit hoặc key của order khác. Approval không phải payment evidence.
  Unknown/missing-payer/unsupported recurring receipts giữ trong ledger cần review; không
  tạo identity giả hoặc gán event hệ thống cho Owner. Original receipt không overwrite khi
  replay conflict. Webhook production/auth còn T14/T18; không gọi mocked feed thành live webhook.

  Hoàn tất local theo yêu cầu Owner ngày 2026-10-21. T08.a/b: `domain/models/payments.py`,
  payments port/service, SQL tables/repo/UoW, migration `0008_payments`; Order PAID/attention,
  inventory ALLOCATED/count/admin view. T08.c: shared parser/router + pinned SDK dispatcher,
  financial ingress bypass, composition root/checker thật và tất cả callers/fixtures. Tests mới:
  `tests/unit/test_payments.py`, `tests/contract/test_payment_intake.py`,
  `tests/integration/test_payments.py`, `test_payment_intake.py`, `test_payment_migrations.py`,
  `tests/payments.py`; existing migration/pre-checkout/bot fixtures cập nhật đúng schema/API.
  README, PROJECT, ARCHITECTURE, CONVENTIONS, DATABASE, SECURITY và spec/plan đồng bộ.

  Git: HEAD hiện tại `069fe52` là commit T07 có sẵn; initial index snapshot SHA-256 là empty
  diff và giữ nguyên. Ghi chú đầu phiên còn nói T07 staged đã được sửa theo evidence hiện tại.
  Không agent stage/commit/push/reset; giữ nguyên AGENTS/task.md/ADRs, sáu skills/harness,
  manifest/lock/settings/Compose và revisions 1–7. Không thêm dependencies.

  Test-first: ba initial PostgreSQL cases RED vì payments domain chưa có; ba intake cases RED
  vì dispatcher chưa nhận payment services. Sau code, payment/stock/work và commit-before-offset
  GREEN. Self-review bắt provider reference check quá chặt: regression XTR với nonempty provider
  ID RED (1 failed/1 passed), sửa service + deferred constraint rồi pass; field chỉ là evidence.
  First broad D: 196 passed/2 failed do cached prepared SELECT * đổi result shape sau DDL;
  tests đổi sang toàn bộ old-column projection, không bỏ field/assertion hoặc disable cache.

  | Command / acceptance | Kết quả cuối, exit 0 |
  | --- | --- |
  | `uv lock --check`; `uv sync --locked --no-build` | PASS; graph 50 kể cả project, pins giữ nguyên. |
  | `uv run --no-sync ruff check .`; `ruff format --check .` | PASS; 174 owned Python files, không tắt rule. |
  | `uv run --no-sync pytest tests/unit tests/contract tests/architecture -q --tb=short` | **359 passed in 20.36s**. |
  | `uv run --no-sync pytest tests/integration -q --tb=short` | **202 passed in 162.05s**, PostgreSQL 18.6 thật, isolated marked DB/schemas. |
  | `uv run --no-sync python scripts/check_architecture.py` | PASS. |
  | `uv build --no-sources` | PASS sdist→wheel; artifact source/secret/cache checks pass. |
  | Runtime wheel smoke theo commands dưới | PASS; environment riêng runtime only, 75 modules đúng working tree, poisoned settings/no network/no pytest và native AES-GCM smoke pass. |
  | `uv audit --locked` | 49 third-party packages: no known vulnerabilities/adverse statuses; uv command experimental, advisory snapshot không thay whole-source/release audit. |
  | `python scripts/verify_harness.py`; `python -B -m unittest discover -s tests -p test_harness.py -v` | PASS; **6 tests OK**, global Python 3.11.9 độc lập app 3.12.15. |
  | `git diff --check`; document/local-link/fence/whitespace/protected/index checks | PASS; chỉ T01–T08 done, T09 pending. |

  Exact runtime artifact commands (cache paths ignored; smoke asserts như README, thêm native AES-GCM):

  ```text
  uv export --locked --no-dev --no-emit-project --format requirements-txt --output-file .cache/t08/runtime-requirements.txt
  uv venv --python .venv/Scripts/python.exe .cache/t08-wheel-runtime
  uv pip install --offline --python .cache/t08-wheel-runtime/Scripts/python.exe --require-hashes --no-build -r .cache/t08/runtime-requirements.txt
  uv pip install --python .cache/t08-wheel-runtime/Scripts/python.exe --no-index --no-deps dist/telebotshop-0.1.0-py3-none-any.whl
  .cache/t08-wheel-runtime/Scripts/python.exe -I .cache/t08/runtime_smoke.py
  .venv/Scripts/python.exe .cache/t08/artifact_check.py
  ```

  Concurrency evidence: same-charge advisory wait trên hai physical DB PIDs; different charge
  cùng order qua hai RowShareLock connections chỉ một primary/work. Duplicate, cross-order
  conflicting content/digest replay và extra charges không credit/cấp lại. Expiry thắng lock:
  receipt vẫn PAID + RESERVATION_LOST; payment thắng lock: expiry SKIP LOCKED bỏ qua, key
  ALLOCATED không release. Unit đã reserve/allocate cho buyer mới không bị late receipt lấy lại.
  Snapshot price giữ dù catalog đổi. Unknown/missing payer, wrong buyer/currency/amount và
  unsupported recurring/context giữ review, không tạo fake identity hoặc mất financial evidence.
  Fault injection mark_paid/allocate/save_work/audit/commit rollback receipt/state/stock/work;
  deferred trigger bắt missing intent. Direct SQL chặn sửa/xóa/truncate ledger/work, unpaid lại
  và release allocated. T07 row/approval/ciphertext preservation, metadata và guarded downgrade pass.

  Intake evidence: same parser cho polling và handler; native GetUpdates offset chưa tiến trước
  durable record, DB error dừng fetch không ack, restart redelivers. Receipt bypass full rate
  window/missing-profile/group admission; lỗi DB propagate, không generic-success hoặc send.
  ASGI harness commit failure→500, replay→200 chỉ một receipt/work, không plaintext/PII/secret
  response và không delivery API. Đây chưa phải authenticated deployed webhook.
  Mutation review đảo amount check (**2 failed**), duplicate equality (**1 failed**), đưa record
  sau polling yield (**1 failed**), bỏ financial guard bypass (**1 failed**): đều exit 1 đúng kỳ
  vọng, mỗi source restored byte-for-byte trước final C/D. Five-axis self-review không còn
  Critical/High/Required trong T08; không nới lint/tests. Environment/pins như T07, không nâng SDK.

  Cleanup: xác minh Compose project `telebotshop-t02`, service `postgres-test` và exact workspace
  label trước lifecycle actions. Sau final D, DB trả `test_schemas=0`, `other_connections=0`;
  đã stop đúng postgres-test đã start cho T08, không down/remove volume hoặc đóng Docker Desktop.

  Giới hạn: private aiogram 3.31.0 polling/drain hooks cần regression review khi nâng pin;
  receipt persistence có thể trì hoãn polling khi DB contention, chưa live SLO/load evidence.
  Review/conflict/paid-attention records chờ reconciliation/refund có quyền ở task sau; chưa
  tự sửa receipt, lấy key mới hoặc hoàn tiền. Chưa worker/lease/attempt/decrypt/delivery/Buy,
  production webhook/auth, actual Telegram test/live, hosted CI, Linux/macOS/container app,
  production role separation/image/deploy hoặc key/DB restore. Không gọi mocks là live gates.
  **T08 hoàn tất; dừng trước T09 pending, cần được giao riêng.**

- [x] **T09 — Key/license worker và recovery.** Depends: T08. Spec: FD-01..03,06; CI-05. Scope: work claim/lease/CAS, delivery attempts, unique-key/license sending, retry/uncertain/alerts. Acceptance: một active sender, stale token bị chặn, crash sau send không cấp key mới và không auto-send uncertain. Verify: C + D, two-worker races và fault injection trước/sau từng durable/send boundary; focused security review.

  - [x] **T09.a — Durable claim/lease/recovery.** Migration 0009_fulfillment mở READY intent thành work ledger; attempt UUID đồng thời là fencing token, append-only sanitized result observations, permanent allocation giữ nguyên. Claim work-only SKIP LOCKED commit riêng; prepare/complete/recover lock order→work. CLAIMED expired requeue; SENDING expired UNCERTAIN, no automatic resend.
  - [x] **T09.b — Authorized finite delivery.** Application kiểm tra primary payment/order/buyer/destination/exact allocated unit/type/hold + current token/lease trước đọc encrypted envelope/decrypt. Commit SENDING trước API; SENT/known rejection/rate-limit retry/UNCERTAIN lưu theo token; late results giữ observation, không overwrite newer state. Bounded send count/backoff, crypto/known failure/uncertain tạo durable attention/audit và redacted operational alerts; không allocate mới.
  - [x] **T09.c — Adapter/runtime.** UNIQUE_KEY/LICENSE plain-text send tới private buyer, no previews/raw payload logs; bounded timeout và deadline. Worker command với validated policy, startup recovery, per-call UoW, cancellation/shutdown closes clients/engine. Chưa chạy Telegram thật; refund hold là persisted pre-send boundary cho T13, không thêm refund/reconcile UI. Public Buy/navigation tích hợp trong core UI T14, không mở flow năm types khi T10 còn thiếu.
  - [x] **T09.d — Evidence/review/docs.** PostgreSQL two-worker/lease/refund-hold races, stale token/results, failure before/after durable/send boundaries, ciphertext/approval/payment preservation + guarded downgrade; Telegram mocked outside DB. C+D+H/wheel/audit, mutation review, protected source/index checks; chỉ đóng T09 khi đủ evidence, dừng trước T10.

  Implementation clarification: work hold nằm trên fulfillment, serialize bằng order lock;
  không có refund operations trước T13. Alerts gồm durable attention/audit và sanitized log;
  actual staff notification routing thuộc T14. Attempt UUID là lease/fencing capability, không
  dùng username/callback/order ID làm quyền decrypt. Recovery không tự gửi lại UNCERTAIN.

  Hoàn tất local 2026-10-22 theo yêu cầu Owner. Files: domain `fulfillment.py` + paid lifecycle
  enum; application fulfillment port/service + UoW/InventoryCipher.open; SQL model/repo,
  orders/work metadata, schema head/Alembic env và `0009_fulfillment`; finite delivery adapter,
  worker loop, bootstrap/WorkerSettings. Tests: `tests/fulfillment.py`, unit delivery models/
  worker/runtime, contract delivery và integration fulfillment/recovery/races/failures/integrity/
  migrations. README/PROJECT/ARCHITECTURE/CONVENTIONS/DATABASE/SECURITY/fulfillment/OP spec/plan
  đồng bộ. Không dependencies hoặc speculative file/manual/refund/resend worker.

  Git snapshot trước edits: HEAD `19e7b53` (T08 đã commit), index SHA-256 empty diff; current
  index/head/branch giữ nguyên. Ghi chú T08 staged lúc đầu không thay snapshot này. Agent không
  stage/commit/push/reset; AGENTS/task.md/ADRs, sáu skills/harness, pins/manifest/lock/.env.example,
  Compose và revisions 1–8 giữ nguyên. T09 không chạy Bot/worker command thật.

  Test-first: initial two PostgreSQL cases RED thiếu fulfillment model, adapter RED thiếu
  finite sender, worker settings/loop RED thiếu WorkerSettings. Sau code các vertical slices
  GREEN. Tests còn bắt/sửa wrong payload type trước SENDING, reduced retry budget sau restart,
  freeform observation error và rewrite sending timestamp; mỗi case RED rồi GREEN. Historical
  valid rows/DB-time seam kiểm thử expiry, không sleep TTL thật hoặc disable integrity guards.

  | Command / gate | Kết quả cuối, exit 0 |
  | --- | --- |
  | `uv lock --check`; `uv sync --locked --no-build` | PASS; graph 50 kể cả project, pins giữ nguyên. |
  | `uv run --no-sync ruff check .`; `uv run --no-sync ruff format --check .` | PASS; 195 owned Python files. |
  | `uv run --no-sync pytest tests/unit tests/contract tests/architecture -q --tb=short` | **402 passed in 20.20s**. |
  | `uv run --no-sync pytest tests/integration -q --tb=short` | **238 passed in 200.50s**, isolated marked PostgreSQL 18.6 thật. |
  | `uv run --no-sync python scripts/check_architecture.py` | PASS. |
  | `uv build --no-sources` | PASS sdist→wheel; source/secret/cache/protected artifact checks pass. |
  | Runtime-only wheel commands dưới | PASS; 84 modules chính xác working tree, poisoned settings/no network/no pytest/native AES-GCM. |
  | `uv audit --locked` | 49 third-party packages: no known vulnerabilities/adverse statuses. Experimental uv command, advisory snapshot. |
  | `python scripts/verify_harness.py`; `python -B -m unittest discover -s tests -p test_harness.py -v` | PASS; **6 tests OK**, global Python 3.11.9 độc lập app CPython 3.12.15. |
  | `git diff --check`; local-link/fence/whitespace/protected/index checks | PASS; chỉ T01–T09 done, T10 pending. |

  ```text
  uv export --locked --no-dev --no-emit-project --format requirements-txt --output-file .cache/t09/runtime-requirements.txt
  uv venv --python .venv/Scripts/python.exe .cache/t09-wheel-runtime
  uv pip install --offline --python .cache/t09-wheel-runtime/Scripts/python.exe --require-hashes --no-build -r .cache/t09/runtime-requirements.txt
  uv pip install --python .cache/t09-wheel-runtime/Scripts/python.exe --no-index --no-deps dist/telebotshop-0.1.0-py3-none-any.whl
  .cache/t09-wheel-runtime/Scripts/python.exe -I .cache/t09/runtime_smoke.py
  .venv/Scripts/python.exe .cache/t09/artifact_check.py
  ```

  Acceptance evidence: two physical workers (distinct PG PIDs) không cùng claim locked work;
  nonce stale/expired không prepare/decrypt/send. Hai phía thắng order lock với hold đều
  deterministic; hold trước chặn decrypt, SENDING trước chặn automatic hold/refund. Claim chỉ
  khóa work và commit; prepare/complete/recovery order→work, không DB transaction khi Telegram.
  Unique-key/license gửi đúng typed material/private buyer, plain text/no preview, same unit
  qua retry. Known rate-limit RETRY_WAIT tôn trọng retry-after; max attempts recheck trước decrypt.
  FAILED/UNCERTAIN không auto requeue, allocation/payment giữ nguyên. Deadline hết không API.

  Recovery CLAIMED→ABANDONED/READY, SENDING→UNCERTAIN. Late success trước/sau recovery chỉ giữ
  typed/digest observation, không mark delivered hoặc overwrite current state. Fault injection
  cả ba commit boundaries, sau API success trước result commit, mất ACK sau result commit và
  cancellation: không mất paid work/allocate key mới hoặc tự gửi lại unknown send. Terminal
  work/attempt/observations/binding/timestamps không rewrite/delete/truncate; freeform SDK error
  không vào ledger/audit/log. T08 toàn bộ old-column data/ciphertext/receipt/work preservation,
  metadata và guarded downgrade pass. CLI/runtime lifecycle mocks chứng minh resources đóng
  cả normal/error/cancel; schema/key/Owner checks trước Bot startup, settings budgets validated.

  Mutation review: đảo active-token check, bỏ hold gate, đảo CLAIMED recovery branch, bỏ late
  result lease check: **mỗi probe 1 failed**, exit 1 đúng kỳ vọng; source restored byte-for-byte
  trước final C/D. Five-axis self-review không còn Critical/High/Required trong T09. Không nới
  lint/tests, thêm dependency, broker hoặc alternate entitlement contract. Environment/pins như T08.

  Performance review bổ sung ba query indexes: attempts theo work, work theo order và active
  attempt để hỗ trợ aggregate send count/transition guard/recovery khi ledger lớn; chưa claim
  benchmark tải. Sau thay đổi, full C/D và wheel/runtime được chạy lại; wheel reinstall bằng
  `uv pip install --python .cache/t09-wheel-runtime/Scripts/python.exe --no-index --no-deps --reinstall dist/telebotshop-0.1.0-py3-none-any.whl`.
  Installed package và wheel đều so byte-for-byte với 84 source modules của working tree.

  Cleanup: xác minh Compose project `telebotshop-t02`, service `postgres-test` và workspace
  labels trước lifecycle actions. Sau final D: `test_schemas=0`, `other_connections=0`;
  đã stop đúng service đã start cho T09, không down/remove volume hoặc đóng Docker Desktop.

  Giới hạn: Telegram API/lifecycle mocked, chưa live Stars/key send, native OS signal/load/SLO,
  hosted CI, Linux/macOS/container app, production role separation/webhook/image/deploy/restore.
  Adapter deadline dùng host UTC so với DB lease, production cần clock synchronization. Lease
  không fence được Telegram HTTP bên ngoài DB; unknown send dừng UNCERTAIN, không hứa exactly-once
  network delivery. Alerts hiện attention/audit + sanitized log, staff notifications T14. Chưa
  public Buy/core UI (T14), ba nonfinite/manual types (T10), history/resend (T11), refund/reconcile
  operations (T13); hold chỉ là pre-send boundary, chưa toàn FD-06/refund acceptance.
  **T09 hoàn tất; dừng trước T10 pending, cần được giao riêng.**

- [x] **T10 — File, reusable và manual fulfillment.** Depends: T09. Spec: CI-04, FD-04,06. Scope: từng typed dispatch adapter và manual work completion có permission/audit. Acceptance: mỗi loại giao đúng payload cho đúng buyer; unsupported/corrupt payload fail-closed; MANUAL không giả delivered khi payment tới. Verify: C + D + mocked message/document contracts; tách T10.a/b/c nếu cần, child nào xong không thay thế coverage cả ba loại.

  Owner giao T10 ngày 2026-10-22. Lượt Git đầu thấy HEAD `19e7b53` và T09 staged;
  trước snapshot/edits T10 đã có commit `6582dd0` (T09) bên ngoài agent. Snapshot và cuối
  task đều empty index. Agent giữ work có sẵn, không stage/commit/push. Snapshot để review
  T10 lưu local trong `.cache/t10-baseline/`, không chứa runtime secrets.

  - [x] **T10.a — Encrypted reusable sources và checkout.** Typed file/text/document/manual
    payloads; protected, confirmed resource versions; immutable source snapshot theo order.
    Nối create/pre-checkout/cancel/expiry/payment cho nonfinite types, giữ exact finite
    reservation/allocation invariants. Migration mới, không sửa revisions 0001–0009.
  - [x] **T10.b — Typed worker dispatch.** Gửi registered file_id bằng document, reusable
    text/document và manual waiting notice đúng private buyer; reuse fencing/retry/uncertain
    boundaries. Không fetch URL, không lộ staff instructions, không coi waiting notice là delivered.
  - [x] **T10.c — Manual work và completion.** Protected queue/detail, current permission
    trước decrypt, actor-bound confirmation có bằng chứng, atomic completion/audit; replay,
    revoke, hold, stale/uncertain/concurrent actions fail closed.
  - [x] **T10.d — Review, gates và handoff.** C + D + H, migration preservation/integrity,
    failure/race và Telegram mock contracts, build/runtime smoke, final diff; cập nhật docs
    và báo Owner setup/external gates rõ ràng. Dừng trước T11.

  Threat/abuse focus: cross-buyer entitlement, URL/token-bearing file references, wrong
  type/format/AAD, resource edit sau checkout, forged/replayed manual completion, revoked
  staff, refund hold vs worker/completion, lost commit ACK và ambiguous Telegram result.
  Chỉ fake fixtures + isolated PostgreSQL + mocked Telegram; không chạy bot/payment/refund thật.

  **Implementation/evidence:**

  - `domain/models/resources.py`, resources port/service/repository/model và
    `admin/resources.py`: typed DIGITAL_FILE(file_id/name/MIME), reusable TEXT/DOCUMENT,
    manual notice/private instructions. Current inventory.manage, key-verifier guard,
    actor/product/expected-source/10-minute confirmation; immutable encrypted source
    versions, một ACTIVE/product, giữ RETIRED cho old entitlement. `/resource` đăng ký
    document reference hoặc bounded text; không download/fetch URL hoặc echo private material.
  - Orders/pre-checkout/payments thêm nonfinite binding, không giả inventory unit. Cùng
    UoW lưu order/item/source snapshot/invoice/audit; pre-checkout giữ buyer/XTR/giá/TTL/
    active-product/query-id checks. Receipt/PAID/primary work atomic; nguồn đã chốt không
    đổi vì publication mới hoặc catalog edit. Finite reserve/allocation/release giữ nguyên.
  - Fulfillment/domain/SQL reader + sender mở file/reusable/manual. Authorized ciphertext
    load trước native open; wrong metadata/format/AAD/envelope fail closed, giữ paid binding.
    Document dùng sendDocument, ACK thiếu document/sai chat về UNCERTAIN. Manual sender chỉ
    nhận ManualNotice; waiting notice thành công giữ order FULFILLING/work AWAITING_MANUAL.
    Reuse attempts/fence/lease/retry/hold/recovery/late observations của T09.
  - `domain/models/manual.py`, manual port/service/repository/model và `admin/manual.py`:
    queue/detail, permission-before-decrypt, read audit; `fulfillment.manage` ceiling cho
    OWNER/ADMIN, ADMIN cần explicit grant mới. `/manual_complete WORK_ID` + nonempty evidence
    ≤500 UTF-8 bytes tạo actor/work/attempt-bound confirmation. Evidence có namespace/AAD
    riêng, preview/log/audit không echo reference. Current grant/hold/state/expiry recheck;
    action APPLIED + work SENT + order DELIVERED + audit digest atomic. Handler/service đều
    giữ quyền đúng boundary; không network trong UoW. Bootstrap wire routers/services/cipher.
  - `0010_resources`: ba tables resource_versions/order_resources/manual_actions, nullable
    unit chỉ cho nonfinite, source/item composite FK + XOR, resource/history/attempt guards,
    manual atomic completion và new permission seed, không backfill user grants. Native
    upgrade từ bảy T09 work states giữ toàn old columns/rows/receipt/ciphertext; metadata
    không drift. Safe downgrade giữ finite history, từ chối xóa resource/manual/grant history.
    Bốn frozen predecessor functions khớp T08/T09 sau normalize SQL whitespace.

  **TDD và verification thực tế (CPython 3.12.15, PostgreSQL test 18.6):**

  | Command/check | Result |
  | --- | --- |
  | Focused RED → GREEN | New resource/manual module/router contracts fail trước implementation; wrong typed document đã không bị chặn, missing-document ACK đã bị nhận SENT, corrupt-envelope load đã propagate thay vì durable failure; thêm tests rồi sửa và chạy green. |
  | `uv lock --check`; `uv sync --locked --no-build` | Exit 0; lock/dependency versions giữ nguyên. |
  | `uv run --no-sync ruff check .`; `uv run --no-sync ruff format --check .` | Exit 0; 225 Python files formatted. |
  | `uv run --no-sync python scripts/check_architecture.py` | Exit 0; import matrix đạt. |
  | `uv run --no-sync pytest tests/unit tests/contract tests/architecture -q --tb=short --junitxml=.cache/t10-c.xml` | **445 passed**, 24.40s; zero errors/failures/skips. |
  | `APP_ENV=test`, exact isolated `TEST_DATABASE_URL`; `uv run --no-sync pytest tests/integration -q --tb=short --junitxml=.cache/t10-d.xml` | **301 passed**; retained JUnit: 301 cases, zero errors/failures/skips, 272.883s. PostgreSQL thật, mỗi test schema có ownership marker. |
  | `python scripts/verify_harness.py`; `python -B -m unittest discover -s tests -p test_harness.py -v` | Exit 0; original skills/references/license/provenance giữ nguyên, **6 harness tests passed**. |
  | `uv build --no-sources` | Exit 0; sdist + wheel; mọi 96 Python modules khớp source, không cache/tools/tests/env secrets trong artifact. |
  | README runtime export + fresh `.cache/t10-wheel-runtime` | Locked hash-checked 37 runtime dependencies cài offline/no-build, wheel no-index/no-deps; `python -I` import **96 modules** với poisoned settings/no network/no pytest; native AES-GCM roundtrip/wrong-AAD đạt cho 7 typed contexts. |
  | `uv audit --locked` | Exit 0 sau read-only network access; **49 packages, no known vulnerabilities/adverse statuses** tại lúc chạy, không phải whole-release security claim. |
  | Independent read-only review | Không required finding; kiểm tra source/payment ownership, permission/decrypt, typed sender, manual/hold/fence và migration predecessor equivalence. Không sửa snapshot để review. |
  | Mutation probes trong isolated source copy | Green control **10 passed**. Bỏ resource UUID AAD, bỏ manual hold check, không cho retired snapshot, hoặc đánh manual delivered ngay khi notice SENT đều bị focused tests bắt; **4 probes**, zero fixture errors. Copy được restore; source chính không đổi. |
  | `git diff --check`; UTF-8/local links/fences/protected/index/checklist | PASS; 71 changed/new files (61 Python, 10 Markdown), 50 local links tồn tại; 96 source modules khớp reviewed snapshot, 34 protected baseline files giữ nguyên; empty index, chỉ T01–T10 done, T11–T18 pending. |

  Native failure/race cases gồm source publication-vs-checkout hai physical connections và
  cả hai lock outcomes, competing publication/completion, same-action replay, cross-user/
  revoked grant, stale/cancelled/expired/held action, native crypto tamper, commit/audit rollback,
  lost ACK và immutable SQL history. Real dispatcher + message/document mocks xác nhận
  không DB connection qua Telegram, private staff notes/evidence không gửi buyer hoặc preview.

  Một lượt probe ban đầu gặp fixture timeout vì Docker Engine đã tắt, không tính là mutation
  evidence. Khởi động installed Docker Desktop ở background, verify đúng test service/workspace,
  chạy green control rồi cả bốn probe có assertion failures và zero setup errors. Cleanup query
  xác minh **0 tbs_test schemas, 0 other client connections**, dừng riêng postgres-test;
  không drop dev DB/volume hoặc dừng Docker Desktop toàn máy. Không chạy live Telegram,
  real payment/refund, production migration/deploy, stage/commit/push.

  **Owner setup T10 local: không cần thêm key/provider/server.** Reuse keyring contract hiện
  có với fake fixtures. Trước Telegram external acceptance T14 cần test bot/account, secure
  BOT_TOKEN/OWNER_ID/key environment và inventory/file có quyền phân phối/hướng dẫn staff thật.
  Không gửi secrets trong chat. Staff completion là attestation việc xử lý bên ngoài, chưa có
  kiểm chứng tự động về evidence thực tế. History/resend T11, refund/reconcile T13, operational
  alerts/test-webhook/live acceptance T14, production/restore T18 còn theo task sở hữu.

  **T10 hoàn tất local; dừng trước T11 pending, cần được giao riêng.**

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
