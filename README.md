# TeleBotShop

Shop Telegram bán tài nguyên số có quyền phân phối và giao hàng an toàn. Payment V2 local/sandbox
dùng **Bank/VietQR hoặc USDT để nạp ví, Credit để mua hàng**; Stars đang PAUSED/BLOCKED.
Trạng thái và task đang được thực hiện: [PROJECT](docs/PROJECT.md),
[implementation plan](docs/plans/shop/implementation-plan.md).

T01–T13 đã có toolchain/runtime, identity/permissions/audit, catalog PostgreSQL với năm loại
metadata công khai, admin product maintenance và kho key/license mã hóa, import atomic,
retire/low-stock có phân quyền và order/reservation/expiry cho key/license. Durable invoice
intent, pre-checkout approval, durable receipt, paid allocation và READY fulfillment intent đã có.
T09 có worker giao key/license, fenced attempts/recovery và bounded known-failure retry.
T10 thêm nguồn mã hóa theo phiên bản cho file/reusable/manual, document dispatch và staff
completion có bằng chứng/xác nhận. T11 thêm lịch sử đơn, dashboard staff và resend cùng
entitlement có xác nhận. T12 thêm ticket mã hóa, phân công/phản hồi theo quyền, `/paysupport`
và điều khoản do Owner cung cấp. T13 thêm full refund có xác nhận/ledger/hold, intake refund
event và đối soát bằng chứng Stars. T14 nối public Buy/đồng ý terms/invoice, tra cứu khách,
test endpoint, authenticated webhook và thông báo vận hành. Telegram API trong tests là mock;
PostgreSQL integration dùng DB thật. **T14 chức năng thay Stars đã PASS** theo
[nghiệm thu Payment V2](docs/plans/shop/t14-replacement-acceptance.md), gồm actual Telegram Test
delivery/refund; các gate Stars cũ, hosted payment providers và production còn riêng.
Owner chưa có bot/account: bắt đầu tại [BOTFATHER-SETUP](docs/BOTFATHER-SETUP.md).

Payment V2 có [ADR-0006](docs/adr/0006-payment-v2-wallet.md),
[spec/plan](docs/plans/shop/payment-v2.md) và [runbook](docs/PAYMENT-V2.md).
Checkout Credit dùng một transaction PostgreSQL để trừ ví, ghi payment, cấp entitlement và
durable work; không cần invoice, pre-checkout hoặc Telegram successful_payment. Bộ Stars
cũ được giữ lại; E3/E4 Stars lịch sử vẫn chưa đạt, không chặn T14 chức năng theo quyết định Owner
mới. Không có nhận tiền thật hoặc deployment ở phase này.

