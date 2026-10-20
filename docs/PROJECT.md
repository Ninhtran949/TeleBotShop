# TeleBotShop

## Trạng thái hiện tại

Mục tiêu từ [`task.md`](../task.md): shop Telegram bán tài nguyên số được phép phân phối, thanh toán Stars, quản lý kho/đơn và giao hàng an toàn; tiếng Việt mặc định, admin qua Telegram.

| Hạng mục | Trạng thái |
| --- | --- |
| Harness, sáu upstream skills, kiểm tra integrity | Đã có; nguồn và checksum giữ nguyên. |
| Rule, architecture, convention, capability specs, ADR, implementation plan | Đã thiết kế thành baseline kỹ thuật theo yêu cầu Owner giao vai trò SE/SA. |
| Application, dependency manifest/lock, database, migrations, CI | Chưa triển khai. Cây `src/` trong tài liệu là cấu trúc đích. |
| BotFather, test bot, webhook, PostgreSQL, credentials, backup/restore | Chưa cấu hình hoặc kiểm chứng. |
| Git | Workspace chưa là Git repository; không tự `git init`, commit hoặc push. |

Lượt thiết kế chỉ tạo/sửa tài liệu. Trạng thái thực thi của từng slice nằm trong [implementation plan](plans/shop/implementation-plan.md); tất cả task ứng dụng bắt đầu ở `pending`. Việc tiếp theo khi được giao triển khai là **T01**; không tự chạy toàn roadmap khi chỉ được giao một task.

## Đọc theo nhiệm vụ

| Việc đang làm | Nguồn cần đọc thêm sau AGENTS.md |
| --- | --- |
| Code hoặc sửa behavior | [CONVENTIONS](CONVENTIONS.md), section liên quan trong [ARCHITECTURE](ARCHITECTURE.md), đúng capability spec từ [bản đồ](plans/shop/README.md). |
| Thanh toán/kho/giao hàng/refund | Spec `orders-payments`, `catalog-inventory`, `fulfillment` liên quan; ADR-0002 và ADR-0003. |
| Auth/admin/support | Spec `identity-access` hoặc `admin-support-operations`; không cần đọc growth. |
| Thay đổi nền tảng/ranh giới | ADR liên quan trong [index](adr/README.md), rồi cập nhật hoặc supersede quyết định một cách tường minh. |
| Docs/harness | Đúng tài liệu đang sửa và check dưới đây; không cần tải các skill code/security nếu không liên quan. |

## Stack đã chọn cho triển khai, chưa cài

Python 3.12, aiogram 3.x, FastAPI, PostgreSQL 18, SQLAlchemy 2.0 Async + asyncpg, Alembic, Pydantic Settings, Pytest, Ruff, Docker Compose. `uv` quản lý môi trường/lock; `cryptography` phục vụ mã hóa inventory. Lý do và giới hạn nằm trong ADR-0001 đến ADR-0004. Đây là quyết định thiết kế mới, không phải mô tả package đã tồn tại hoặc đã test tương thích.

Chốt patch versions, Python/uv/container versions và lockfile trong T01/T02 sau khi kiểm tra hỗ trợ thực tế. Redis chỉ thêm trước khi cần chia sẻ rate limit/FSM giữa nhiều ingress; chưa có React, Mini App, Celery, microservice hoặc plugin bổ sung.

Harness dùng Python standard library 3.11+; Python 3.11.9 của phiên setup chỉ xác minh harness, không đáp ứng baseline runtime ứng dụng 3.12.

## Các lệnh hiện chạy được

Run from the workspace root:

```text
python scripts/verify_harness.py
python -B -m unittest discover -s tests -p test_harness.py -v
```

Các lệnh này chỉ kiểm tra harness. Gate dự kiến cho app được ghi riêng trong CONVENTIONS và chỉ dùng sau T01/T02; hiện chưa có app build/lint/type-check/migration/test suite để tuyên bố pass.

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
