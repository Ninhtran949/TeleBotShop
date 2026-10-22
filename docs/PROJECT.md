# TeleBotShop

## Trạng thái hiện tại

Mục tiêu từ [`task.md`](../task.md): shop Telegram bán tài nguyên số được phép phân phối, thanh toán Stars, quản lý kho/đơn và giao hàng an toàn; tiếng Việt mặc định, admin qua Telegram.

| Hạng mục | Trạng thái |
| --- | --- |
| Harness, sáu upstream skills, kiểm tra integrity | Đã có; nguồn và checksum giữ nguyên. |
| Rule, architecture, convention, capability specs, ADR, implementation plan | Đã thiết kế thành baseline kỹ thuật theo yêu cầu Owner giao vai trò SE/SA. |
| T01: toolchain, package, manifest/lock, quality gates | Đã triển khai và kiểm chứng local: CPython 3.12.15, uv/uv_build 0.13.4, Pytest 9.1.1, Ruff 0.17.1; package tối thiểu, AST import checks, wheel/import smoke. |
| T02: settings/runtime, HTTP health, PostgreSQL/UoW, Alembic, dev/test Compose | Đã triển khai và kiểm chứng local trên PostgreSQL 18.6; revision `0001_runtime` giữ nguyên. |
| T03: identity/Owner, explicit permissions, audit và Telegram admin/settings foundation | Đã triển khai và kiểm chứng local: schema head `0003_access`, `/start`/private profile, `/admin`, Owner grant/revoke/settings confirmation và development polling. Telegram API mock; PostgreSQL thật. |
| T04: catalog, public metadata năm loại và admin product maintenance | Đã triển khai và kiểm chứng local: schema head `0004_catalog`, category/subcategory, browse/search/pagination, staff tạo/sửa sản phẩm/giá/trạng thái/ảnh với confirmation, current permission và audit. Chỉ metadata công khai; chưa nhận đơn. |
| T05: encrypted key/license inventory, import/retire/stock/low-stock | Đã triển khai và kiểm chứng local: head `0005_inventory`, AES-GCM/HMAC, versioned key guard, atomic TXT/CSV import, current inventory.manage, durable confirmation/audit và stock pagination. Download/send Telegram mock ngoài transaction, PostgreSQL thật. |
| T06: finite orders/reservations, expiry/cancel, durable invoice intent | Đã triển khai và kiểm chứng local: head `0006_orders`, buyer/action idempotency, immutable quantity-one price snapshot, 15-minute DB TTL, guarded release, DB-only maintenance và mocked Stars invoice outcomes. |
| T07: pre-checkout validation/serialized approval và bounded answer | Đã triển khai và kiểm chứng local: head `0007_pre_checkout`, one immutable approval/order/query, atomic TTL extension/audit, exact reservation/current product/owner/price checks, deadline/uncertainty/races. T08 nối checker thật cùng receipt intake; không có live payment validation. |
| T08: durable receipt/paid allocation/READY fulfillment intent | Đã triển khai local: head `0008_payments`, charge dedup/conflicts/extra review, PAID + attention khi mất reservation; atomic permanent allocation/work, commit-before-polling-offset, không delivery. |
| T09: finite worker và recovery | Đã triển khai local: head `0009_fulfillment`, fenced attempts, authorized decrypt, separate claim/prepare/complete transactions, known-rate bounded retry, uncertain recovery/observations và durable attention/alerts; Telegram mock. |
| T10: file/reusable/manual fulfillment | Đã triển khai local: head `0010_resources`, confirmed encrypted resource versions, pinned order entitlement, typed document/text/manual notice và current-permission staff completion/evidence/audit; PostgreSQL thật, Telegram mock. |
| T11: history, dashboard và safe resend | Đã triển khai và kiểm chứng local: head `0011_history`, own-customer/scoped-staff history, 30-day real dashboard, shared entitlement reader và durable actor-bound resend; giữ nguyên primary work/allocation, hold/uncertain fences. |
| Public Buy, production webhook, CI, production image/deployment | Chưa triển khai; các phần tương ứng trong cây kiến trúc là cấu trúc đích. |
| BotFather/test bot, live credentials, backup/restore | Chưa cấu hình hoặc kiểm chứng; local PostgreSQL dùng credentials giả tách biệt. |
| Git | Branch `dev`, HEAD `f245807` (T10). T10 còn staged ở lượt kiểm tra đầu T11, đã được commit bên ngoài agent trước baseline/edits T11; snapshot index trống. T11 working tree; agent không stage/commit/push/discard work. |

Trạng thái và bằng chứng của từng slice nằm trong [implementation plan](plans/shop/implementation-plan.md). T01–T11 hoàn tất local; **T12** là task tiếp theo, còn pending và cần được giao riêng. Không tự chạy toàn roadmap khi chỉ được giao một task.

## Đọc theo nhiệm vụ

