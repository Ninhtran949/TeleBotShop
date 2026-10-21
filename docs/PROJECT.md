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
| Inventory/orders/payments, production webhook/worker, CI, production image/deployment | Chưa triển khai; các phần tương ứng trong cây kiến trúc là cấu trúc đích. |
| BotFather/test bot, live credentials, backup/restore | Chưa cấu hình hoặc kiểm chứng; local PostgreSQL dùng credentials giả tách biệt. |
| Git | Repository branch `dev`; T01 (`ea4882f`) và T02 (`6f21cec`) đã có commit. T03 đang staged; T04 chưa stage/commit/push. Giữ nguyên index/history và work đã có. |

Trạng thái và bằng chứng của từng slice nằm trong [implementation plan](plans/shop/implementation-plan.md). T01–T04 hoàn tất; **T05** là task tiếp theo, còn pending và cần được giao riêng. Không tự chạy toàn roadmap khi chỉ được giao một task.

## Đọc theo nhiệm vụ

| Việc đang làm | Nguồn cần đọc thêm sau AGENTS.md |
| --- | --- |
| Code hoặc sửa behavior | [CONVENTIONS](CONVENTIONS.md), section liên quan trong [ARCHITECTURE](ARCHITECTURE.md), đúng capability spec từ [bản đồ](plans/shop/README.md). |
| Thanh toán/kho/giao hàng/refund | Spec `orders-payments`, `catalog-inventory`, `fulfillment` liên quan; ADR-0002 và ADR-0003. |
| Auth/admin/support | Spec `identity-access` hoặc `admin-support-operations`; không cần đọc growth. |
| Thay đổi nền tảng/ranh giới | ADR liên quan trong [index](adr/README.md), rồi cập nhật hoặc supersede quyết định một cách tường minh. |
| Docs/harness | Đúng tài liệu đang sửa và check dưới đây; không cần tải các skill code/security nếu không liên quan. |

## Stack đã chọn và mức triển khai

Python 3.12, aiogram 3.x, FastAPI, PostgreSQL 18, SQLAlchemy 2.0 Async + asyncpg, Alembic, Pydantic Settings, Pytest, Ruff, Docker Compose. `uv` quản lý môi trường/lock; `cryptography` phục vụ mã hóa inventory khi tới T05. Lý do và giới hạn nằm trong ADR-0001 đến ADR-0004. Hiện pin FastAPI 0.156.4, Pydantic 2.13.5/Settings 2.16.1, SQLAlchemy 2.0.55 Async, asyncpg 0.32.0, Alembic 1.20.0, Uvicorn 0.54.0 và aiogram 3.31.0; dev tests dùng httpx2 2.15.1/Pytest-asyncio 1.4.0. T03 điều chỉnh Pydantic từ 2.14.1 xuống 2.13.5 theo constraint `<2.14` của aiogram và kiểm chứng lại T02 contracts.

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

HTTP không cần bot secrets. Polling cần BOT_TOKEN/OWNER_ID trong environment bảo vệ, chỉ
development/test, từ chối existing webhook; hướng dẫn trong README. Không chạy command
polling thật từ việc T03 được giao code/test.

T04 verification: **211 unit/contract/architecture**, **60 PostgreSQL integration**, **6 harness**
pass; lock/sync, lint/format, architecture, build/isolated runtime wheel và dependency audit
pass. Tests bao gồm T03 regressions, catalog năm loại, hidden/public scope, navigation và
tampered callbacks, current permission, concurrent price/confirmation, audit/commit rollback,
T03→T04 migration preservation, schema drift và mọi Telegram send ngoài DB transaction.
Chưa có type checker, hosted CI, Telegram test/live hoặc production validation; scope hiện
có và giới hạn ở [SECURITY](SECURITY.md). Không suy ra inventory/checkout/delivery acceptance
từ catalog hoặc từ passing mocked Telegram tests.

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
