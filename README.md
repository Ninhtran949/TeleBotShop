# TeleBotShop

Shop Telegram bán tài nguyên số có quyền phân phối, thanh toán Stars và giao hàng an toàn.
Trạng thái và task đang được thực hiện: [PROJECT](docs/PROJECT.md),
[implementation plan](docs/plans/shop/implementation-plan.md).

T01/T02 đã có toolchain, HTTP health server, PostgreSQL readiness, async UoW và Alembic baseline.
Chưa có Telegram bot, catalog, payment hoặc business tables.

## Môi trường development

Baseline: CPython **3.12.15**, uv/build backend **0.13.4**, Pytest **9.1.1**, Ruff **0.17.1**.
`.python-version` pin interpreter; `pyproject.toml` kiểm tra chính xác phiên bản uv và pin
direct dev/build/runtime dependencies; `uv.lock` khóa dependency graph. Runtime pins ở
[pyproject.toml](pyproject.toml); SQLAlchemy giữ nhánh 2.0 theo ADR, chưa thêm aiogram trước khi cần bot.

Nếu máy chưa có uv, bootstrap từ wheel chính thức trong môi trường riêng của workspace
(PowerShell, cần Python 3.11+ chỉ cho bước cài tool):

```powershell
python -m venv .tools/uv
.tools/uv/Scripts/python.exe -m pip install --disable-pip-version-check --no-cache-dir --only-binary=:all: --no-deps --index-url https://pypi.org/simple uv==0.13.4
Set-Alias uv (Resolve-Path .tools/uv/Scripts/uv.exe)
```

Các biến dưới giữ interpreter/cache tải về trong workspace, không thay Python hệ thống.
Alias và biến môi trường chỉ áp dụng cho PowerShell session hiện tại:

```powershell
$env:UV_CACHE_DIR = Join-Path $PWD '.cache/uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $PWD '.tools/python'
$env:UV_PYTHON_BIN_DIR = Join-Path $PWD '.tools/bin'
uv python install
uv lock --check
uv sync --locked --no-build
```

`uv_build` cùng phiên bản được tích hợp trong uv; không chạy installer script từ mạng.
Tool/cache/virtualenv và build artifacts được ignore. Chưa cần Telegram token hay encryption key.

## Chạy local

Docker Compose dùng credentials giả và chỉ bind PostgreSQL vào loopback.
Start development database, export environment rồi chạy migration riêng trước server:

```powershell
docker compose --profile dev up -d --wait postgres
$env:APP_ENV = 'development'
$env:DATABASE_URL = 'postgresql+asyncpg://telebotshop_dev:dev-local-only@127.0.0.1:55432/telebotshop_dev'
uv run --no-sync alembic upgrade head
uv run --no-sync telebotshop
```

`uv run --no-sync python -m telebotshop` là entrypoint tương đương.
Server mặc định `127.0.0.1:8000`; `HTTP_HOST`/`HTTP_PORT` nhận từ environment nếu cần đổi.
DATABASE_URL kiểm tra port 1–65535; query chỉ hỗ trợ `ssl` với mode hợp lệ của asyncpg.
Missing/invalid settings làm CLI thoát 2 với thông báo đã redact. Import không load settings,
không mở network. `.env.example` chỉ là mẫu; muốn dùng local `.env`, nạp tường minh bằng
`uv run --env-file .env --no-sync telebotshop`. Không commit `.env` hoặc dùng mẫu local cho production.

`/health/live` trả 200 khi server chạy. `/health/ready` trả 200 khi DB/schema revision đúng,
503 khi thiếu migration, sai revision, DB lỗi hoặc probe timeout. Startup không tự migrate.
Chi tiết isolation, transaction và shutdown: [DATABASE](docs/DATABASE.md).

## PostgreSQL integration tests

Test service tách dev DB, dùng tmpfs và marker bên ngoài fixture:

```powershell
docker compose --profile test up -d --wait postgres-test
$env:APP_ENV = 'test'
$env:TEST_DATABASE_URL = 'postgresql+asyncpg://telebotshop_test:test-local-only@127.0.0.1:55433/telebotshop_test'
uv run --no-sync pytest tests/integration
```

Fixture không dùng `DATABASE_URL`; target ngoài allowlist hoặc sai database/run marker bị chặn.
Mỗi test tạo schema riêng và chỉ cleanup schema đó. Không SQLite/mock cho DB integration.
`docker compose stop postgres-test` dừng test service; dữ liệu tmpfs sẽ mất theo thiết kế.

## Kiểm tra

Chạy từ repository root sau sync:

```text
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pytest tests/unit tests/contract tests/architecture
uv run --no-sync python scripts/check_architecture.py
uv build --no-sources
```

Wheel/runtime import smoke (PowerShell): xuất dependency pins/hashes từ lock vào artifact
tạm được ignore, cài runtime dependencies và đúng wheel vừa build trong môi trường riêng.
`uv.lock` vẫn là lockfile duy nhất; export không được sửa tay hay commit. `-I` bỏ qua current
directory và `PYTHONPATH`:

```powershell
uv export --locked --no-dev --no-emit-project --format requirements-txt --output-file .cache/runtime-requirements.txt
uv venv --python .venv/Scripts/python.exe .cache/wheel-runtime-smoke
uv pip install --python .cache/wheel-runtime-smoke/Scripts/python.exe --require-hashes --no-build -r .cache/runtime-requirements.txt
uv pip install --python .cache/wheel-runtime-smoke/Scripts/python.exe --no-index --no-deps dist/telebotshop-0.1.0-py3-none-any.whl
.cache/wheel-runtime-smoke/Scripts/python.exe -I -c "import sys, telebotshop.bootstrap; from pathlib import Path; from importlib.metadata import version; assert Path(telebotshop.__file__).is_relative_to(Path(sys.prefix)); assert version('telebotshop') == '0.1.0'; print('PASS: isolated wheel runtime import')"
```

uv có thể cảnh báo cache nằm trong source root khi build; backend chỉ đóng gói package,
README và metadata. Đã kiểm tra nội dung sdist/wheel không chứa cache/tools/virtualenv.
Migration commands cần source checkout và `alembic.ini`/`migrations/`; các file này chưa được
đóng thành production image. DB runtime tests và package import smoke là hai gate riêng.

Harness độc lập với app dependencies, vẫn chạy được bằng Python standard library 3.11+:

```text
python scripts/verify_harness.py
python -B -m unittest discover -s tests -p test_harness.py -v
```

`uv audit --locked` kiểm tra advisories của dependency graph bằng uv 0.13.4 (command còn
experimental). CI, Telegram test/live, production image/deployment và backup/restore chưa thực hiện.

Nguồn toolchain: [Python 3.12.15](https://www.python.org/downloads/release/python-31215/),
[uv trên PyPI](https://pypi.org/project/uv/0.13.4/),
[uv required-version](https://docs.astral.sh/uv/reference/settings/#required-version),
[uv build backend tích hợp](https://docs.astral.sh/uv/concepts/build-backend/#bundled-build-backend).
