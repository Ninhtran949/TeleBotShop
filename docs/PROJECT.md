# TeleBotShop

## Trạng thái hiện tại

Mục tiêu từ [`task.md`](../task.md): shop Telegram bán tài nguyên số được phép phân phối, thanh toán Stars, quản lý kho/đơn và giao hàng an toàn; tiếng Việt mặc định, admin qua Telegram.

| Hạng mục | Trạng thái |
| --- | --- |
| Harness, sáu upstream skills, kiểm tra integrity | Đã có; nguồn và checksum giữ nguyên. |
| Rule, architecture, convention, capability specs, ADR, implementation plan | Đã thiết kế thành baseline kỹ thuật theo yêu cầu Owner giao vai trò SE/SA. |
| T01: toolchain, package, manifest/lock, quality gates | Đã triển khai và kiểm chứng local: CPython 3.12.15, uv/uv_build 0.13.4, Pytest 9.1.1, Ruff 0.17.1; package tối thiểu, AST import checks, wheel/import smoke. |
| Runtime/business flows, database, migrations, Compose, CI | Chưa triển khai. Chỉ có `src/telebotshop/__init__.py`; các lớp còn lại trong cây kiến trúc là cấu trúc đích. |
| BotFather, test bot, webhook, PostgreSQL, credentials, backup/restore | Chưa cấu hình hoặc kiểm chứng. |
| Git | Workspace hiện là Git repository, branch `dev`; sạch khi bắt đầu T01. Chưa commit hoặc push thay đổi T01. |

Trạng thái và bằng chứng của từng slice nằm trong [implementation plan](plans/shop/implementation-plan.md). T01 hoàn tất; **T02** là task tiếp theo, còn pending và cần được giao riêng. Không tự chạy toàn roadmap khi chỉ được giao một task.

## Đọc theo nhiệm vụ

| Việc đang làm | Nguồn cần đọc thêm sau AGENTS.md |
| --- | --- |
| Code hoặc sửa behavior | [CONVENTIONS](CONVENTIONS.md), section liên quan trong [ARCHITECTURE](ARCHITECTURE.md), đúng capability spec từ [bản đồ](plans/shop/README.md). |
| Thanh toán/kho/giao hàng/refund | Spec `orders-payments`, `catalog-inventory`, `fulfillment` liên quan; ADR-0002 và ADR-0003. |
| Auth/admin/support | Spec `identity-access` hoặc `admin-support-operations`; không cần đọc growth. |
| Thay đổi nền tảng/ranh giới | ADR liên quan trong [index](adr/README.md), rồi cập nhật hoặc supersede quyết định một cách tường minh. |
| Docs/harness | Đúng tài liệu đang sửa và check dưới đây; không cần tải các skill code/security nếu không liên quan. |

## Stack đã chọn và mức triển khai

Python 3.12, aiogram 3.x, FastAPI, PostgreSQL 18, SQLAlchemy 2.0 Async + asyncpg, Alembic, Pydantic Settings, Pytest, Ruff, Docker Compose. `uv` quản lý môi trường/lock; `cryptography` phục vụ mã hóa inventory. Lý do và giới hạn nằm trong ADR-0001 đến ADR-0004. T01 mới cài toolchain và dev dependencies; runtime dependencies chưa được thêm trước slice cần dùng, chưa có bằng chứng tương thích toàn stack.

Python/uv/dev/build versions và lockfile đã pin trong T01; xem [README](../README.md) để bootstrap cục bộ. Container versions và runtime dependencies chốt từ T02 sau khi kiểm tra hỗ trợ thực tế. Redis chỉ thêm trước khi cần chia sẻ rate limit/FSM giữa nhiều ingress; chưa có React, Mini App, Celery, microservice hoặc plugin bổ sung.

Harness dùng Python standard library 3.11+ và đã kiểm tra độc lập bằng Python hệ thống 3.11.9. App gates dùng CPython 3.12.15 trong `.venv`; không thay Python hệ thống.

## Các lệnh hiện chạy được

Chạy từ workspace root. Bootstrap uv/Python và cấu hình cache theo [README](../README.md), rồi chạy các gate T01:

```text
uv lock --check
uv sync --locked --no-build
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pytest tests/architecture
uv run --no-sync python scripts/check_architecture.py
uv build --no-sources
```

Wheel/import smoke trong môi trường riêng: [README](../README.md#kiểm-tra). Harness tiếp tục chạy riêng, không cần uv/app dependencies:

```text
python scripts/verify_harness.py
python -B -m unittest discover -s tests -p test_harness.py -v
```

Các architecture self-cases kiểm tra checker và cây source hiện có, chưa chứng minh nghiệp vụ chưa triển khai. Chưa có unit/contract/integration suite, type checker, DB/migration hoặc CI gate; chỉ bổ sung khi task tương ứng cần dùng theo CONVENTIONS. Local build/import không phải runtime hoặc Telegram validation.

## Điều kiện cần Owner cung cấp trước khi mở bán

Inventory có quyền phân phối; thông tin shop/support; nội dung điều khoản, chính sách refund và retention được Owner chốt; cấu hình môi trường test/production tách biệt. Token, OWNER_ID, webhook secret và encryption keys đưa qua môi trường an toàn khi thực sự cần, không gửi vào chat. Thiếu các đầu vào này không chặn việc viết unit/integration tests với dữ liệu giả, nhưng chặn nghiệm thu external/live tương ứng.

## Skill provenance and reproduction

Six original skills from [Addy Osmani's Agent Skills](https://github.com/addyosmani/agent-skills) are vendored without changes. [`.agents/upstream.json`](../.agents/upstream.json) pins the exact commit, archive URL and SHA-256, selected names, and file SHA-256 values. The original MIT license is [`.agents/LICENSE`](../.agents/LICENSE).

The source layout is preserved under `.agents/`: upstream `skills/` maps to `.agents/skills/`, and upstream shared `references/` maps to `.agents/references/`. The security skill also retains its own `references/` directory. `.gitattributes` prevents Git newline conversion of these files.

To reproduce the vendor subset, download the commit-pinned archive from the manifest, verify its SHA-256, and copy only the six listed skill directories, the complete shared `references/` directory, and `LICENSE` into those locations. Copy bytes without executing archive contents, scripts, hooks, or installers. Run the checks above; optionally compare against the downloaded archive:

```text
python scripts/verify_harness.py --archive <path-to-pinned.tar.gz>
```

Codex [discovers repository skills natively](https://learn.chatgpt.com/docs/build-skills) from `.agents/skills/`; no router, plugin configuration, or global configuration change is needed. Launch from this workspace root while Git is absent. If skills do not appear after a change, restart Codex. User or plugin skills with the same names can coexist; inspect their paths before choosing one.

Nguồn chuẩn hiện tại là [ARCHITECTURE](ARCHITECTURE.md), [CONVENTIONS](CONVENTIONS.md), [ADR index](adr/README.md), và [shop specs/plan](plans/shop/README.md). [Báo cáo setup harness](plans/engineering-harness.md) là bằng chứng lịch sử, không phải tài liệu khởi động bắt buộc.