| Việc đang làm | Nguồn cần đọc thêm sau AGENTS.md |
| --- | --- |
| Code hoặc sửa behavior | [CONVENTIONS](CONVENTIONS.md), section liên quan trong [ARCHITECTURE](ARCHITECTURE.md), đúng capability spec từ [bản đồ](plans/shop/README.md). |
| Thanh toán/kho/giao hàng/refund | Spec `orders-payments`, `catalog-inventory`, `fulfillment` liên quan; ADR-0002 và ADR-0003. |
| Auth/admin/support | Spec `identity-access` hoặc `admin-support-operations`; không cần đọc growth. |
| Thay đổi nền tảng/ranh giới | ADR liên quan trong [index](adr/README.md), rồi cập nhật hoặc supersede quyết định một cách tường minh. |
| Docs/harness | Đúng tài liệu đang sửa và check dưới đây; không cần tải các skill code/security nếu không liên quan. |

## Stack đã chọn và mức triển khai

Python 3.12, aiogram 3.x, FastAPI, PostgreSQL 18, SQLAlchemy 2.0 Async + asyncpg, Alembic, Pydantic Settings, Pytest, Ruff, Docker Compose. `uv` quản lý môi trường/lock; T05 pin `cryptography 50.0.2` cho AES-GCM; lock thêm cffi 2.1.1/pycparser 3.11, giữ nguyên mọi locked version cũ. Lý do và giới hạn nằm trong ADR-0001 đến ADR-0004. Hiện pin FastAPI 0.156.4, Pydantic 2.13.5/Settings 2.16.1, SQLAlchemy 2.0.55 Async, asyncpg 0.32.0, Alembic 1.20.0, Uvicorn 0.54.0 và aiogram 3.31.0; dev tests dùng httpx2 2.15.1/Pytest-asyncio 1.4.0. T03 điều chỉnh Pydantic từ 2.14.1 xuống 2.13.5 theo constraint `<2.14` của aiogram và kiểm chứng lại T02 contracts.

Python/uv/dev/build versions và lockfile đã pin trong T01; runtime/development packages cập nhật ở T02. Compose pin official PostgreSQL 18.6-bookworm bằng immutable digest; đã chạy với Docker Linux Engine 29.6.2/Compose 5.3.1. Xem [README](../README.md) để bootstrap/start và [DATABASE](DATABASE.md) cho DB/migrations/isolation. Redis chỉ thêm trước khi cần chia sẻ rate limit/FSM giữa nhiều ingress; chưa có React, Mini App, Celery, microservice hoặc plugin bổ sung.

Harness dùng Python standard library 3.11+ và đã kiểm tra độc lập bằng Python hệ thống 3.11.9. App gates dùng CPython 3.12.15 trong `.venv`; không thay Python hệ thống.

## Các lệnh hiện chạy được

Chạy từ workspace root. Bootstrap uv/Python và cấu hình cache theo [README](../README.md), rồi chạy C gates:

```text
uv lock --check
uv sync --locked --no-build
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pytest tests/unit tests/contract tests/architecture
uv run --no-sync python scripts/check_architecture.py
uv build --no-sources
```

Runtime và migration commands đã tồn tại (export environment trước, theo README):

```text
uv run --no-sync alembic upgrade head
uv run --no-sync telebotshop
uv run --no-sync telebotshop polling
uv run --no-sync telebotshop expire-orders
uv run --no-sync telebotshop worker
```

PostgreSQL gate D, sau khi start disposable test service và export `APP_ENV=test`/`TEST_DATABASE_URL` theo README:

```text
uv run --no-sync pytest tests/integration
```

