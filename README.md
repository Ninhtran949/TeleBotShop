# TeleBotShop

Shop Telegram bán tài nguyên số có quyền phân phối, thanh toán Stars và giao hàng an toàn.
Trạng thái và task đang được thực hiện: [PROJECT](docs/PROJECT.md),
[implementation plan](docs/plans/shop/implementation-plan.md).

T01–T06 đã có toolchain/runtime, identity/permissions/audit, catalog PostgreSQL với năm loại
metadata công khai, admin product maintenance và kho key/license mã hóa, import atomic,
retire/low-stock có phân quyền và order/reservation/expiry cho key/license. Durable invoice
intent và Stars adapter đã có; customer Buy còn chờ pre-checkout/receipt T07/T08. Chưa có
payment intake, fulfillment hoặc production webhook; Telegram API trong tests là mock.

## Môi trường development

Baseline: CPython **3.12.15**, uv/build backend **0.13.4**, Pytest **9.1.1**, Ruff **0.17.1**.
`.python-version` pin interpreter; `pyproject.toml` kiểm tra chính xác phiên bản uv và pin
direct dev/build/runtime dependencies; `uv.lock` khóa dependency graph. Runtime pins ở
[pyproject.toml](pyproject.toml); SQLAlchemy giữ nhánh 2.0 theo ADR. aiogram **3.31.0** yêu cầu
Pydantic `<2.14`, nên T03 pin Pydantic **2.13.5**, giữ các runtime pins còn lại của T02.

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
Tool/cache/virtualenv và build artifacts được ignore. HTTP health và automated tests không
cần Telegram token thật hoặc encryption key thật.

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

Operator có thể chạy một batch tối đa 100 đơn hết hạn và 100 invoice claims gián đoạn:

```text
uv run --no-sync telebotshop expire-orders
```

Lệnh cần DATABASE_URL/schema head `0006_orders`, không cần BOT_TOKEN/OWNER_ID hoặc
inventory keys; không gửi Telegram. TTL reservation 15 phút theo DB time. Claim SENDING
quá 30 giây chuyển UNCERTAIN, giữ attempt và không tự gửi lại. Chạy lặp theo lịch vận hành
khi có checkout; T06 chưa có scheduler/worker hoặc thao tác tự reconcile/resend invoice.
Invoice sent/uncertain không chứng minh đã thanh toán; order/payload history vẫn được giữ
sau cancel/expiry để payment intake ở T08 xử lý receipt tới trễ.

## Development polling và quyền Owner

Sau migration, operator đặt `BOT_TOKEN`, numeric `OWNER_ID` và inventory key settings bên dưới trong process environment
hoặc local `.env` được bảo vệ, theo mẫu trống trong `.env.example`. Không đưa secrets vào chat/Git.
Polling chỉ cho `APP_ENV=development` hoặc `test`; dùng test bot tách biệt và chỉ chạy khi
operator chủ động muốn kết nối Telegram:

```text
uv run --no-sync telebotshop polling
```

Nếu dùng local `.env`: `uv run --env-file .env --no-sync telebotshop polling`.
HTTP health tiếp tục chạy riêng bằng `telebotshop` hoặc `telebotshop http`.
Polling kiểm tra exact schema head, bootstrap Owner, kiểm tra registered inventory keys và từ chối bot đã có webhook;
không xóa webhook hoặc drop pending updates. Chỉ một polling process cho một bot.
Thiếu/sai settings hoặc Owner mismatch thoát 2; lỗi DB/Telegram runtime thoát 1 với thông báo
đã redact. Ctrl+C kết thúc polling và đóng client/pool; interrupted CLI có thể thoát 130.

Trong private chat:

- `/start` tạo/cập nhật metadata trên cùng Telegram ID; có Cửa hàng, Tìm kiếm, Tài khoản,
  Quay lại/Trang chủ/Hủy. `/shop` mở danh mục; `/search <từ khóa>` tìm tên/mô tả/SKU.
- `/admin` kiểm tra quyền hiện tại. Owner có Quản lý quyền, Cài đặt, Sản phẩm và Kho hàng;
  staff có `catalog.manage` thấy Sản phẩm, `inventory.manage` thấy Kho hàng.
  Chưa có orders/refunds controls của các task sau.
- Owner dùng `/grant <Telegram_ID> <ADMIN|SUPPORT> <permission>` hoặc `/revoke` với cùng
  ba tham số, sau đó bấm **Xác nhận** trong 10 phút. Người nhận phải `/start` trước;
  lấy ID của chính mình từ Tài khoản. Mỗi lần chỉ đổi một permission.
