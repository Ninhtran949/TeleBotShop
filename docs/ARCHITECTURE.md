# Kiến trúc TeleBotShop

## Trạng thái và lựa chọn

Đây là baseline thiết kế được SE/SA chọn theo ủy quyền của Owner, chưa có application source hay kiểm chứng runtime. Yêu cầu nghiệp vụ gốc ở [`task.md`](../task.md); lý do chọn kiến trúc ở [ADR-0001](adr/0001-modular-monolith.md). Thiết kế chỉ dựng package/file khi một slice cần dùng, không tạo toàn bộ cây trống.

Chọn **modular monolith**, một codebase/image, PostgreSQL là nguồn dữ liệu bền vững. Production dự kiến có hai process từ cùng image: HTTP ingress và fulfillment worker. Local polling chỉ là adapter development thay thế webhook, không chạy cùng webhook cho cùng bot.

```mermaid
flowchart TD
    B[Telegram handlers / admin] --> A[Application use cases + ports]
    H[FastAPI webhook] --> B
    W[Worker adapter] --> A
    A --> D[Domain models / invariants]
    I[Database / Stars / delivery / security adapters] --> A
    I --> D
    C[Composition root] --> B
    C --> H
    C --> W
    C --> I
```

Mũi tên biểu diễn phụ thuộc code. Application gọi external effects qua port được inject; implementation của port thuộc infrastructure. Không có service locator, mediator/event bus, generic repository, base-service hierarchy hay DI framework.

## Cấu trúc đích

```text
src/telebotshop/
  __main__.py                 # entrypoint chọn ingress / worker / dev polling
  bootstrap.py                # composition root; tạo và đóng resources
  domain/
    models/                   # identity, catalog, inventory, orders, fulfillment
    errors.py                 # business errors thuần Python
  application/
    services/                 # use cases theo nghiệp vụ, không chứa Telegram handlers
    ports/                    # Protocol tối thiểu cho persistence và external effects
  bot/
    routers/                  # start, catalog, checkout, orders, support
    keyboards/                # callback/keyboard builders; không truy cập DB
    texts/                    # nội dung tiếng Việt, tổ chức để thêm locale sau
  admin/                      # Telegram admin routers/keyboards; không phải business layer
  api/                        # FastAPI factory, webhook, liveness/readiness
  workers/                    # vòng chạy/claim jobs; gọi use cases
  infrastructure/
    config.py                 # Pydantic Settings; load ở bootstrap, không import-time I/O
    database/
      models/                 # SQLAlchemy models; tách khỏi domain
      repositories/           # SQL/query implementations
      uow.py                  # AsyncSession/transaction implementation
    payments/                 # Telegram Stars adapter
    delivery/                 # send message/document adapters
    security/                 # encryption/key loading và adapter controls
migrations/                   # Alembic; thêm dần cùng slice cần schema
tests/
  unit/
  integration/                # PostgreSQL thực, kể cả race/migration cases
  contract/                   # Telegram/webhook boundary, external API mocked
  architecture/               # kiểm tra import direction bằng AST
```

Hàm/DTO đặt cạnh use case sở hữu; chỉ tách module chung khi có nhu cầu thật. Không tạo sẵn file cho mọi item trong cây. Mapping capability → trách nhiệm nằm trong [spec index](plans/shop/README.md), không sinh microservice cho từng capability.

## Ma trận import bắt buộc

| Nơi import | Được phụ thuộc | Không được phụ thuộc |
| --- | --- | --- |
| `domain` | Python standard library | application, adapters, Pydantic, ORM, aiogram/FastAPI, env/config |
| `application` | domain, application-owned DTO/ports, standard library | infrastructure, bot/admin/api/workers, ORM/session, Telegram SDK |
| `bot`, `admin`, `api`, `workers` | application/domain contracts, SDK phục vụ transport của mình | concrete repositories, direct SQL, crypto key material, gọi transport khác để lấy business logic |
| `infrastructure` | application ports, domain contracts, external libraries | import handlers/use cases để điều khiển nghiệp vụ ngược chiều |
| `bootstrap`, entrypoints | Các adapter và use case cần wiring | Không chứa quy tắc tiền/kho/quyền |

FastAPI route nhận một dispatcher/handler đã wire từ bootstrap; không tự dựng Bot/repository/engine. Worker adapter không import HTTP routes. Repository không trả ORM instance ra khỏi infrastructure; query results chỉ chứa trường cần cho use case và không tự kèm secrets.

## Transaction và ranh giới side effect