T15 thêm coupon percent/fixed **Credit** với caps/scope/expiry, price snapshot và redemption
atomic trong checkout; refund không hoàn lượt. Admin/customer commands và bước nâng schema
`0021_coupons`: [runbook coupon](docs/PAYMENT-V2.md#coupon-credit-t15). Stars tiếp tục PAUSED.

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

Lệnh cần DATABASE_URL/schema head `0014_notifications`, không cần BOT_TOKEN/OWNER_ID hoặc
inventory keys; không gửi Telegram. TTL reservation 15 phút theo DB time. Claim SENDING
quá 30 giây chuyển UNCERTAIN, giữ attempt và không tự gửi lại. Chạy lặp theo lịch vận hành
khi có checkout; T06 chưa có scheduler/worker hoặc thao tác tự reconcile/resend invoice.
Invoice sent/uncertain không chứng minh đã thanh toán; order/payload history vẫn được giữ
sau cancel/expiry để T08 ghi nhận receipt tới trễ.

Pre-checkout T07 kiểm tra buyer, XTR, giá snapshot, active product và exact reservation;
approval/query ID giữ bền vững, chỉ gia hạn một lần từ DB time. Cùng query lặp lại không
kéo dài TTL; query khác cho order đã approved bị từ chối. API timeout không reset approval.
DB/validation budget 4s, API answer budget 3s; kiểm thử local không thay Telegram test/live.
T08 nối runtime vào checker thật cùng durable successful-payment intake. Approval không
tạo PAID/allocation. T14 public Buy dùng các use cases này; không gọi delivery trong handler receipt.

Polling dùng native aiogram concurrent update tasks, cap 32, để một download import không
chặn pre-checkout. Mỗi task có UoW riêng; shutdown đợi tối đa 10s rồi cancel/drain trước
khi đóng Bot/DB. Drain dùng pending-task set private của aiogram 3.31.0 đã pin; kiểm tra lại
khi nâng SDK. T08 persist receipt trước khi native polling yield/advance offset; lỗi DB
dừng polling và không confirm receipt đó. Re-delivery dùng charge ID để dedup; receipt
handler không send/decrypt/giao hàng. Chưa có nghiệm thu tải/latency Telegram hoặc production ingress.

## Worker giao hàng và thông báo (T09–T14)

Sau migration head `0014_notifications`, đặt BOT_TOKEN/OWNER_ID, DATABASE_URL và inventory
keys qua environment bảo vệ như polling. Command có thể gửi secret tới buyer; chỉ operator
chạy với đúng bot/environment và authorization gửi thực tế. Automated tests dùng mock.

```text
uv run --no-sync telebotshop worker
```

Worker chỉ cho APP_ENV development/test; không đọc updates hoặc xóa/thay webhook. Kiểm tra
schema, persisted Owner và key verifiers trước tạo Bot. Startup/mỗi lượt recover tối đa 100
attempts hết lease; claim một due work bằng SKIP LOCKED rồi commit riêng. Trước decrypt/send
recheck order/payment/destination/exact allocated unit/type/hold/token/lease. Commit SENDING
trước HTTP; không giữ DB transaction trong network call. Gửi đúng nguyên văn key/license,
plain text, không preview hoặc raw payload log. Local/mock checks chưa là Telegram test acceptance.

| Environment setting | Default / giới hạn |
| --- | --- |
| `WORKER_CLAIM_SECONDS` | 30; 1–300. |
| `WORKER_SEND_SECONDS` | 60; tối đa 300, ít nhất API timeout + 10s. |
| `WORKER_TIMEOUT_SECONDS` | 10; 1–20, adapter cũng chặn deadline đã hết. |
| `WORKER_MAX_ATTEMPTS` | 3; 1–5 actual sends, recheck budget trước decrypt cả sau restart. |
| `WORKER_POLL_SECONDS` | 1; 0.05–60 khi không có eligible work. |

Known rate-limit rejection: persisted RETRY_WAIT, exponential 2s→60s + jitter, không sớm hơn
retry_after, tối đa configured sends. Retry-after ngoài 1–86400s dừng FAILED theo input
policy. Network/timeout, lease SENDING hết hoặc result tới quá lease giữ UNCERTAIN, không
tự gửi lại. CLAIMED chưa gửi hết lease được requeue bằng token; key vẫn cùng allocation.
Late/stale results chỉ là sanitized observations khi không còn quyền áp dụng state.

Ctrl+C/cancellation đóng Bot/engine; interrupted SENDING được recovery thành UNCERTAIN khi
hết lease. Giữ DB/host UTC clocks đồng bộ; production clock/signal/load checks còn T18.
T14 thêm durable metadata-only notifications qua chính worker này: discover mỗi 10s,
tối đa 10 nguồn/loại và 100/lượt, drain một thông báo/lượt. Alert tài chính/manual/backlog/
low-stock gửi current persisted Owner; latest staff reply của ticket OPEN gửi đúng customer.
Không push nội dung ticket/key/charge/evidence; staff dùng inbox hiện có. Low-stock một lần/
ngày UTC/sản phẩm, backlog quá 5 phút. Trước send kiểm tra lại quyền/người nhận/source.
Known refusal là FAILED; unknown/expired SENDING là UNCERTAIN, không tự gửi lại kể cả
rate-limit rejection. Kết quả muộn chỉ thêm observation, không clear uncertainty. Kiểm tra
fixed logs/ledger `notifications` và business inbox; chưa có màn hình retry alert.
Không reset SENDING/UNCERTAIN hoặc lấy key khác để sửa lỗi giao. Refund/reconcile/resend
dùng các confirmation và positive-evidence flows đã có, không sửa ledger trực tiếp.

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
  T10 thêm Nguồn giao hàng và Giao hàng thủ công theo quyền hiện tại.
  T11 thêm Đơn hàng/Tổng quan đơn; T12 thêm Ticket theo `tickets.manage`;
  T13 thêm Hoàn tiền Stars theo `payments.refund` hiện tại. T14 thêm **Khách hàng** và
  `/customer TELEGRAM_ID [PAGE]` theo current `orders.read`, lọc đúng buyer, 5 đơn/trang,
  30 ngày gần nhất; PAGE bắt đầu từ 0. Không tìm theo username hoặc cho đọc secrets.
- Owner dùng `/grant <Telegram_ID> <ADMIN|SUPPORT> <permission>` hoặc `/revoke` với cùng
  ba tham số, sau đó bấm **Xác nhận** trong 10 phút. Người nhận phải `/start` trước;
  lấy ID của chính mình từ Tài khoản. Mỗi lần chỉ đổi một permission.
- ADMIN có thể nhận `catalog.manage`, `inventory.manage`, `fulfillment.manage`, `orders.read`, `payments.refund`,
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
giả. T14 nút **Mua** chỉ hiện khi có finite AVAILABLE stock hoặc nguồn nonfinite phù hợp.
Ảnh/catalog thông báo trạng thái thật hiện có, chưa là Telegram external acceptance.
`APP_ENV=test` không tự đổi Bot API sang Stars test server; đặt riêng
`TELEGRAM_ENVIRONMENT=test` với bot tạo trong Test Server. Xem [setup](docs/BOTFATHER-SETUP.md).

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

## File, tài nguyên dùng lại và công việc thủ công (T10)

Sau khi tạo public metadata của sản phẩm, staff có `inventory.manage` mở **Nguồn giao hàng**
trong `/admin` hoặc `/resources`. Đăng nguồn bằng `/resource SKU`, rồi bấm xác nhận trong
10 phút. Nguồn pending không được mua; xác nhận kiểm tra lại actor, quyền, product version
và phiên bản nguồn trước đó. Hai lần cập nhật đồng thời chỉ áp dụng một phiên bản.

- `DIGITAL_FILE`: gửi document cho bot kèm caption `/resource DEMO-FILE`. Tên file/MIME
  phải khớp public metadata. Chỉ đăng ký `file_id` từ document; không download hoặc nhận URL.
- `REUSABLE_RESOURCE` có format `DOCUMENT`: gửi document kèm caption `/resource DEMO-DOC`.
  Với format `TEXT`, gửi `/resource DEMO-TEXT`, xuống dòng rồi nội dung tối đa 4096 UTF-8 bytes.
- `MANUAL_FULFILLMENT`: gửi `/resource DEMO-MANUAL`, xuống dòng rồi hướng dẫn riêng cho staff
  tối đa 2048 UTF-8 bytes. Customer notice lấy từ public metadata, tối đa 500 ký tự.

Các tên DEMO ở trên chỉ minh họa cú pháp; bot không tự seed sản phẩm hoặc nguồn thật.
Nguồn được mã hóa bằng keyring hiện có, giữ phiên bản cũ. Mỗi order item chốt một resource
version khi tạo đơn; sửa nguồn/catalog sau đó không đổi entitlement đã chốt. Ba loại này
không reserve/allocate finite units hoặc hiển thị số stock giả. Pre-checkout vẫn kiểm tra
buyer/XTR/giá/TTL/active product; receipt và paid work được commit atomic.

Worker gửi file/reusable document bằng `sendDocument`, text bằng `sendMessage`, tới private
buyer đã lưu. Wrong type/format/AAD/envelope fail closed; không fallback document sang text.
Manual worker chỉ gửi customer notice, không đưa staff instructions vào sender. Notice
được Telegram xác nhận mới chuyển work `AWAITING_MANUAL`; order vẫn `FULFILLING`.
Known rejection/retry và uncertain dùng cùng policy T09; failed/uncertain cần attention và
reconciliation ở task tương ứng, không tự hoàn tất manual work.

Owner có quyền `fulfillment.manage`; ADMIN cần Owner cấp riêng quyền này qua flow hiện có.
`orders.read` và `inventory.manage` không đủ để đọc hướng dẫn riêng/hoàn tất đơn thủ công.
Staff có quyền dùng `/manual` xem queue (5/trang), `/manual WORK_ID` xem đúng buyer/hướng dẫn.
Sau khi thực sự xử lý xong, gửi `/manual_complete WORK_ID`, xuống dòng rồi bằng chứng hoặc
tham chiếu xử lý tối đa 500 UTF-8 bytes, và bấm **Xác nhận đã giao** trong 10 phút.
Bằng chứng được mã hóa, audit chỉ giữ digest; staff đang xác nhận việc đã xử lý bên ngoài,
bot không tự xác minh tính đúng đắn của tham chiếu đó. Xác nhận kiểm tra quyền hiện tại,
actor/attempt, expiry, paid state và hold; completion + audit + order DELIVERED commit cùng nhau.
Replay không hoàn tất lần hai. Preview/log/audit không echo nội dung nguồn hoặc bằng chứng.

**Owner setup cho T10 local: không cần thêm key/provider/server.** Tests dùng fixture giả,
Telegram mock và PostgreSQL test cô lập. Để nghiệm thu Telegram T14 cần test bot/account,
secrets trong môi trường bảo mật và tài nguyên có quyền phân phối; không gửi secrets trong chat.
Public Buy và production deployment vẫn theo task sở hữu; refund/reconcile local có ở T13.

## Lịch sử đơn và gửi lại (T11)

- Khách dùng **Đơn hàng đã mua**, `/orders` hoặc `/orders ORDER_ID`. Danh sách 5 đơn/trang,
  mới nhất trước, hiển thị mã đơn, tên/giá lúc mua, trạng thái và thời gian UTC. Chỉ chính
  buyer được xem, kể cả khi tự sửa callback/ID; không trả invoice payload hoặc inventory secret.
- Staff có `orders.read` dùng **Đơn hàng**, `/staff_orders [ORDER_ID]` và
  **Tổng quan đơn** hoặc `/dashboard`. Danh sách/dashboard giới hạn 30 ngày gần nhất;
  ID lookup vẫn đọc được đơn cũ theo quyền. Tổng số/trạng thái/giá trị đơn đã thanh toán
  lấy từ PostgreSQL, không số mẫu; giá trị là snapshot các đơn có receipt MATCHED,
  chưa trừ refund và không cộng extra/unmatched charges.
- **Xem lại / gửi lại tài nguyên** tạo xác nhận 10 phút, chưa gửi ngay. Khách chỉ gửi về
  private buyer đã lưu. Staff cần cả `orders.read` và `fulfillment.manage`; quyền xem
  đơn không cho đọc/gửi secrets. Xác nhận gắn actor và lần giao gốc, kiểm tra lại quyền,
  buyer, payment, state và hold trước decrypt; không đổi destination từ callback.

Chỉ đơn `DELIVERED` với primary work `SENT`, không hold/attention, được gửi lại.
Key/license/file/reusable dùng cùng unit/resource version và typed sender T09–T10;
catalog/source mới không thay nội dung cũ. Không allocate key mới hoặc mở lại primary work.
Đơn thủ công chỉ xem trạng thái; không lặp lại công việc hoặc gửi lại thông báo đang chờ.

`resend_actions` giữ confirmation, send window và kết quả riêng. Commit `SENDING` trước API,
không giữ DB connection qua Telegram; lưu kết quả/audit trong transaction khác. Replay cùng
action không gọi send lần hai. Known failure cần yêu cầu mới rõ ràng; không retry tự động.
Timeout/mất ACK/crash sau claim được coi là chưa rõ: history hiển thị `UNCERTAIN` khi quá
deadline; lần gọi lại action/prepare lưu trạng thái này bền vững, giữ late observation,
chặn resend tiếp và automatic refund hold. T13 cho phép explicit refund decision sau lease
theo phần hoàn tiền bên dưới; không clear uncertainty để gửi lại.

**Owner setup T11 local: không cần thêm key/provider/server.** Tests dùng fake inventory,
PostgreSQL test cô lập và Telegram mock. Real Telegram acceptance vẫn cần test bot/account,
secrets/keyring hiện có qua environment bảo vệ ở T14. T13 đã bổ sung native refund-ledger/
resend race tests, không dựa riêng vào simulated `REFUNDED` repository DTO của T11.

## Hỗ trợ và điều khoản (T12)

Khách dùng **Hỗ trợ**, `/support`, `/paysupport` hoặc **Hỗ trợ đơn này** trong chi tiết đơn.
Các đường dẫn này hoạt động cho cả đơn paid/failed/uncertain; tạo ticket không thay đổi
payment, allocation hoặc delivery. `/paysupport` hướng dẫn tạo ticket loại PAYMENT,
kể cả chưa có mã đơn; không tự suy receipt hoặc credit tài khoản.

- Tạo bằng `/ticket GENERAL|PAYMENT|DELIVERY [ORDER_ID]`, xuống dòng rồi nhập nội dung.
  ORDER_ID nếu có phải thuộc chính khách. Mỗi tin tối đa 2000 UTF-8 bytes, được mã hóa
  bằng keyring hiện có; không gửi mật khẩu/OTP/mã khôi phục/thông tin thẻ.
- `/tickets` và `/ticket TICKET_ID` chỉ mở ticket của mình. `/ticket_reply TICKET_ID`,
  xuống dòng rồi nhập phản hồi. Danh sách 5 ticket/trang; thread 1 tin/trang, mới nhất trước.
- Staff có current `tickets.manage` dùng `/staff_tickets` và `/staff_reply TICKET_ID`
  với nội dung ở dòng tiếp theo. SUPPORT chỉ thấy ticket đã được phân công trong DB.
  Linked-order context chỉ là public snapshot qua ticket đúng scope; không cấp orders.read
  hoặc đọc tài nguyên giao hàng.
- OWNER/ADMIN có quyền ticket dùng `/ticket_assign TICKET_ID TELEGRAM_ID` hoặc `-` để bỏ
  phân công. Người nhận phải có current `tickets.manage`. `/ticket_close TICKET_ID`
  (khách), `/staff_close TICKET_ID` (staff) và phân công đều cần confirmation trong 10 phút;
  kiểm tra lại actor, quyền, assignment và phiên bản ticket. Ticket đã đóng không nhận tin mới.

Phản hồi được lưu bền vững trong thread; khách mở/làm mới ticket để đọc. T14 worker gửi
thông báo metadata về latest reply của ticket OPEN; UI chỉ xác nhận đã lưu, không hứa
đã push thành công. Support contact vẫn dùng `/setsupport username`
với Owner confirmation; mặc định chưa cấu hình, bot không xác minh account đó thuộc shop.

**Owner setup T12:** không cần thêm key/provider/server cho local tests. Owner đã chọn
`temp.md` làm điều khoản chính thức và sẽ điền ngày hiệu lực, đơn vị vận hành, thời gian
phản hồi/xử lý và liên kết privacy còn trống. File này được giữ nguyên, chưa tự công bố.
Sau khi hoàn thiện nội dung công khai, đặt đường dẫn rồi khởi động lại polling theo phần trên:

```powershell
$env:SHOP_TERMS_PATH = (Resolve-Path -LiteralPath '.\temp.md').Path
```

`/terms` đọc snapshot từ file UTF-8 `.md`/`.txt` cục bộ tối đa 24000 bytes, chia trang
3000 bytes, plain text và tắt previews. Không cấu hình thì báo chưa sẵn sàng; file thiếu,
sai định dạng hoặc còn placeholder khiến polling từ chối startup trước khi tạo Bot/DB.
Không tải URL/UNC. Nội dung này phải được Owner hoàn thiện và chịu trách nhiệm; bot không
tự thẩm định pháp lý. Checkout consent và Telegram test/live acceptance thuộc T14;
refund/reconciliation đã có local ở T13. Yêu cầu payment support/terms tham chiếu
[Telegram Stars](https://core.telegram.org/bots/payments-stars).

## Hoàn tiền Stars và đối soát (T13)

OWNER/ADMIN có current `payments.refund` mở **Hoàn tiền Stars** trong `/admin`, `/refunds`
hoặc `/refunds PAYMENT_ID`. Queue 5 thanh toán/trang, PAYMENT_ID là UUID ledger nội bộ;
UI không trả raw Telegram charge, payer ID hoặc tham chiếu quyết định riêng tư. SUPPORT
và quyền đọc đơn không tự có quyền hoàn tiền. Đây là công cụ operator; tests dùng dữ liệu
giả/Telegram mock, chưa được nghiệm thu tài khoản thật.

- `/refund PAYMENT_ID`, xuống dòng rồi nhập tham chiếu quyết định đã duyệt theo chính
  sách shop (tối đa 500 UTF-8 bytes). Bot tạo preview toàn bộ Stars, payment/order và tác
  động lên entitlement; **Xác nhận** trong 10 phút mới thực hiện. Không nhận amount/payer/
  raw charge từ callback. Reference mã hóa bằng keyring hiện có; current grant, actor,
  snapshot trạng thái và deadline được kiểm tra lại trước gọi Telegram.
- Chỉ hoàn toàn bộ verified received charge bằng `refundStarPayment`. Charge chính MATCHED
  dùng receipt đã xác thực; charge thừa/unmatched cần `/verify_charge PAYMENT_ID [OFFSET]`.
  Mỗi lần đọc tối đa 100 Telegram transactions, khớp charge/payer/amount/payload/direction;
  OFFSET là số record bỏ qua, 0–1000000. Không sửa receipt gốc hoặc suy payer từ username.
- Trước API, commit intent SENDING và hold thuộc intent trên work chính. Không giữ DB
  transaction qua network. Confirm lặp không gọi lại. Chỉ positive API result hoặc refund
  event/transaction evidence khớp mới ghi SUCCEEDED. Refund charge chính chuyển REFUNDED
  và chặn worker/manual/resend; refund charge thừa giữ nguyên order/entitlement chính.
  Unit đã allocated, source snapshot và attempt history không tự restock/xóa/thu hồi.
- Timeout, mất ACK, kết quả không rõ hoặc SENDING quá 60 giây giữ UNCERTAIN và hold.
  `/reconcile_refund PAYMENT_ID [OFFSET]` chỉ xác nhận từ positive outbound refund evidence;
  một trang không thấy record không chứng minh chưa hoàn. Không auto retry, không có nút
  xóa quarantine để hoàn lại. API rejection được xác định chưa accepted cần yêu cầu/xác
  nhận mới; chỉ hold của intent đó được thả.
- Delivery/resend còn in-flight chặn refund. Sau hết lease, helper giữ UNCERTAIN.
  Nếu operator đã duyệt rõ rủi ro khách có thể đã nhận tài nguyên, dùng
  `/refund_uncertain PAYMENT_ID`, xuống dòng rồi nhập evidence/reference, đọc cảnh báo và
  xác nhận mới. Quyết định này cho phép refund, giữ nguyên uncertainty và lịch sử gửi;
  không tuyên bố đã thu hồi hàng hoặc cho phép blind resend.

Authenticated `refunded_payment` được lưu trước polling offset, kể cả chưa có profile hoặc
payment tới sau. Event sai amount/payload/provider được giữ để kiểm tra, không mark refund.
Receipt tới trước payment được áp dụng atomically khi payment được lưu; work giữ hold,
không mở lại delivery. Kết quả delivery tới muộn sau external refund chỉ thêm observation.
Verification/reconciliation lưu audit của chính staff trong cùng transaction; system events
giữ source/time trong immutable receipt ledger, không gán giả một staff đã thực hiện.

**Owner setup T13 local: không cần key/provider/server mới.** Dùng Bot API và keyring hiện có.
Trước test/live T14 cần bot/account, secure environment và chính sách refund do Owner chốt;
thông tin điều khoản/support còn theo phần T12. Không chạy polling/refund thật để thử các
lệnh này khi chưa chủ động cho phép hiệu ứng đó. API contracts tham chiếu
[refundStarPayment](https://core.telegram.org/bots/api#refundstarpayment),
[RefundedPayment](https://core.telegram.org/bots/api#refundedpayment) và
[StarTransaction](https://core.telegram.org/bots/api#startransaction).

## Public checkout và webhook (T14)

Trong chi tiết sản phẩm có sẵn hàng/nguồn, **Mua** tạo đơn quantity-one và giữ finite stock
15 phút; giá lấy từ DB và lưu snapshot. Replay cùng nút dùng cùng order/action. Trang đơn
cho đọc Điều khoản, đồng ý rồi mở invoice XTR, làm mới, hủy đơn chưa trả hoặc mở support.
Thiếu terms thì không nhận public checkout; terms đổi trong lúc confirmation mở thì phải
mở lại. SHA-256 terms đã đồng ý được lưu audit, không lưu nội dung terms/secret vào audit.

Invoice sent chưa là paid. DB receipt đúng mới tạo allocation/work; worker gửi riêng.
Invoice SENDING/UNCERTAIN không có nút phát lại, hướng dẫn `/paysupport`, không tạo đơn
khác để sửa ambiguous send. `/checkout ORDER_ID` hoặc Đơn hàng mở lại đơn của chính buyer.
Hủy/expiry không xóa receipt/history; late payment vẫn được giữ để đối soát.

`TELEGRAM_ENVIRONMENT=main` là mặc định; test cần đặt tường minh `test` với token test.
`telebotshop webhook` thay polling và đồng thời có health endpoints, cần `WEBHOOK_SECRET`
32–256 allowed ASCII, schema/Owner/keys/terms hợp lệ. Route POST `/telegram/webhook` kiểm
tra đúng một secret header trước đọc body, JSON unique fields, cap 256 KiB kể cả stream,
read timeout 2s, dispatch 20s và tối đa 32 concurrent requests. Response/logs cố định không
echo payload hoặc secrets; processing/commit errors trả 503. Financial receipt/refund commit
trước 200. Aiogram parse nằm trong infrastructure, HTTP adapter không import Telegram SDK.

HTTP health riêng vẫn chạy bằng `telebotshop http`, không cần bot secrets. App không tự
set/delete webhook. Setup/account, external registration và evidence checklist:
[BOTFATHER-SETUP](docs/BOTFATHER-SETUP.md). Telegram thật chưa được agent gọi;
production roles/TLS/shared rate limits/restore/deploy thuộc T18.

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

T14 có bộ nghiệm thu Telegram Test Server qua Telethon + Bot API, tách khỏi suite mặc định.
Setup optional group bằng `python scripts/setup_telegram_e2e.py`, rồi chạy
`python -m tests.e2e preflight`; login/commands/payment/refund và giới hạn an toàn nằm ở
[BOTFATHER-SETUP §9](docs/BOTFATHER-SETUP.md#9-nghiệm-thu-bằng-telethon--bot-api).
Giữ các `.env`/session local và dùng `uv run --no-sync` khi đã cài nhóm E2E.

Nguồn toolchain: [Python 3.12.15](https://www.python.org/downloads/release/python-31215/),
[uv trên PyPI](https://pypi.org/project/uv/0.13.4/),
[uv required-version](https://docs.astral.sh/uv/reference/settings/#required-version),
[uv build backend tích hợp](https://docs.astral.sh/uv/concepts/build-backend/#bundled-build-backend),
[aiogram release/constraints](https://pypi.org/project/aiogram/3.31.0/),
[aiogram polling contract](https://docs.aiogram.dev/en/dev-3.x/dispatcher/dispatcher.html).