- ADMIN có thể nhận `catalog.manage`, `inventory.manage`, `orders.read`, `payments.refund`,
  `tickets.manage`; SUPPORT chỉ `tickets.manage` và vẫn cần assignment của ticket thật.
  Đổi ADMIN/SUPPORT phải thu hồi hết grants cũ trước. Owner/settings permissions không delegate.
- `/setsupport <username>` (có thể có `@`) hoặc `/setsupport -` để xóa; đều cần xác nhận.
  Mặc định chưa cấu hình. Không tự xác minh username thuộc tài khoản hỗ trợ thật.

Owner đầu tiên được lưu bền vững; restart không sửa quyền đã mất hoặc đổi Owner theo env.
Khi mismatch, khôi phục đúng cấu hình và kiểm tra quy trình ownership riêng; T03 không có
owner-transfer command. Confirmation ràng buộc actor/target/version; replay không áp dụng lại.
Controls và giới hạn: [SECURITY](docs/SECURITY.md). Chưa thực hiện Telegram test/live ở T03.

## Catalog và quản lý sản phẩm (T04)

Người có `catalog.manage` mở Sản phẩm trong `/admin` hoặc `/products`. Dữ liệu đọc từ DB,
không auto-seed và không có dashboard counts giả. Danh mục gồm root và một cấp con;
code/SKU phân biệt hoa thường, 1–32 ký tự ASCII, bắt đầu bằng chữ/số và chỉ gồm chữ/số/`_`/`.`/`-`, không đổi sau tạo.
Sản phẩm mới mặc định **ẩn**; sửa cùng SKU giữ UUID/type, ảnh và trạng thái hiện tại.
Giá là integer Stars dương trong miền BIGINT; không chấp nhận float/bool hoặc số âm.

Ví dụ dữ liệu công khai **giả** để operator tự nhập trong private chat:

```text
/category demo | Danh mục thử nghiệm
/category demo-child | Danh mục con thử nghiệm | demo
```

Mỗi lệnh tạo một yêu cầu riêng, cần bấm Xác nhận trước khi dùng danh mục đó. Form sản phẩm
gồm header và các dòng tên/mô tả/thông tin theo loại:

```text
/product DEMO-KEY | demo-child | UNIQUE_KEY | 100
Sản phẩm minh họa
Mô tả công khai giả, không có tài nguyên giao hàng.
```

Sau dòng mô tả, UNIQUE_KEY không thêm dữ liệu; các loại khác thêm đúng một dòng:

| TYPE | Dòng cuối công khai |
| --- | --- |
| `LICENSE` | Hướng dẫn kích hoạt, tối đa 500 ký tự; không nhập license key. |
| `DIGITAL_FILE` | `demo.pdf \| application/pdf`; chỉ tên/MIME, không file_id dùng để giao hàng. |
| `REUSABLE_RESOURCE` | `TEXT` hoặc `DOCUMENT`; không nội dung/reference bí mật. |
| `MANUAL_FULFILLMENT` | Thông báo xử lý thủ công cho khách, tối đa 500 ký tự. |

Giới hạn: tên 80, mô tả 1200 ký tự (có thể trống); form tối đa 2200 ký tự. Để sửa, gửi lại
form đầy đủ cùng SKU/type. Các thao tác dưới đây cũng cần confirmation trong 10 phút:

```text
/price DEMO-KEY 150
/sale DEMO-KEY active
/sale DEMO-KEY hidden
/productimage DEMO-KEY clear
```

Đặt ảnh bằng cách gửi **photo** với caption `/productimage DEMO-KEY`; chỉ lưu Telegram
photo file_id công khai từ Update, không nhận URL/local path. Ảnh minh họa không phải
DIGITAL_FILE delivery reference. Hủy confirmation không áp dụng thay đổi; request cũ sau
khi sản phẩm đổi version cần tạo lại. Callback cũ luôn đọc lại giá/trạng thái hiện tại.

Khách chỉ xem active products; search substring literal không phân biệt hoa thường,
1–80 ký tự, `%`/`_` được hiểu là ký tự thường. Trang có 5 items, page 0–9999. Root category
filter gồm cả sản phẩm ở danh mục con. Navigation context actor-bound, tối đa 4096 views,
hết hạn logic sau 10 phút hoặc mất khi restart/eviction; mở lại `/shop` nếu hết hạn.

Product metadata vẫn chỉ là dữ liệu công khai. Private key/license nằm trong inventory mã hóa
của T05; file/reusable/manual payload thuộc T10. Không tự fetch URL, không hiển thị số stock
giả hoặc nhận đơn qua polling; finite reservation có ở T06, public Buy chờ T07/T08.
Ảnh/catalog thông báo trạng thái thật hiện có, chưa là Telegram external acceptance.
`APP_ENV=test` không tự đổi Bot API sang Stars test server; setup chính thức thuộc T14.