1. Use case nhận actor/context và dữ liệu đã parse; vẫn kiểm tra quyền, ownership, trạng thái và invariant từ dữ liệu server.
2. Use case mở UoW qua application port. Một instance AsyncSession cho một transaction; rollback khi lỗi. Repository không `commit()` hoặc tự mở transaction ẩn.
3. Ghi business state, audit quan trọng và work bền vững liên quan trong cùng transaction. Constraint/row lock ở PostgreSQL bảo vệ bất biến, không dựa vào `asyncio.Lock` hay check-then-write.
4. Commit rồi mới gọi Telegram. Không giữ DB connection/lock xuyên qua network I/O; không che lỗi commit bằng thông báo thành công.
5. Persist kết quả side effect bằng transaction mới, với attempt/lease token để chặn stale worker. Thất bại không rõ kết quả được lưu `UNCERTAIN`, không tự làm lại financial operation.

Các row cùng loại được lock theo thứ tự ID ổn định. Những flow sửa cùng order phải lock order trước inventory/work; transaction claim job riêng phải commit trước khi flow lấy order lock. Lock timeout/deadlock là lỗi retry được khi transaction chắc chắn rollback, không đồng nghĩa được retry external effect.

Một transaction có thể chạm order/payment/inventory/work để giữ invariant; không tách thành nhiều commit chỉ để bảo toàn cách chia folder. Port dùng `typing.Protocol` theo nhu cầu thật; không tạo CRUD interface cho mọi table hoặc aggregate.

Theo [SQLAlchemy](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#using-asyncsession-with-concurrent-tasks), AsyncSession không dùng chung cho các concurrent task. [PostgreSQL row locks](https://www.postgresql.org/docs/18/explicit-locking.html#LOCKING-ROWS) là cơ chế nền; isolation level/constraint/query cụ thể phải được test bằng PostgreSQL.

## Flow nhận payment và giao hàng

Webhook xác minh secret header, giới hạn body, parse Update rồi await xử lý. Với successful payment, chỉ trả 2xx sau khi receipt và kết quả xử lý tối thiểu đã durable; DB lỗi trả non-2xx để Telegram có thể gửi lại. Không dùng FastAPI BackgroundTasks hoặc aiogram background acknowledgement cho đường nhận tiền trước khi lưu bền vững.

Receipt hợp lệ với order → lock/kiểm tra → lưu payment + chuyển reservation thành allocation + tạo `fulfillments` work duy nhất → commit. Receipt không khớp/đến muộn vẫn phải được lưu để xử lý, không tạo hàng sai. Không gọi worker hoặc gửi tài nguyên trong transaction nhận payment. Contract chi tiết ở [orders-payments](plans/shop/orders-payments.md).

Worker poll `fulfillments` trong PostgreSQL; claim ngắn bằng row lock/lease, rồi thực hiện send ngoài transaction. Đây là durable work ledger đủ cho phạm vi hiện tại, không phải event bus tổng quát. State/claim/recovery ở [fulfillment](plans/shop/fulfillment.md) và [ADR-0002](adr/0002-payment-and-fulfillment-safety.md).

Cam kết là **một entitlement/allocation cho mỗi order item**. Gửi message qua mạng và commit DB không phải một atomic operation; hệ thống không tuyên bố exactly-once transport delivery. Lịch sử/resend dùng cùng entitlement, không cấp key mới.

## Dữ liệu và vận hành

- UUID nội bộ, Telegram IDs là integer/BIGINT, timestamps UTC có timezone, tiền Stars là integer dương. Trạng thái order không thay thế payment/refund ledger.
- PostgreSQL giữ users/permissions, catalog/inventory, orders/items/payments/refunds/fulfillments, support/audit và dữ liệu growth khi tới phase đó. Không tạo toàn bộ schema trước các slice.
- Initial ingress chạy một replica/process; rate limit tạm thời trong process phải ghi `shortcut:` và thay bằng shared counters trước khi scale nhiều ingress. Worker có thể cạnh tranh qua PostgreSQL lease đã được test; không dùng Redis để làm nguồn sự thật cho kho/đơn.
- Sensitive inventory và delivery payload mã hóa theo [ADR-0003](adr/0003-inventory-secrets.md); authorization trước decrypt. Username, invoice payload và callback data không phải bằng chứng cấp quyền.
- `/health/live` phản ánh process còn chạy; `/health/ready` kiểm tra dependency/schema cần thiết với timeout ngắn. Không dump settings, DSN, user/inventory data vào response.
- Graceful shutdown dừng nhận/claim công việc mới, hoàn tất hoặc để lại attempt có trạng thái recoverable, đóng HTTP clients/DB engine. Không giả mark job delivered khi bị terminate.

## Ranh giới harness

`AGENTS.md` là entrypoint rule; docs là kiến thức được tải theo task. `.agents/` là upstream guidance được pin, không phải application package hay nơi đặt convention riêng. Công cụ harness hiện có không chứng minh app tuân import matrix; T01/T02 phải thêm architecture checks và app quality gates trước các feature.
