# Database và transaction baseline

T02 dùng PostgreSQL **18.6**, SQLAlchemy **2.0.55** Async + asyncpg **0.32.0** và
Alembic **1.20.0**. T03 thêm identity/access/settings và audit repositories bằng SQLAlchemy
Core tables; không trả ORM/session ra application. Head hiện tại **`0004_catalog`**;
`0001_runtime` giữ nguyên, `0002_identity` thêm identity/Owner/audit, `0003_access` thêm
explicit grants, settings và confirmation. T04 thêm `0004_catalog`; chưa tạo orders hoặc kho.

| Tables | Contract |
| --- | --- |
| `users` | UUID nội bộ; positive BIGINT Telegram ID unique, optional profile metadata; UTC registration/last seen và access version. Concurrent upsert không đổi UUID/registration. |
| `roles`, `user_roles` | OWNER/ADMIN/SUPPORT; một staff role/user, partial unique index chỉ một Owner. Customer không tự có staff role. |
| `permissions`, `role_permissions`, `user_permissions` | Enum permission và role ceilings; ADMIN/SUPPORT cần explicit user grants. Seed Owner permissions không bị bootstrap ghi lại. |
| `shop_settings` | Singleton `id=1`; persisted Owner UUID, optional support username và version. Không có secrets; Owner UUID vẫn còn khi role bị xóa. |
| `admin_actions` | UUID confirmation, actor/target, typed action, expected version, expiry, pending/applied/cancelled. FK/check constraints chặn unknown permissions/shape. |
| `audit_logs` | Actor/action/target/correlation/outcome/UTC time và redacted changes; unique correlation, DB trigger chặn UPDATE/DELETE/TRUNCATE. |
| `categories` | UUID/code unique, public name, optional parent FK; trigger giữ code/parent immutable và chỉ cho parent là root. |
| `products` | UUID/SKU unique immutable, type immutable, category FK, positive BIGINT Stars, public name/description/typed JSONB, HIDDEN/ACTIVE, optional public photo ID và version. |
| `catalog_actions` | Catalog-owned pending intent; actor FK, kind, preallocated target UUID, bounded typed public JSONB, expected version, expiry/status/completed_at. Create target chưa tồn tại nên target không có FK. |

## Local development và test

`docker-compose.yml` pin official image `postgres:18.6-bookworm` bằng manifest digest
`sha256:afc7e2d441324c0388fa80c3d24f733b4194a4eb7f47dd8ee2b08eb1a24a647c`.
Đã kiểm chứng bằng Docker Linux Engine 29.6.2, Compose 5.3.1 trên Windows x86_64.

| Profile / service | Database và role | Host port | Storage |
| --- | --- | --- | --- |
| `dev` / `postgres` | `telebotshop_dev` | `127.0.0.1:55432` | Named volume `postgres-dev`, giữ dữ liệu local qua restart. |
| `test` / `postgres-test` | `telebotshop_test` | `127.0.0.1:55433` | tmpfs, disposable; init SQL đặt database marker. |

Credentials trong Compose/`.env.example` là dữ liệu giả chỉ cho local, không dùng production.
Không bind DB ra public interface. Volume PostgreSQL 18 mount vào `/var/lib/postgresql`,
không dùng đường dẫn volume của PostgreSQL 17 trở xuống.
Hướng dẫn start/export environment ở [README](../README.md).

Settings yêu cầu asyncpg URL có host/database/credentials và port hợp lệ nếu chỉ định.
DSN query chỉ cho `ssl` với một trong các mode `disable`, `allow`, `prefer`, `require`,
`verify-ca`, `verify-full`; unknown/duplicate/invalid options fail tại startup, không để
driver TypeError thoát thành health 500. TLS connection thực tế chưa là acceptance local;
chỉ mở thêm driver options khi integration cụ thể cần và đã có validation/tests.

## Chốt an toàn của integration fixtures

Fixtures yêu cầu `APP_ENV=test` và `TEST_DATABASE_URL` tường minh, không fallback sang
`DATABASE_URL`. Allowlist chính xác driver `postgresql+asyncpg`, host `127.0.0.1`, port 55433,
database/role `telebotshop_test`, có password và không có query override.

Trước writes, fixtures đọc identity, major version 18 và database comment
`telebotshop:isolated-tests:v1`. Marker này do service init SQL cấp từ ngoài fixture;
fixture không tự đánh dấu một database chưa xác minh thành test database.
Database comment được đọc bằng `pg_catalog.shobj_description`, vì database là shared object.

Mỗi test có engine/event loop riêng và schema `tbs_test_<uuid>`, kèm run marker ngẫu nhiên.
Search path chỉ trỏ schema đó. Cleanup đóng pool trước, kiểm tra lại database identity,
schema owner và chính marker của run rồi mới drop đúng schema. Sai marker/owner/allowlist
thì từ chối cleanup; không truncate public schema hoặc drop database.
Unit tests kiểm tra URL refusal; PostgreSQL tests kiểm tra marker/refusal và migration thật.

## Migrations

Chạy từ repository root, sau khi đã export đúng `APP_ENV`/`DATABASE_URL`:

```text
uv run --no-sync alembic heads
uv run --no-sync alembic upgrade head
uv run --no-sync alembic current
```