## Inventory mã hóa và import (T05)

Polling cần `INVENTORY_ENCRYPTION_KEYS` là JSON object, ví dụ cấu trúc
`{"1":"<base64-32-byte-key>"}`, `INVENTORY_CURRENT_KEY_VERSION=1`, và
`INVENTORY_DEDUP_KEY` là base64 của **32 bytes ngẫu nhiên khác**. Tạo bằng CSPRNG và
đặt qua môi trường bảo vệ; placeholder không phải key hợp lệ. Không gửi key lên chat,
không tái sử dụng fixture test, không commit hoặc in settings. HTTP health/migrations
không cần encryption keys; automated tests tự inject fake keys.
Keyring nhận 1–32 versions, mỗi version là số nguyên 1–2.147.483.647 dưới dạng JSON key.

AES-GCM 256-bit dùng nonce ngẫu nhiên 12 bytes; AAD bind product UUID/SKU/unit UUID/type/version.
HMAC-SHA256 dùng dedup key riêng, không trim/casefold/normalize key material. DB pin HMAC
verifiers của cấu hình lần đầu dùng: đổi dedup key, thay AES key dưới cùng version hoặc
bỏ registered old version đều fail-closed trước Telegram polling/import. Để thêm version,
giữ nguyên các key cũ, thêm version/key mới và chọn current version mới. Chưa có bulk
re-encryption/key removal; thay dedup key cần migration riêng có Owner authorization.
Backup keys riêng DB; mất key thì không phục hồi payload từ DB dump đơn thuần.

Staff có `inventory.manage` dùng trong private chat:

```text
/inventory
/stock DEMO-KEY
/retire DEMO-KEY <UNIT_ID từ /stock>
/lowstock
```

Import bằng **document .txt/.csv**, caption `/import DEMO-KEY`. Không đặt key trong caption.
Chỉ UNIQUE_KEY và LICENSE nhận finite units ở T05. UTF-8 (cho phép BOM), tối đa **1 MiB**,
**1.000 rows/batch**, mỗi key tối đa **4.096 UTF-8 bytes**, không rỗng/control characters.
TXT: một key mỗi dòng, LF/CRLF; CSV: đúng một header `key` (UNIQUE_KEY) hoặc `license_key`
(LICENSE), đúng một cột/row, hỗ trợ CSV quoting, không multiline key. Khoảng trắng đầu/cuối
của key hợp lệ được giữ nguyên. Không dùng blank lines; một final newline được cho phép.

Download được giới hạn thực tế trong bộ nhớ, không ghi plaintext temporary file, không
đọc local file path hoặc tải URL tùy ý. Confirmation chỉ hiển thị SKU/số lượng; pending
batch chỉ lưu encrypted envelopes, **chưa tính vào stock**. Xác nhận trong 10 phút:
units + redacted audit + applied marker atomic. Bất kỳ row lỗi/trùng đều rollback batch;
lỗi chỉ trả số dòng/mã lỗi, không echo key. Hai import đồng thời vẫn bị DB unique constraint
chặn trùng trong SKU, kể cả key đã retire. Cancel xóa pending envelopes; replay không tạo
thêm stock, action đã apply không thể cancel để undo.

`/stock` chỉ trả counts và unit IDs/status, 5 units/trang; không có secret view/export/decrypt.
Retire chỉ cho AVAILABLE → RETIRED, có confirmation/audit; không delete/reuse payload.
`/lowstock` chỉ query active finite products, committed AVAILABLE ≤ `LOW_STOCK_THRESHOLD`
(mặc định 5, khoảng 0–1.000.000), 5 products/trang. Đây là query/flag thực, chưa tự gửi
notification, reserve unit, tạo invoice hoặc nhận thanh toán. Customer UI vẫn ghi **Chưa nhận đơn**.
Pending actions hết hạn bị chặn; chưa có retention job trước khi Owner chốt policy.

Dependency theo ADR-0003: [cryptography 50.0.2](https://pypi.org/project/cryptography/50.0.2/),
pin trong manifest/lock; crypto contract theo [AESGCM documentation](https://cryptography.io/en/stable/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM).

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
[uv build backend tích hợp](https://docs.astral.sh/uv/concepts/build-backend/#bundled-build-backend),
[aiogram release/constraints](https://pypi.org/project/aiogram/3.31.0/),
[aiogram polling contract](https://docs.aiogram.dev/en/dev-3.x/dispatcher/dispatcher.html).