Wheel/runtime import smoke trong môi trường riêng và locked dependency audit: [README](../README.md#kiểm-tra). Harness tiếp tục chạy riêng, không cần uv/app dependencies:

```text
python scripts/verify_harness.py
python -B -m unittest discover -s tests -p test_harness.py -v
```

HTTP không cần bot secrets. Polling cần BOT_TOKEN/OWNER_ID và inventory key settings trong environment bảo vệ, chỉ
development/test, từ chối existing webhook; hướng dẫn trong README. Không chạy command
polling thật từ các task code/test T03–T11. Worker/resend gửi entitlement/customer notice nên chỉ operator chạy khi có authorization gửi thực tế; tests mock. `expire-orders` chạy một batch DB-only,
không Bot/keys; hướng dẫn và giới hạn tại README/DATABASE.

T09 verification: **402 unit/contract/architecture**, **238 PostgreSQL integration**,
**6 harness** pass; lock/sync/lint/format/architecture/build/isolated runtime wheel và dependency
audit pass. Wheel có 84 source modules khớp working tree; poisoned settings/no network/no pytest
và native AES-GCM pass. Tests giữ regressions T01–T08, thêm two-physical-worker SKIP LOCKED,
both hold-vs-prepare order-lock outcomes, stale/expired claim, pre-send budget/type/ownership,
typed key/license, bounded retry-after, late observations, commit failures/lost ACK/cancellation,
immutable history và T08 preservation/downgrade/metadata. Bốn mutation probes token/hold/recovery/
late-result bị bắt; source restored trước final C/D. Runtime/lifecycle và Telegram API mocked;
PostgreSQL thật, không suy live message hoặc production SLO từ local tests.
Chưa public Buy/core UI (T14), history/resend (T11), refund/reconcile
(T13), production webhook/deploy/restore hoặc live Telegram validation. Alerts hiện durable
attention/audit/log; staff notification routing T14. Chi tiết ở [SECURITY](SECURITY.md) và ledger.

T10 verification: **445 unit/contract/architecture**, **301 PostgreSQL integration**,
**6 harness** pass. Lock/sync/lint/format/architecture/build, fresh isolated wheel runtime
(96 matching source modules, poisoned settings/no network/no pytest, native AES-GCM) và
native locked audit 49 packages pass. Independent read-only review không có required finding;
bốn mutation probes có green control, không sửa source chính, đều bị focused tests bắt đúng.
Schema 0010 giữ T09 paid/attempt history ở bảy states; source publication/checkout và manual
hold/completion dùng hai physical connections; data/attempt/audit rollback/lost-ACK và typed
message/document contracts có tests. Đây là local/mock evidence, chưa live Telegram.

**Owner setup T10 local: không cần thêm key/provider/server.** Dùng keyring contract hiện có
với fake fixtures. Trước external acceptance T14 cần test bot/account + secure environment,
inventory/file có quyền phân phối và hướng dẫn thủ công thật. Staff attestation về manual
delivery không được bot tự xác minh. Refund/live và production còn task sở hữu.

T11 verification: **453 unit/contract/architecture**, **338 PostgreSQL integration** và
**6 harness** pass. Lock/sync/lint/format/architecture/build/native audit (49 packages) đạt.
Fresh runtime wheel gồm **104 matching modules**, no pytest/network, poisoned settings và
native AES-GCM pass. Read-only independent review không required finding; isolated mutation
bỏ buyer SQL filter bị test bắt (green control, zero fixture errors, source chính giữ nguyên).
Native two-connection hold/resend/duplicate confirmation, migration preservation/downgrade,
revoke/IDOR/commit/lost ACK/expiry/corrupt material và real dispatcher mocks có coverage.

**Owner setup T11 local: không cần thêm key/provider/server.** Reuse keyring + fake fixtures;
Telegram/live credentials không dùng. T12 cần Owner chốt support identity/terms trước chạy
thật, tests có thể dùng nội dung giả rõ ràng. T13 mới có refund ledger/reconciliation:
REFUNDED test hiện là injected repository DTO, chưa native refund-ledger acceptance.

## Điều kiện cần Owner cung cấp trước khi mở bán

Inventory có quyền phân phối; thông tin shop/support; nội dung điều khoản, chính sách refund và retention được Owner chốt; cấu hình môi trường test/production tách biệt. Token, OWNER_ID, webhook secret và encryption keys đưa qua môi trường an toàn khi thực sự cần, không gửi vào chat. Thiếu các đầu vào này không chặn việc viết unit/integration tests với dữ liệu giả, nhưng chặn nghiệm thu external/live tương ứng.

## Skill provenance and reproduction

Six original skills from [Addy Osmani's Agent Skills](https://github.com/addyosmani/agent-skills) are vendored without changes. [`.agents/upstream.json`](../.agents/upstream.json) pins the exact commit, archive URL and SHA-256, selected names, and file SHA-256 values. The original MIT license is [`.agents/LICENSE`](../.agents/LICENSE).

The source layout is preserved under `.agents/`: upstream `skills/` maps to `.agents/skills/`, and upstream shared `references/` maps to `.agents/references/`. The security skill also retains its own `references/` directory. `.gitattributes` prevents Git newline conversion of these files.

To reproduce the vendor subset, download the commit-pinned archive from the manifest, verify its SHA-256, and copy only the six listed skill directories, the complete shared `references/` directory, and `LICENSE` into those locations. Copy bytes without executing archive contents, scripts, hooks, or installers. Run the checks above; optionally compare against the downloaded archive:

```text
python scripts/verify_harness.py --archive <path-to-pinned.tar.gz>
```

Codex [discovers repository skills natively](https://learn.chatgpt.com/docs/build-skills) from `.agents/skills/`; no router, plugin configuration, or global configuration change is needed. Launch from this workspace root. If skills do not appear after a change, restart Codex. User or plugin skills with the same names can coexist; inspect their paths before choosing one.

Nguồn chuẩn hiện tại là [ARCHITECTURE](ARCHITECTURE.md), [CONVENTIONS](CONVENTIONS.md), [ADR index](adr/README.md), và [shop specs/plan](plans/shop/README.md). [Báo cáo setup harness](plans/engineering-harness.md) là bằng chứng lịch sử, không phải tài liệu khởi động bắt buộc.
