# Database và transaction baseline

T02 dùng PostgreSQL **18.6**, SQLAlchemy **2.0.55** Async + asyncpg **0.32.0** và
Alembic **1.20.0**. Chưa có business tables hoặc repository nghiệp vụ.
`alembic_version` ghi baseline `0001_runtime`; source migration không tạo users, orders hay kho sớm.

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
Baseline chỉ hỗ trợ online migration; `--sql` và autogenerate chưa cấu hình vì chưa có ORM models.

Readiness so sánh revision trong DB với `SCHEMA_HEAD` ở
`src/telebotshop/infrastructure/database/schema.py`. Architecture test so sánh constant này
với Alembic head để bắt drift. Khi thêm schema ở slice sau, cập nhật migration, ORM metadata,
revision contract và acceptance tests cùng nhau. Không sửa migration đã được dùng.
Chưa có production migration, destructive upgrade, backup/restore hoặc release acceptance.

## Unit of work

Application port chỉ có `commit()`; concrete UoW và AsyncSession ở infrastructure.
`unit_of_work(engine)` tạo session/transaction mới cho mỗi lần gọi, không share session
giữa concurrent tasks. Use case phải gọi `commit()` tường minh. Session close rollback
mọi phần chưa commit, kể cả exception, cancelled task hoặc deferred constraint failure.
`autobegin=False` chặn query tự mở transaction khác sau commit.

Chưa có repository để wire ở T02. Khi thêm use case, composition root inject UoW factory;
repository chỉ dùng session trong transaction đó và không commit hoặc trả ORM ra ngoài.
PostgreSQL tests đã chứng minh hai backend connections độc lập, commit/rollback/cancel,
commit failure và không có hidden commit.

## Health và shutdown

- `/health/live`: 200 `{"status":"ok"}`, không gọi database.
- `/health/ready`: 200 khi DB truy cập được và schema revision đúng; 503
  `{"status":"unavailable"}` khi missing/wrong schema, DB lỗi hoặc quá hạn 2 giây.
- Probe đóng connection kể cả timeout. Response không có DSN, SQL error hoặc settings;
  health responses dùng `Cache-Control: no-store`.
- FastAPI lifespan đóng engine/pool; mỗi UoW đóng session của nó. Uvicorn có graceful
  shutdown deadline 10 giây. Chưa có Telegram/HTTP outbound client hoặc worker để đóng.

Sources: [SQLAlchemy async session ownership](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html#using-asyncsession-with-concurrent-tasks),
[Alembic async integration](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic),
[official PostgreSQL image/18 volume](https://hub.docker.com/_/postgres),
[PostgreSQL shared comments](https://www.postgresql.org/docs/18/functions-info.html#FUNCTIONS-INFO-COMMENT-TABLE).