Migration là thao tác operator riêng, không chạy khi import/startup/request.
CLI dùng validated settings và NullPool, đóng engine cả khi lỗi.
Integration tests cấp explicit connection trong schema do test sở hữu.
Chỉ hỗ trợ online migration; `--sql` chưa hỗ trợ. Metadata đã wire vào Alembic;
`alembic check` kiểm tra drift, autogenerate vẫn phải review trước khi dùng. PostgreSQL tests
so sánh metadata/head, upgrade từ T02 và giữ profile/Owner khi đi từ `0002_identity` lên head.

Readiness so sánh revision trong DB với `SCHEMA_HEAD` ở
`src/telebotshop/infrastructure/database/schema.py`. Architecture test so sánh constant này
với Alembic head để bắt drift. Khi thêm schema ở slice sau, cập nhật migration, ORM metadata,
revision contract và acceptance tests cùng nhau. Không sửa migration đã được dùng.
Chưa có production migration, destructive upgrade, backup/restore hoặc release acceptance.

## Unit of work

Application UoW port có `identity`, `access` repositories và `commit()`;
concrete UoW và AsyncSession ở infrastructure.
`unit_of_work(engine)` tạo session/transaction mới cho mỗi lần gọi, không share session
giữa concurrent tasks. Use case phải gọi `commit()` tường minh. Session close rollback
mọi phần chưa commit, kể cả exception, cancelled task hoặc deferred constraint failure.
`autobegin=False` chặn query tự mở transaction khác sau commit.

Composition root inject UoW factory vào IdentityService/AccessService;
repository chỉ dùng session trong transaction đó và không commit hoặc trả ORM ra ngoài.
PostgreSQL tests đã chứng minh hai backend connections độc lập, commit/rollback/cancel,
commit failure và không có hidden commit.

Owner bootstrap khóa singleton `shop_settings FOR UPDATE`; chỉ lần đầu ghi Owner role,
persisted owner và audit trong cùng transaction. Các mutation quyền/settings khóa singleton
exclusive trước khi đọc current permissions. `require_permission(work, ...)` khóa SHARE
và giữ khóa đến hết transaction của use case để revoke không chen giữa check và mutation.
Đây là lock bảo thủ cho quy mô ban đầu; thu hẹp khi có contention thực tế, không thay bằng
cached role. Public `authorize()` là read use case, không cấp quyền cho transaction khác.

Confirmation giữ target access version hoặc settings version, hết hạn sau 10 phút. Confirm
kiểm tra lại actor/permission/version rồi mutation + audit + mark applied trong một commit.
Concurrent/replayed confirm chỉ áp dụng một lần; cancel làm token không còn dùng được.
Lỗi audit ngay khi INSERT hoặc tại deferred commit đều rollback mọi thay đổi.
Không Telegram/network trong UoW; integration test kiểm tra pool không có checked-out
connection tại mọi Telegram call của flow identity/admin. Retention của confirmations/audit
và production DB role separation chưa chốt ở T03; xem [SECURITY](SECURITY.md).

T04 catalog mutations cũng gọi `require_permission` với `catalog.manage` trong chính UoW.
Catalog action khóa `FOR UPDATE`; product update khóa đúng product row, kiểm tra version
và dùng CAS `WHERE version = expected_version`. Không khóa category join ngoài nhu cầu.
SKU/category creation dùng named unique constraints + `ON CONFLICT DO NOTHING`; race
không overwrite row đã có. Mutation + redacted audit + applied marker commit cùng nhau;
price audit ghi amount trước/sau và version, không copy mô tả/ảnh/payload.

Public query chọn rõ các cột public, chỉ ACTIVE, limit 6 để trả 5 items + has_more;
page 0–9999, search 1–80 ký tự literal `icontains(..., autoescape=True)`. Parent-category
query gồm root và children. Không lấy inventory/delivery payload hoặc cache price/quyền.
Catalog public metadata có SQL CHECK theo cả năm type; không chấp nhận key, delivery
file_id/content hay extra fields. Photo ID regex kiểm tra charset, LENGTH kiểm tra 16–512
riêng vì PostgreSQL regex bounds chỉ hỗ trợ tới 255. T04 không thêm extensions/dependencies.

Migration tests đi từ T03 head lên catalog và giữ pending Owner confirmation/audit hoạt
động; metadata drift rỗng. `0001_runtime`–`0003_access` không sửa. Không auto-seed runtime;
demo hợp pháp giả nằm trong test fixtures/README, chưa có actual inventory.

## Health và shutdown

- `/health/live`: 200 `{"status":"ok"}`, không gọi database.
- `/health/ready`: 200 khi DB truy cập được và schema revision đúng; 503
  `{"status":"unavailable"}` khi missing/wrong schema, DB lỗi hoặc quá hạn 2 giây.
- Probe đóng connection kể cả timeout. Response không có DSN, SQL error hoặc settings;
  health responses dùng `Cache-Control: no-store`.
- FastAPI lifespan đóng engine/pool; mỗi UoW đóng session của nó. Uvicorn có graceful
  shutdown deadline 10 giây. Development polling sở hữu/đóng Bot session và engine trong
  context/finally, kể cả webhook refusal, API failure hoặc cancellation; chưa có worker.

Sources: [SQLAlchemy async session ownership](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#using-asyncsession-with-concurrent-tasks),
[Alembic async integration](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic),
[official PostgreSQL image/18 volume](https://hub.docker.com/_/postgres),
[PostgreSQL shared comments](https://www.postgresql.org/docs/18/functions-info.html#FUNCTIONS-INFO-COMMENT-TABLE).
[PostgreSQL regex bounds](https://www.postgresql.org/docs/18/functions-matching.html#POSIX-QUANTIFIERS-TABLE).
