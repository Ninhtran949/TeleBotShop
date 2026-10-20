# ADR-0004: Toolchain và quality gates có thể lặp lại

- Status: Accepted, design only; chưa triển khai.

## Context

Yêu cầu ưu tiên Python 3.12+, SQLAlchemy 2.0, Pytest/Ruff và Docker Compose; chưa có manifest, lockfile hoặc CI. Các agent cần một contract thống nhất và phải phân biệt commands dự kiến với commands đã chạy.

## Decision

Chọn Python 3.12, PostgreSQL 18, aiogram 3.x, FastAPI, SQLAlchemy **2.0.x** Async với asyncpg, Alembic và Pydantic Settings. Không tự nâng sang SQLAlchemy 2.1 chỉ vì đó là release mới. `cryptography` là dependency thực sự cần cho ADR-0003.

Chọn `uv` làm development/build dependency manager, một `pyproject.toml` + `uv.lock`; không duy trì thêm requirements lock hoặc Poetry/Pipenv manifest. T01 pin tool/runtime versions và các package versions tương thích từ nguồn chính thức. Không tự tạo con số patch versions chưa resolve/test.

Pytest kiểm tra behavior; Ruff format/lint với config ở [CONVENTIONS](../CONVENTIONS.md). PostgreSQL thật cho integration/races, AST checks cho import direction. CI dùng locked environment và cùng gates local. Chưa thêm type checker, coverage threshold, pre-commit/plugin/hook framework chỉ để có thêm công cụ.

## Alternatives

- pip + nhiều requirements/constraints files: dùng được nhưng dễ tạo hai nguồn dependency truth khi các agent cùng cập nhật.
- Poetry/Pipenv hoặc nhiều tool song song: không có nhu cầu riêng bù chi phí.
- Luôn latest/no lock: không tái tạo được build hoặc review upgrade rõ ràng.

## Consequences và kiểm chứng

Thêm uv như tool development, không là dependency runtime. Bootstrap phải chứng minh clean locked install, test/lint/format/build, pin image và cấu hình DB test riêng trước khi feature tasks coi gates là tồn tại. `--locked` kiểm tra metadata drift; không dùng `--frozen` thay cho check đó. `--no-build` chặn third-party sdist builds nhưng project build backend vẫn cần review.

Nguồn: [uv locking/sync](https://docs.astral.sh/uv/concepts/projects/sync/), [uv build/install options](https://docs.astral.sh/uv/reference/cli/), [PostgreSQL 18 documentation](https://www.postgresql.org/docs/18/), [SQLAlchemy 2.0 asyncpg dialect](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#module-sqlalchemy.dialects.postgresql.asyncpg). Đây là baseline lựa chọn, chưa có compatibility evidence trong repository.
