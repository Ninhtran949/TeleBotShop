# TeleBotShop

Shop Telegram bán tài nguyên số có quyền phân phối, thanh toán Stars và giao hàng an toàn.
Trạng thái và task đang được thực hiện: [PROJECT](docs/PROJECT.md),
[implementation plan](docs/plans/shop/implementation-plan.md).

T01 thiết lập package/toolchain; chưa có bot, HTTP server, database hoặc business flow để chạy.

## Môi trường development

Baseline: CPython **3.12.15**, uv/build backend **0.13.4**, Pytest **9.1.1**, Ruff **0.17.1**.
`.python-version` pin interpreter; `pyproject.toml` kiểm tra chính xác phiên bản uv và pin
direct dev/build dependencies; `uv.lock` khóa dependency graph. Chưa có runtime dependency.

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
Tool/cache/virtualenv và build artifacts được ignore. Chưa cần token, `.env` hay PostgreSQL.

## Kiểm tra

Chạy từ repository root sau sync:

```text
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pytest tests/architecture
uv run --no-sync python scripts/check_architecture.py
uv build --no-sources
```

Wheel/import smoke (PowerShell): cài đúng wheel vừa build vào môi trường riêng,
không có dev dependencies; `-I` bỏ qua current directory và `PYTHONPATH`:

```powershell
uv venv --python .venv/Scripts/python.exe .cache/wheel-smoke
uv pip install --python .cache/wheel-smoke/Scripts/python.exe --no-index --no-deps dist/telebotshop-0.1.0-py3-none-any.whl
.cache/wheel-smoke/Scripts/python.exe -I -c "import sys, telebotshop; from pathlib import Path; from importlib.metadata import version; assert Path(telebotshop.__file__).is_relative_to(Path(sys.prefix)); assert version('telebotshop') == '0.1.0'; print('PASS: isolated wheel import')"
```

uv có thể cảnh báo cache nằm trong source root khi build; backend chỉ đóng gói package,
README và metadata. Đã kiểm tra nội dung sdist/wheel không chứa cache/tools/virtualenv.
Các self-cases của checker tạo fixture tạm và không cần cài Telegram/ORM SDK.

Harness độc lập với app dependencies, vẫn chạy được bằng Python standard library 3.11+:

```text
python scripts/verify_harness.py
python -B -m unittest discover -s tests -p test_harness.py -v
```

Không có integration/contract/business unit suite trước các task triển khai tương ứng.
Database, migrations, Compose, CI và Telegram test/live validation thuộc task sau.

Nguồn toolchain: [Python 3.12.15](https://www.python.org/downloads/release/python-31215/),
[uv trên PyPI](https://pypi.org/project/uv/0.13.4/),
[uv required-version](https://docs.astral.sh/uv/reference/settings/#required-version),
[uv build backend tích hợp](https://docs.astral.sh/uv/concepts/build-backend/#bundled-build-backend).
