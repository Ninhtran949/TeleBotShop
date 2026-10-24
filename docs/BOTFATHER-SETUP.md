# Setup Telegram test và nghiệm thu T14

**Cập nhật nghiệm thu:** T14 chức năng thay Stars bằng Credit đã PASS theo yêu cầu Owner,
với actual Test bot/Owner session cho5types/6shapes, Credit refund/resend/support; xem
[R1–R8](plans/shop/t14-replacement-acceptance.md). Không cần Test Stars để đóng mốc chức năng.
Stars E3/E4 dưới đây giữ unchecked/PAUSED; đây là lịch sử, không phải trạng thái T14 chức năng.

**Owner24/10: Stars development/external acceptance PAUSED/BLOCKED.** E3/E4 chưa đạt;
giữ nguyên evidence bên dưới. Công việc hiện tại là Bank/QR + USDT → Credit → fulfillment
theo [Payment V2](PAYMENT-V2.md). Không cần xử lý Buyer2/Test Stars để chạy các local flows này.
Các hướng dẫn đăng nhập/thanh toán Stars bên dưới chỉ dùng khi Owner mở lại phạm vi đó.

Bot Test Server riêng và Owner đã được xác thực; runtime dùng database test có marker riêng.
**Gate E chưa hoàn tất.** Hướng nghiệm thu hiện tại là
[Telethon + Bot API qua CLI](#9-nghiệm-thu-bằng-telethon--bot-api), thay thao tác desktop thường
xuyên. [Mục10](#10-rà-lại-blocker-và-environment--24102026) ghi lần rà lại mới nhất;
mục6–9 giữ lịch sử và bằng chứng từng lượt, không suy kết quả mocked thành external PASS.

## Những thứ cần chuẩn bị

| Thành phần | Cần cho lần test đầu |
| --- | --- |
| Telegram | Account trong **Test Server** và bot tạo bởi BotFather trong chính môi trường đó; thêm account khách thứ hai để kiểm tra quyền. |
| Secrets | `BOT_TOKEN`, numeric `OWNER_ID`, keyring AES và dedup key riêng, lưu ở máy bạn. Không gửi chúng vào chat/Git. |
| Database | PostgreSQL local riêng cho bot, không dùng database disposable của Pytest ở port 55433. |
| Nội dung | Hoàn thiện `temp.md`, tên đơn vị/ngày hiệu lực và support contact; chỉ dùng hàng giả bạn có quyền phân phối khi test. |
| Provider/server | Stars không cần Stripe/bank/provider key. Polling cần máy chạy Python + Docker + Internet; chưa cần VPS/domain. Webhook tùy chọn cần HTTPS công khai. |

## 1. Tạo account và bot đúng Test Server

Theo [Telegram: dedicated test environment](https://core.telegram.org/bots/features#dedicated-test-environment),
trên Telegram Desktop mở Settings, giữ **Shift + Alt**, nhấp chuột phải **Add Account**,
chọn **Test Server** rồi tự đăng nhập/tạo account. Trên iOS, chạm Settings mười lần,
chọn Accounts → Login to another account → Test. Giữ mật khẩu/OTP trên thiết bị của bạn.

Trong account test đó, mở **@BotFather**, dùng `/newbot`, đặt tên/username và lưu token
an toàn. Bot/account test tách biệt hoàn toàn với môi trường main; `file_id` thuộc từng bot,
nên phải upload lại file/ảnh cho bot test. Không lấy token main để thử thanh toán.

Ở BotFather của bot test, có thể đặt `/setcommands` với danh sách công khai sau.
Menu admin xuất hiện trong `/admin` sau kiểm tra quyền ở server:

```text
start - Mở cửa hàng
shop - Danh mục sản phẩm
search - Tìm kiếm sản phẩm
orders - Đơn hàng của tôi
tickets - Ticket hỗ trợ của tôi
paysupport - Hỗ trợ thanh toán Stars
terms - Điều khoản mua hàng
admin - Quản trị theo quyền
```

## 2. Bootstrap và cấu hình local

Theo [README](../README.md), cài toolchain đã pin rồi chạy từ workspace root trong
PowerShell. Nếu chưa có alias `uv`, đặt lại trong mỗi terminal:

```powershell
Set-Alias uv (Resolve-Path .tools/uv/Scripts/uv.exe)
$env:UV_CACHE_DIR = Join-Path $PWD '.cache/uv'
uv sync --locked --no-build
if (Test-Path -LiteralPath .env) { throw '.env đã tồn tại; mở và bổ sung, không ghi đè.' }
Copy-Item -LiteralPath .env.example -Destination .env
notepad .env
```

Chỉnh các giá trị này trong `.env` riêng trên máy; các dấu `<...>` là chỗ bạn phải điền,
không phải giá trị chạy được. Token/key không đặt trong lệnh shell hoặc ảnh evidence:

```dotenv
APP_ENV=test
TELEGRAM_ENVIRONMENT=test
DATABASE_URL=postgresql+asyncpg://telebotshop_dev:dev-local-only@127.0.0.1:55432/telebotshop_dev
BOT_TOKEN=<token của bot Test Server>
OWNER_ID=<Telegram ID của chính account Owner test>
INVENTORY_ENCRYPTION_KEYS='{"1":"<base64 AES key 32 bytes>"}'
INVENTORY_CURRENT_KEY_VERSION=1
INVENTORY_DEDUP_KEY=<base64 dedup key 32 bytes, khác AES key>
LOW_STOCK_THRESHOLD=5
SHOP_TERMS_PATH=C:/Users/Admin/workspace/TeleBotShop/temp.md
```

**`APP_ENV=test` không chọn Telegram Test Server**. Phải đặt riêng
`TELEGRAM_ENVIRONMENT=test` cho cả ingress và worker; SDK chọn endpoint test chính thức.
Không trộn token, file IDs, DB hoặc keyring của hai môi trường.

Với database **mới**, tạo hai khóa ngẫu nhiên bằng thư viện chuẩn. Lệnh sau chỉ in keys ở
terminal local của bạn để điền vào `.env`; giữ kín và lưu bản sao bảo vệ cùng backup DB:

```powershell
uv run --no-sync python -c "import base64,secrets,json; key=lambda:base64.b64encode(secrets.token_bytes(32)).decode(); print(json.dumps({'INVENTORY_ENCRYPTION_KEYS':{'1':key()},'INVENTORY_DEDUP_KEY':key()}))"
```

Nếu DB đã dùng rồi, **reuse đúng keys cũ**, không tạo lại để sửa lỗi startup. Mất AES key
thì không đọc được hàng/ticket/evidence; đổi dedup key hoặc bỏ old key version bị DB guard
từ chối. `.env` được ignore nhưng vẫn là file nhạy cảm, không gửi hoặc đưa vào artifact.

### Chưa biết OWNER_ID

Điền token test vào `.env`, dừng mọi polling/webhook của bot này. Trong account Owner
test, gửi `/start` vào private chat với bot mới. Chạy đoạn sau; nó chỉ in ID người gửi
trong private updates hiện có, không in token/nội dung và không advance offset. Chọn ID
của chính bạn; nếu có nhiều ID, xác minh trước khi bootstrap Owner:

```powershell
@'
import asyncio, os
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TEST

async def main():
    async with Bot(os.environ["BOT_TOKEN"], session=AiohttpSession(api=TEST)) as bot:
        updates = await bot.get_updates(timeout=0, allowed_updates=["message"])
        ids = {u.message.from_user.id for u in updates
               if u.message and u.message.chat.type == "private"
               and u.message.from_user and not u.message.from_user.is_bot}
        for identifier in sorted(ids):
            print(identifier)

asyncio.run(main())
'@ | uv run --env-file .env --no-sync python -
```

Lần startup đầu pin Owner ID trong DB; đổi `OWNER_ID` về sau không chuyển chủ shop.

## 3. Migrate và chạy bot

Hoàn thiện các placeholders trong `temp.md` trước, rồi đặt `SHOP_TERMS_PATH`. Loader từ
chối file chưa hoàn thiện; thiếu terms thì public Buy không nhận đơn. Nội dung được load
lúc startup, nên phải restart ingress sau khi sửa. Việc dùng terms chính thức đã được
Owner chấp thuận; guide không tự điền danh tính hoặc sửa chính sách của Owner.

Start DB local cho bot, giữ tách biệt với test suite:

```powershell
docker compose -p telebotshop-t02 --profile dev up -d --wait postgres
uv run --env-file .env --no-sync alembic upgrade head
```

Head cần đạt `0014_notifications`. Migration không chạy tự động khi start app.
Trong hai terminal cùng workspace, đặt alias `uv` như trên rồi chạy lần lượt:

```text
uv run --env-file .env --no-sync telebotshop polling
uv run --env-file .env --no-sync telebotshop worker
```

Mỗi lệnh chạy ở terminal riêng. Polling nhận updates; worker giao hàng và gửi thông báo
vận hành. Dùng Ctrl+C để dừng. Chỉ một ingress/polling cho bot này. Polling từ chối webhook
đang tồn tại, không tự xóa hoặc drop updates.

Chạy batch dọn reservation định kỳ trong terminal thứ ba; lệnh chỉ dùng DB, không gửi
Telegram. Hiện chưa có scheduler tự động; khi thử checkout có thể chạy lại thủ công:

```text
uv run --env-file .env --no-sync telebotshop expire-orders
```

HTTP health tùy chọn: `uv run --env-file .env --no-sync telebotshop http`, rồi kiểm tra
`http://127.0.0.1:8000/health/ready`. Không cần mở port này ra Internet khi dùng polling.

Mở bot test: Owner và khách đều `/start`. Owner dùng `/admin`, `/setsupport USERNAME`
và xác nhận; khách có Tài khoản, Cửa hàng, Đơn hàng, Ticket và Điều khoản. Tạo category,
sản phẩm/giá, import kho và nguồn giao hàng theo [README](../README.md). Chuẩn bị đủ
`UNIQUE_KEY`, `LICENSE`, `DIGITAL_FILE`, `REUSABLE_RESOURCE` (text/document),
`MANUAL_FULFILLMENT`. File/ảnh phải upload thật vào **bot test**, không dùng `FAKE_*`
file IDs của automated fixtures. Không nhập hàng hoặc dữ liệu khách thật.

[Telegram Stars test payments](https://core.telegram.org/bots/payments-stars#testing-payments)
dùng dedicated test environment. Bot gửi invoice `XTR`, provider token rỗng; không cần
payment-provider key. Đọc terms, bấm Mua → Đồng ý điều khoản → thanh toán trong client
test, rồi chờ receipt và worker. Approval pre-checkout hoặc invoice đã gửi chưa là paid.

## 4. Webhook tùy chọn sau khi polling đã chạy

Chỉ cần mục này khi Owner muốn nghiệm thu ingress qua HTTPS. Owner chọn reverse proxy/
tunnel hoặc server, URL và TLS; không có nhà cung cấp bắt buộc. Đặt target nội bộ
`127.0.0.1:8000`, route công khai `/telegram/webhook`, giữ header secret, cap body 256 KiB,
tắt access/body/header logs chứa secrets; proxy timeout ít nhất 30s cho read2s + dispatch20s.
Giữ một ingress vì rate limit hiện trong process, chưa dùng Redis/shared quota.

Dừng polling, tạo secret riêng bằng `secrets.token_urlsafe(32)` trên máy, lưu
`WEBHOOK_SECRET` trong `.env` (32–256 ký tự `A-Z a-z 0-9 _ -`). Thêm `WEBHOOK_URL` là
HTTPS URL đầy đủ tới `/telegram/webhook`. App bỏ qua field URL này; đoạn đăng ký bên
dưới mới đọc nó. Chạy `uv run --env-file .env --no-sync telebotshop webhook`.

Theo [Bot API: setWebhook](https://core.telegram.org/bots/api#setwebhook), secret được
Telegram gửi trong header `X-Telegram-Bot-Api-Secret-Token`; server kiểm tra trước đọc
body. HTTP 200 cho financial update chỉ sau khi ghi DB thành công; lỗi commit trả 503
để Telegram có thể gửi lại. Registration là thao tác Owner chủ động chạy, app không tự làm:

```powershell
@'
import asyncio, os
from urllib.parse import urlsplit
from telebotshop.bootstrap import create_bot
from telebotshop.infrastructure.config import WebhookSettings

async def main():
    settings = WebhookSettings()
    assert settings.telegram_environment == "test"
    url = os.environ["WEBHOOK_URL"]
    parsed = urlsplit(url)
    assert (parsed.scheme == "https" and parsed.hostname
            and parsed.path == "/telegram/webhook"
            and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment)
    async with create_bot(settings) as bot:
        ok = await bot.set_webhook(
            url=url, secret_token=settings.webhook_secret.get_secret_value(),
            allowed_updates=["message", "callback_query", "pre_checkout_query"],
            max_connections=16, drop_pending_updates=False,
        )
        print("registered" if ok else "not registered")

asyncio.run(main())
'@ | uv run --env-file .env --no-sync python -
```

Giữ nguyên updates khi đổi transport. Muốn quay về polling, Owner dừng webhook process
và chủ động gọi SDK `delete_webhook(drop_pending_updates=False)` với cùng bot test,
rồi start polling. Không dùng `drop_pending_updates=True` để sửa lỗi receipt. Không chạy
polling và webhook đồng thời. Đây chưa phải runbook production/deployment T18.

## 5. Evidence gate E — kết quả quan sát thật

Ghi phiên bản Git + hash working tree, ngày, transport, `TELEGRAM_ENVIRONMENT=test`, SDK,
schema head và người thực hiện. Giữ token/DSN/keys/charge IDs/payload/file IDs/Telegram
IDs ngoài evidence public. Chỉ lưu UUID nội bộ, kết quả và ảnh đã che dữ liệu nhạy cảm.

| Case cần quan sát trên Telegram test | Kết quả / evidence |
| --- | --- |
| `/start`, browse/search, terms, profile, orders, support/paysupport; nút Back/Home | PARTIAL:22 CLI cases PASS trên Test Server, gồm các lệnh khách, browse/search/detail Back/Home,11 admin views và own-customer lookup. Chưa kiểm tra mọi nút hoặc user thứ hai. |
| Buy mỗi loại, đọc/đồng ý terms; invoice XTR giá snapshot; pre-checkout và receipt paid | PARTIAL: T14_KEY có terms/invoice Test XTR1 và1 approval DB;0 payments. Chưa chứng minh Bot API answer được chấp nhận hoặc receipt paid; năm types còn PENDING. |
| Unique key + license đúng một entitlement; digital file + reusable text/document đúng nguồn | BLOCKED:0 paid fulfillments; thiếu Test Stars. |
| Manual: khách nhận notice; staff có quyền xem queue, xác nhận evidence rồi DELIVERED | BLOCKED: queue read PASS; notice/completion/DELIVERED chưa có paid order. |
| Click/receipt lặp, hết hàng, hai khách tranh key cuối; không giao hai lần | PARTIAL: một buyer giữ chỗ key cuối làm stock0/ẩn Buy, hủy unpaid trả lại stock1. Stale Buy callback bị Telegram DataInvalid trước bot; DB không thêm đơn. Concurrent duplicate/receipt/two-buyer chưa nghiệm thu. |
| Sai quyền/đơn người khác/revoked staff bị từ chối; admin Customers lọc đúng khách | PARTIAL: Customers button/Home và own-ID lookup PASS; cross-user/IDOR/revoked staff chưa nghiệm thu. |
| Restart worker/gián đoạn gửi: giữ UNCERTAIN để đối soát, không gửi mù hoặc cấp key khác | PENDING |
| Ticket/reply đến đúng khách; alert Owner về tồn kho/manual/uncertain; không lộ payload | PARTIAL: actual Owner-own ticket/assignment/staff reply/thread PASS;3 LOW_STOCK,1 TICKET_ACTIVITY,1 TICKET_REPLY SENT. Chưa chứng minh routing giữa hai người hoặc manual/uncertain alerts. |
| `/refund PAYMENT_UUID` có confirmation; một refund test thành công, order REFUNDED và key vẫn giữ | BLOCKED: chưa có matched payment để refund. |
| Nếu dùng webhook: secret sai bị từ chối, receipt commit trước ack, kiểm tra pending/errors đã che secrets | N/A phiên này chọn polling; local webhook contracts PASS, không claim public HTTPS acceptance. |

Không sửa trực tiếp ledger, reset uncertain hoặc tạo receipt giả để lấp evidence.
Khi Owner setup xong, báo **đã có bot/account test, cấu hình local và terms hoàn thiện**;
không gửi giá trị secrets. Các thao tác Telegram phải nằm trong phạm vi Owner đã giao;
authorization smoke ở mục 6 không bao gồm thanh toán/hoàn tiền trên server chính.
Chỉ tick T14 cha khi evidence E đầy đủ và review core không còn Critical/High.

## 6. Smoke giới hạn trên server chính — Owner giao ngày 22/10/2026

Owner quan sát BotFather trên Test Server không phản hồi `/start`/`/newbot`; chưa có
bằng chứng xác nhận nguyên nhân hoặc sự cố toàn dịch vụ. Owner chọn tiếp tục kiểm tra
`/start`, menu và quyền Owner với bot mới ở server chính, tắt checkout.

Cấu hình local dùng `APP_ENV=development`, `TELEGRAM_ENVIRONMENT=main`, database dev
riêng ở port 55432 và schema `0014_notifications`. Giữ `SHOP_TERMS_PATH` ở dạng comment;
`BotSettings.shop_terms_path` phải là `None`. `CheckoutService.prepare/pay` từ chối khi
không có terms, trước tạo đơn/gửi invoice. Test tập trung
`test_public_checkout_refuses_missing_or_changed_terms_before_accessing_database` đạt.

Preflight trước startup: main `getMe` thành công; không có webhook; database chưa ghim Owner và có
0 users/products/orders/payments/invoice intents. Owner gửi `/start` kèm challenge một
lần trực tiếp cho bot; chỉ ghim ID từ private message khớp challenge, không chọn người
gửi đầu tiên hoặc suy ID từ token. Không ghi Telegram ID/challenge/token vào evidence.

Chỉ chạy polling sau khi xác minh Owner; worker không cần cho smoke menu. Không thử
Stars, refund hoặc fulfillment trên server chính. Kết quả live cần ghi riêng sau khi
quan sát thật; gate E và T14 cha tiếp tục PENDING.

### Bằng chứng smoke ngày 22/10/2026

- Private challenge khớp đúng một tài khoản; `OWNER_ID` chỉ lưu trong `.env` bị Git
  ignore. Bootstrap ghim đúng account đó với role OWNER; incoming `/start` thật đã
  cập nhật profile trong PostgreSQL. Không giả lập incoming update để ghi kết quả này.
- Polling chạy bằng Python của `.venv` ở background; PID/logs ở ignored
  `.cache/main-smoke/`. Đây là tiến trình local, chưa có service tự khởi động lại.
- `setMyCommands` và `getMyCommands`: 8 lệnh đọc lại đúng cấu hình. `AccessService`
  trả đủ quyền Owner; truy vấn admin cho ID giả chưa đăng ký bị `AccessDenied`.
- Gọi use case và `admin_menu` hiện có, gửi menu cho chính Owner bằng `sendMessage`:
  Telegram trả thành công. Owner sau đó xác nhận trong chat đã thấy tin chào/menu
  và thao tác được trên thiết bị sau yêu cầu thử `/admin` và điều hướng menu.
- `CheckoutService.prepare/pay` với terms `None` đều trả `InvalidAction`; focused unit
  command `uv run --no-sync pytest tests/unit/test_checkout.py::test_public_checkout_refuses_missing_or_changed_terms_before_accessing_database -q`
  đạt **1 passed**. `SHOP_TERMS_PATH` vẫn unset; không chạy worker hoặc financial API.
- Smoke tin chào/menu/điều hướng trên thiết bị có xác nhận của Owner; không suy kết quả
  này thành kiểm tra toàn bộ nút hoặc toàn shop. Gate E/Stars, fulfillment/refund,
  second-account authorization và production deployment vẫn chưa nghiệm thu.

## 7. Tiếp tục T14 trên Test Server

Owner yêu cầu tiếp tục nghiệm thu; sau câu hỏi xác minh môi trường, Owner xác nhận token
hiện có trong `.env` vẫn là bot Telegram bình thường của lần smoke. Chưa có token Test
Server local để chạy E. Cấu hình smoke trên server chính vẫn giữ riêng trong `.env`.

Đã chuẩn bị `.env.telegram-test` bị Git ignore, keyring/dedup key riêng và database local
`telebotshop_telegram_test` trên PostgreSQL dev port 55432. Database này tách khỏi
`telebotshop_dev` của bot main và database disposable port 55433 của automated tests;
có ownership marker riêng, migration head `0014_notifications`, không reset/xóa dữ liệu cũ.

Owner mở `.env.telegram-test`, chỉ điền `BOT_TOKEN` từ BotFather trong Test Server rồi
báo đã xong; không gửi token vào chat. `OWNER_ID` để trống tới khi xác minh bằng private
challenge. Không sao chép ID từ account main. JSON keyring cần được bọc dấu nháy đơn
để `uv --env-file` giữ nguyên dấu nháy kép trong JSON.

**Preflight ngày 23/10/2026:** `.env.telegram-test` đã có `BOT_TOKEN`, nhưng so sánh local
xác nhận giá trị trùng hoàn toàn token main trong `.env`. Chưa gọi API bằng cấu hình này.
Cần thay riêng `BOT_TOKEN` bằng token bot Test Server; giữ nguyên keyring/DB đã chuẩn bị.

Terms cho phiên nội bộ đặt trong ignored `.cache/telegram-test/terms-test.md`, ghi rõ
chỉ dùng dữ liệu giả/Test Server. Không sửa hoặc công bố `temp.md` của Owner. Khi token
sẵn sàng sẽ xác minh endpoint/bot, private challenge, rồi mới bootstrap Owner, seed hàng
giả và start polling/worker theo cấu hình test. Checkout/receipt/delivery/refund chỉ được
ghi evidence sau quan sát thật; checklist E ở mục 5 tiếp tục PENDING.

Preflight đã xác minh keyring/terms nội bộ hợp lệ, đúng DB có marker và head0014, 0 users/
products/orders/payments/invoice intents. Chưa start polling/worker test hoặc gọi financial API.
Đề xuất read-only getMe với token `.env` sang endpoint test bị automatic approval review
chặn trước thực thi vì chưa rõ authorization dùng credential khác môi trường. Sau khi
Owner xác nhận token main, không retry hoặc đổi endpoint để vượt rejection. Cần token
của bot tạo trong Test Server theo mục 1, điền trực tiếp vào `.env.telegram-test`.

### Blocker BotFather do Owner quan sát

Owner đã đăng nhập được tài khoản Test Server. Ban đầu search không mở được BotFather;
sau khi gửi username trong Saved Messages và bấm trực tiếp thì mở được profile/chat.
Owner báo `/start` và `/newbot` có dấu tick nhưng không nhận phản hồi, dùng tài khoản
đăng ký bằng số điện thoại thật. Chưa có token test và chưa có xác minh độc lập về nguyên
nhân; không kết luận Test Server lỗi toàn dịch vụ hoặc tài khoản bị hạn chế chỉ từ mô tả này.

Tài liệu [Telegram](https://core.telegram.org/bots/features#dedicated-test-environment)
yêu cầu account/bot test riêng. [Trao đổi TDLib ngày 23/06/2025](https://github.com/tdlib/td/issues/3370)
đề cập đăng ký bằng số thật khi synthetic test accounts bị tắt; đó là bằng chứng lịch sử,
không phải xác nhận trạng thái BotFather hôm nay. Không yêu cầu số test trùng số account main.
BotFather chưa trả token thì chưa thể start shop test hoặc nghiệm thu Stars E. Cấu hình/
database/keyring local đã sẵn sàng; không đổi `.env` bot main để xử lý triệu chứng này.

### Chẩn đoán trực tiếp bằng Computer Use ngày 23/10/2026

Owner cài Windows Computer Use; đã dùng backend local của plugin để quan sát Telegram
Desktop, giới hạn vào đúng tiến trình/cửa sổ Telegram. Profile của chat có các lệnh
`/start`/`/newbot` cũ cho thấy username **`@BotFatherBot`**, dù tên hiển thị là BotFather.
Tìm đúng **`@botfather`**, kiểm tra username trong profile: cuộc chat này còn nút START.
Nhấn START nhận ngay phản hồi hướng dẫn `/newbot`, `/mybots` và các lệnh quản trị bot.
Không suy sự im lặng của chat cũ thành sự cố Test Server.

Đọc metadata mạng của riêng Telegram cũng thấy kết nối `149.154.167.40:443`, endpoint
Test DC2 trong [mã nguồn Telegram Desktop](https://github.com/telegramdesktop/tdesktop/blob/dev/Telegram/SourceFiles/mtproto/mtproto_dc_options.cpp).
Đây là bằng chứng kết nối test hiện diện; chưa thay thế xác minh token bằng Test API.
Ở bước chẩn đoán này chưa tạo bot/credential mới hoặc sửa `.env`/`.env.telegram-test`.
Bước tiếp theo là tạo bot Test Server riêng, lưu token trực tiếp vào cấu hình test,
xác minh endpoint rồi private challenge. Stars/fulfillment/refund E vẫn pending.

### Bot test đã tạo theo xác nhận Owner ngày 23/10/2026

Owner cho phép tạo và cấu hình **TeleBotShop T14 Test**. Qua đúng `@botfather`, đã tạo
`@telebotshop_t14_1023_6c9a_bot`, đọc token vào bộ nhớ bằng UI Automation và chỉ ghi vào
ignored `.env.telegram-test`; token khác bot main. Test API `getMe` khớp username/tên bot,
`getWebhookInfo` không có webhook. Không gọi token main sang endpoint test.

Mở bot mới bằng chính tài khoản Test Server; update `/start` thật tới đúng bot. Private
challenge ngẫu nhiên khớp đúng một sender không phải bot, cùng chat/user ID và còn hạn;
`OWNER_ID` chỉ lưu local, không lấy ID từ người gửi đầu tiên. Giữ updates, chưa advance offset.
`setMyCommands`/`getMyCommands` xác nhận 8 lệnh; mô tả bot ghi rõ nội bộ/Test Server/dữ liệu giả
và được API đọc lại đúng. Đây chưa phải ứng dụng xử lý `/start`, checkout hoặc Stars E.

Automatic approval review chặn đề xuất chụp vùng chat BotFather vì có thể lưu token vào ảnh.
Đề xuất không thực thi; chuyển sang UIA với đầu ra metadata/boolean, không chụp vùng token.
Token, Owner ID và nội dung challenge không nằm trong evidence public.

**Preflight trước phiên khắc phục (lịch sử):** `asyncpg 0.32.0` có đủ ba native modules và checksum khớp RECORD
đã cài, nhưng import `pgproto.cp312-win_amd64.pyd` báo `An Application Control policy has
blocked this file`, cả trong và ngoài sandbox của lệnh agent. Code Integrity có event **3033**
cho module đó với parent `python.exe`; chưa xác định tên policy, không tự kết luận là Smart
App Control hay App Control for Business. Chưa cài lại package, đổi dependencies hoặc sửa policy.

Owner kiểm tra trong **PowerShell riêng ngoài Codex**; lệnh không nạp `.env` hoặc in secrets:

```powershell
C:\Users\Admin\workspace\TeleBotShop\.venv\Scripts\python.exe -c "import asyncpg; print('asyncpg OK')"
```

Báo import thành công hay cũng bị chặn để phân biệt giới hạn tiến trình agent và chính sách
Windows toàn máy. Nếu policy cũng chặn thao tác ngoài Codex, Owner/admin cần xem log/signature
và quyết định cách cho phép component theo [hướng dẫn Microsoft](https://learn.microsoft.com/en-us/windows/security/application-security/application-control/app-control-for-business/operations/appcontrol-debugging-and-troubleshooting).
Không tự tắt bảo vệ Windows hoặc đổi vị trí DLL để vượt chặn.

Docker Desktop ban đầu chưa chạy. Đã start bằng `docker desktop start --detach`, xác minh
Compose labels/project/workspace/service/port rồi chỉ start `telebotshop-t02-postgres-1`
trên localhost55432. Read-only `psql` xác nhận đúng role/DB/marker, head0014 và 0 users,
products, orders, payments, invoice intents; không reset volume, sửa schema hoặc ghi ledger.

Phiên này có launcher local ignored `.cache/telegram-test/start-runtime.py`: kiểm tra hash
config, TEST endpoint/bot, DB/role/port/marker và internal terms trước gọi polling/worker
hiện có. Compile/config guard đạt; chưa chạy runtime vì lỗi native import. Owner ID đã xác minh
và lưu config, **chưa bootstrap Owner/keyring vào DB**; chưa seed hàng hoặc start polling/worker.
Gate E/T14 cha tiếp tục pending.

## 8. Khắc phục Windows Code Integrity và chạy lại runtime — 23/10/2026

Owner giao chẩn đoán read-only, reinstall locked wheel, giữ policy và tiếp tục nghiệm thu
Test Server; không cho phép commit/push/deploy. Baseline337 file hashes, main/test env,
index và HEAD được chụp trong ignored `.cache/t14-runtime-fix/`, không chứa secret values.

**Environment/reproduction:** Windows11 build26200/AMD64, interpreter workspace
`.venv/Scripts/python.exe`, CPython3.12.15, uv0.13.4, installed và locked asyncpg0.32.0.
Lệnh dưới PASS ngay trước mọi thay đổi và PASS lại sau reinstall; không tái hiện được lỗi cũ:

```powershell
& .venv/Scripts/python.exe -c "import asyncpg; print('asyncpg OK', asyncpg.__version__)"
& .venv/Scripts/python.exe -c "import asyncpg, telebotshop.bootstrap; print('runtime imports OK')"
```

Exception lịch sử là `ImportError: DLL load failed while importing pgproto: An Application
Control policy has blocked this file.` Không tạo stack trace mới hoặc gọi import hiện tại
là thất bại. CodeIntegrity Operational events **3033 +3077** xác định chính xác
`.venv/Lib/site-packages/asyncpg/pgproto/pgproto.cp312-win_amd64.pyd`; record3077/252663,
status **0xc0e90002**, policy **VerifiedAndReputableDesktop**, version27555.1000.240208,
GUID **{0283ac0f-fff1-49ae-ada1-8a933130cad6}**. Đây là policy SAC theo
[Microsoft inbox policy mapping](https://learn.microsoft.com/en-us/windows/security/application-security/application-control/app-control-for-business/operations/inbox-appcontrol-policies).
Read-only registry `VerifiedAndReputablePolicyState=1` trước/sau kiểm tra; không tắt Defender,
SAC, Memory Integrity, WDAC, không sửa policy, unblock/rename/copy DLL. `CiTool.exe -lp -json`
trả Access Denied0x80070005 cả khi tool được chạy ngoài sandbox; event log vẫn cung cấp
policy name/GUID, không cần đổi quyền hoặc policy để lấy thêm.

Các read-only diagnostic commands đã dùng (events được lọc local theo TeleBotShop/asyncpg,
chỉ xuất file/policy/status, không dump toàn bộ log ứng dụng):

```powershell
Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-CodeIntegrity/Operational';Id=3033,3077,3099;StartTime=(Get-Date).AddDays(-2)} -MaxEvents 120
& "$env:WINDIR/System32/CiTool.exe" -lp -json
Get-ItemProperty -LiteralPath 'HKLM:/SYSTEM/CurrentControlSet/Control/CI/Policy' -Name VerifiedAndReputablePolicyState
```

**Clean reinstall:** tạo requirement từ Windows cp312 wheel entry trong `uv.lock` với
SHA-256 `cd7157a86817730c3239bc687abf8186a471525d695e225c187b9a523a808a98`, rồi chạy:

```powershell
$env:UV_CACHE_DIR=Join-Path $PWD '.cache/uv'
& .tools/uv/Scripts/uv.exe pip install --python .venv/Scripts/python.exe --reinstall --no-deps --no-build --no-cache --require-hashes --requirements .cache/t14-runtime-fix/asyncpg-wheel.txt
```

Sandbox network fetch bị từ chối; rerun qua approved tool escalation tải fresh official
`files.pythonhosted.org` wheel, uninstall/install đúng1 package, version0.32.0 giữ nguyên.
Cả3 `.pyd` khớp installed RECORD và SHA-256 trước/sau giống nhau. `uv.lock`, manifest,
architecture, source và keyring không đổi. Vì import đã PASS trước reinstall, **không kết luận
reinstall là nguyên nhân hết chặn**; lý do Windows cho phép lại module ở phiên này chưa rõ.

**Runtime và tests:** Docker Desktop initially stopped; `docker desktop start --detach`,
kiểm tra Compose labels/project/path/ports rồi `docker start telebotshop-t02-postgres-1
telebotshop-t02-postgres-test-1`. Suite dùng disposable DB marker/role riêng tạilocalhost55433;
Telegram acceptance dùng `telebotshop_telegram_test`, marker `telebotshop:telegram-test:v1`,
head0014, localhost55432. Không SQLite, reset volume hoặc dùng database main cho suite.

| Actual command/check | Result |
| --- | --- |
| `uv run --no-sync pytest tests/unit tests/contract tests/architecture --junitxml=.cache/t14-runtime-fix/c.xml -q` | FAIL trước tests: launcher `pytest.exe` bị policy4551; events3033/3077 mới, cùng SAC policy. |
| `uv run --no-sync python -m pytest tests/unit tests/contract tests/architecture --junitxml=.cache/t14-runtime-fix/c.xml -q` | **496 passed**,18.36s; zero failure/error/skip. |
| `uv run --no-sync python -m pytest tests/integration --junitxml=.cache/t14-runtime-fix/d.xml -q` | **446 passed**,535.94s; APP_ENV=test/exact TEST_DATABASE_URL allowlist/marker, zero failure/error/skip, gồm6 core-shop cases. |
| `uv lock --check`; `uv run --no-sync python scripts/check_architecture.py` | PASS;50 packages resolved, import matrix giữ nguyên. |
| `python scripts/verify_harness.py`; `python -B -m unittest discover -s tests -p test_harness.py -v` | PASS integrity và **6 tests**. |
| Guarded `.venv/Scripts/python.exe .cache/telegram-test/start-runtime.py polling` và `... worker` | PASS: distinct token/Test API identity/no webhook, DB marker/head, Owner/keyring bootstrap; chạy native. |

`uv` trong bảng là `.tools/uv/Scripts/uv.exe`; Python là workspace `.venv/Scripts/python.exe`.
Module entrypoint là cách gọi Pytest tiêu chuẩn, không sửa security policy hoặc wrapper bị
chặn. Không có asyncpg block mới trong phiên kiểm tra. **Chưa cần Linux fallback** vì native
app và PostgreSQL suite đã chạy; nếu native extension bị chặn trở lại thì dùng Docker Linux
hoặc WSL2 với chính Python/dependency pins, không giảm bảo mật Windows.

**External E hiện có:** actual pending `/start` được polling xử lý; UIA xác nhận welcome/menu.
Seed bằng existing confirmed use cases:7 fake products giá1 test Star, đủ năm types,
reusable text/document và riêng SKU last-key race. Real fake document upload qua bot test
trả file ID hợp lệ, chỉ lưu encrypted resource; không ghi file ID vào evidence. DB có1 user,
7 products,0 orders/payments/fulfillments;3 low-stock alerts SENT qua actual Test API.
Đây chưa là checkout/payment/fulfillment acceptance.

| T14 criterion | Current result |
| --- | --- |
| a — Public checkout/terms/price/availability | Local C/D PASS; external Buy/XTR/pre-checkout/receipt **PENDING**. |
| b — Test transport/authenticated bounded webhook | Local C/D PASS; actual test identity/no-webhook/native polling PASS. Webhook external **N/A** trong phiên chọn polling. |
| c — Durable operational notifications | Local C/D PASS;3 actual low-stock API sends PASS; support/manual/uncertain external **PENDING**. |
| d — Five types, shared dispatcher, auth/races/migrations/review | C496/D446/H6 PASS lại;6 core cases dùng mocked Telegram, không thay thế E. |
| E — Full client flows, five-type paid delivery, refund, races/IDOR/recovery | **PENDING**; từng case ở mục5, không tick T14 cha. |

Owner hiện có1 Test account, đã nhận hướng dẫn chuẩn bị account thứ hai cho two-buyer/
permission cases. Khi attempted `/admin`, GUI guard thấy expected bot chat không mở nên
**không gửi lệnh**, không can thiệp login/OTP hoặc thao tác account/app khác. Cần mở đúng
bot trên Test Server để tiếp tục client checkout/Stars/refund và account thứ hai cho race/
IDOR; không cần thêm payment provider key hoặc VPS. Main `.env`, test `.env`, secrets,
HEAD/index và user work giữ nguyên. Polling/worker test tiếp tục chạy; không commit/push/deploy.

Sau suite, read-only check xác nhận0 `tbs_test_*` schemas và0 client khác; đã chạy
`docker stop telebotshop-t02-postgres-test-1` để dừng riêng disposable service. Dev PostgreSQL
và dedicated acceptance DB/polling/worker vẫn chạy, không xóa volume.

### Tiếp tục với một Test account khi account thứ hai bị giới hạn đăng nhập

Owner báo chưa mở được account Test Server thứ hai vì Telegram báo đăng nhập quá nhiều.
Chưa có error code hoặc wait duration chính xác; không suy ra thời gian mở lại hoặc retry
login tự động. Tài khoản main đang đăng nhập trên server chính không tương tác bot test:
[hai môi trường tách biệt](https://core.telegram.org/bots/features#testing-your-bot).
Dùng số điện thoại của account main để đăng ký vào Test Server vẫn là tạo một Test account
riêng. Giữ session test đã có; không đổi bot token/endpoint sang main để lấp gate E.

Không cần account thứ hai cho toàn bộ nghiệm thu: tiếp tục checkout/delivery/refund một
buyer trước; two-buyer stock race, IDOR và staff/revocation vẫn pending tới khi có account2.
Đã quay lại session test cũ mà không login/OTP/account changes, tìm đúng bot T14 và gửi
actual `/admin`: client hiện đầy đủ quyền Owner/11 mục admin. Actual `/search T14 FAKE KEY`
hiện đúng một fixture giá1 Star. Evidence UIA và ảnh chỉ capture trực tiếp vùng tin công khai
của bot, không sidebar/BotFather/header; không chụp rồi mới redact vùng credential.

Click tự động vào nút xem fixture chưa đổi màn hình. Move-only calibration khớp vị trí
vật lý, foreground PID/HWND đúng, cả Telegram và automation không elevated, không thấy
modal/error text; SendInput trả2 events nhưng chưa quan sát được chuyển view. Chưa kết
luận là lỗi callback của ứng dụng. Owner được nhờ bấm tay đúng nút xem chi tiết để phân
biệt automation với app/client; **không phải nút thanh toán**. Trong lúc chờ không đổi UI
hoặc thử checkout mù. Read-only DB xác nhận1 user/7 products/0 orders/payments/fulfillments;
polling/worker sống, stderr rỗng. Stars/refund E và T14 cha vẫn pending.

## 9. Nghiệm thu bằng Telethon + Bot API

Owner giao rà và hoàn thiện T14 ngày23/10/2026. [ADR-0005](adr/0005-telegram-e2e-user-client.md)
chọn Telethon1.45.0 cho test tooling; aiogram/runtime và các version runtime cũ giữ nguyên.
Không gọi Computer Use cho các luồng protocol thông thường. Bộ E2E không nằm trong testpaths
mặc định, không import SDK user-client vào `src/` và không tự login hoặc tự mua khi chạy C/D.

### Setup local và chạy

`api_id`/`api_hash` từ [API development tools](https://my.telegram.org) nằm trong ignored
`.env.telegram-e2e` với tên `T14_API_ID`, `T14_API_HASH`. Owner đã điền; kiểm tra cấu trúc
PASS, không in giá trị. Token bot/keyring/DSN vẫn ở `.env.telegram-test`; không sao chép main.
Owner có thể đặt số Test Server ở `Phone_Test_Server` trong `.env.telegram-test`; E2E
đọc riêng trường này cho actor `owner`, validate và giữ dưới SecretStr, không in số.
Actor `buyer2` vẫn nhập số riêng. Nếu không có cấu hình số, terminal dùng prompt ẩn.
Owner tự nhập OTP/2FA tại terminal khi login, không gửi vào chat. Session chỉ ở
`.cache/telegram-test/mtproto/{owner,buyer2}.session`; không lấy Desktop tdata/session main.

```powershell
.venv\Scripts\python.exe scripts/setup_telegram_e2e.py
.venv\Scripts\python.exe -m tests.e2e preflight
.venv\Scripts\python.exe -m tests.e2e login --actor owner
.venv\Scripts\python.exe -m tests.e2e navigation --actor owner
.venv\Scripts\python.exe -m tests.e2e purchase --sku T14_KEY
.venv\Scripts\python.exe -m tests.e2e purchase --sku T14_LICENSE
.venv\Scripts\python.exe -m tests.e2e purchase --sku T14_FILE
.venv\Scripts\python.exe -m tests.e2e purchase --sku T14_TEXT
.venv\Scripts\python.exe -m tests.e2e purchase --sku T14_DOC
.venv\Scripts\python.exe -m tests.e2e purchase --sku T14_MANUAL
.venv\Scripts\python.exe -m tests.e2e support
.venv\Scripts\python.exe -m tests.e2e support-pair
.venv\Scripts\python.exe -m tests.e2e refund --sku T14_KEY
```

Các lệnh purchase/refund có tác dụng phụ trên **Test Server**, chỉ dùng fixture FAKE giá1 XTR.
Chạy từng case, kiểm tra kết quả trước case tiếp. Không chạy song song login và client khác
trên cùng session. `expire` chỉ gọi use case expiry hiện có để đóng đơn unpaid quá hạn.
`login --actor buyer2` cần một Test user độc lập; không retry liên tục khi Telegram giới hạn
đăng nhập, không dùng account main thay user Test.
`support-pair` yêu cầu operator Owner và cả hai session Owner/Buyer2 đã authenticated trước
bất kỳ message/ticket nào. Buyer2 tạo ticket, Owner assign/reply, Buyer2 phải nhận đúng
metadata notification và đọc reply từ own thread. Không tự login hoặc yêu cầu OTP.

Preflight chặn main endpoint/token, database ngoài đúng host/port/name, thiếu database
ownership marker/schema head và transport webhook ngoài polling đã chọn. MTProto kiểm tra
session endpoint trước connect và `test_mode` trước login; Owner/user thứ hai được kiểm tra
ID riêng. Invoice message/form phải cùng merchant, Test flag, XTR và đúng1 Star trước pay.
Payment/refund checkpoint được ghi bền trước effect, timeout giữ nguyên để đối soát; không
xóa checkpoint hoặc tự gửi lại. Kết quả chỉ lưu case/status/time/source hash/order UUID nội
bộ ở `.cache/t14-e2e/evidence/`, không body, phone, charge/file IDs hoặc payload tài nguyên.
Nếu đơn đã expiry và không còn trong active lookup, CLI vẫn kiểm tra payment fence của các
đơn cũ cùng buyer/SKU trước tạo đơn mới. Không dùng expiry để vượt checkpoint chưa đối soát.

Telethon phụ thuộc pyaes1.6.1 chỉ có sdist. Helper setup kiểm tra đúng PyPI/hash đã review
`02c1b1405c38d3c370b085fb952dd8bea3fadcee6411ad99f312cc129c536d8f`, chỉ cho build package
này và chính project, chặn source builds của các locked packages còn lại. Setup.py chỉ gọi
packaging; setuptools build constraint80.9.0. Không blanket-enable builds hay đổi Windows
security policies. Audit53 packages: không known CVE, có advisory trạng thái `rsa` archived;
giữ test-only, không đưa vào runtime/production và review lại khi nâng Telethon.

### Checkpoint đã xác minh trong phiên CLI

| Gate/case | Kết quả hiện tại |
| --- | --- |
| Native asyncpg/runtime | PASS:0.32.0, native imports/connect,90 RECORD hashes khớp; polling/worker restart qua launcher có guard. |
| C + H |569 unit/contract/architecture PASS26.95s, gồm73 tests mới;73 tests E2E-tooling cũng PASS khi chặn import Telethon.6 harness PASS ở checkpoint CLI trước. |
| D core |6 cases PostgreSQL thật PASS12.71s, Telegram mocked. D446 đầy đủ của checkpoint trước vẫn là lịch sử, không báo như vừa chạy lại. |
| Lock/audit/build/architecture | PASS;4 test-only dependencies mới,0 runtime version đổi; wheel/sdist không private paths, wheel không test-SDK dependencies. |
| Test API/database preflight | PASS đúng bot test, marker/head0014;1 user,7 fake products,3 EXPIRED orders,1 approval,0 payments/fulfillments,1 support ticket. |
| Đơn/invoice cũ | Hai invoice SENT/unpaid đã qua expiry use case ở hai lần riêng, mỗi lần đóng1 đơn/0 recovery; probe thứ ba reserve rồi cancel trước mở invoice, trả stock1. Không sửa ledger;3 payment/top-up fences vẫn ATTEMPTED. |
| Session user Telethon | Owner login PASS lúc14:48:42Z ngày23/10/2026; exact Owner/test_mode đã xác minh, không dùng OTP từ chat. Buyer2 chưa authorized. |
| E navigation |22 actual CLI cases PASS:11 customer/admin foundation,10 admin views, thêm Customers button/Home. Không suy thành mọi nút hoặc mọi quyền đều PASS. |
| E checkout/delivery/manual/refund/support | Support Owner-own-thread PASS; support-pair có tooling nhưng actual run dừng trước effects vì Buyer2 chưa authorized. Actual paid receipt/delivery/manual/refund còn BLOCKED. |
| E two-buyer/IDOR/revoked staff | BLOCKED: Owner cho phép một public Test FAKE Buyer2; cả fixed-code forms trả PhoneCodeInvalidError trước signup, chưa tạo account. |
| E restart/uncertain/duplicate receipt | PENDING external evidence; stale callback bị Telegram từ chối không thay thế duplicate-update/receipt hoặc paid-worker recovery. |
| E notifications |3 LOW_STOCK,1 TICKET_ACTIVITY,1 TICKET_REPLY SENT; support cùng Owner không thay thế routing tới buyer khác. Manual/uncertain còn PENDING. |
| E webhook |N/A cho phiên polling; local authenticated webhook contracts đã có, không claim public webhook. |

Login đầu trả `ConnectionNotInitedError` trước bước nhập code. Sau khi process đó kết thúc,
probe read-only trên session Test trả Config(test_mode=true), NearestDc và401
AuthKeyUnregistered cho GetState (đúng khi chưa login), không gửi lại mã. Phát hiện prompt
`input/getpass` đồng bộ chặn network loop;2 regression tests tái hiện heartbeat bị chặn,
đã sửa sang `asyncio.to_thread` và thêm test bảo vệ diagnostic khỏi serialize error/request
chứa dữ liệu riêng. Ba tests PASS. Lần kế tiếp trả TypeError tại SendCodeRequest serialize;
tái hiện offline: input rỗng/sai format được SDK parse thành None, không có network call.
Đã validate trước API, ẩn số và cho sửa input tại terminal. Lần sau đã gửi request đến
Telegram, dừng ở PhoneCodeInvalidError/SignInRequest; ConnectionNotInited không xuất hiện
trong lần đó, chưa suy thành bảo đảm mọi phiên. Login helper mới chỉ yêu cầu code một lần,
cho tối đa3 lần Owner tự nhập sửa mã; timeout/RPC khác dừng ngay, mã trống không gọi sign_in.
2FA chỉ hỏi sau SessionPasswordNeededError; không đọc password từ Desktop hoặc tự dùng
`Local_password`. Mã Owner từng dán vào chat không được đưa vào script/log hoặc dùng để login.
Lần kế tiếp vẫn gặp ConnectionNotInited ở GetUsersRequest trong sign_in sau khoảng chờ
Owner, trước khi SDK gửi code tới SignInRequest. Nguyên nhân server quên init chưa xác định;
không khẳng định prompt async đã chữa lỗi external. Probe cold connection chỉ đọc cho
GetUsers401/AuthKeyUnregistered, wrapped InitConnection/GetConfig PASS, GetUsers sau đó401
đúng khi chưa login. Đã thêm read-only initialization trước send_code_request và ngay sau
prompt code/2FA, dùng SDK layer/client metadata, kiểm tra test_mode; không retry auth effect.
Regression mô phỏng state mất init khi chờ input: RED trước fix, PASS sau fix. Actual method
đã chạy read-only trên Test Server PASS. Phiên login sau đó đã PASS với code Owner nhập local;
Owner xác nhận Local_password là Desktop lock password, không phải account2FA.
Mitigation đã qua một actual Owner login và các navigation cases; nguyên nhân Test Server
mất init chưa được chứng minh. Error log giữ class/request/frame và canonical RPC tag đã lọc,
không raw error text/locals/request fields.73 tests tooling chạy không cần SDK user-client;
no-SDK probe PASS73/1.50s, không thay thế external acceptance.
Protocol theo [Telegram initConnection](https://core.telegram.org/api/invoking#saving-client-info).

### Kết quả external và blocker còn lại — 23/10/2026

Latest navigation source hash là
`708ae766183aca3200b215f12ffc5eb5ad036680c1fe50edf22e1de914389fd3`:
21 cases lúc15:47–15:48Z, Customers/Home riêng lúc15:58Z. Support own-thread PASS lúc15:07Z,
hash `65317398278bb2186315ba7e140b9d11d4a8e5eac3e5e6ff0b6365509d3fce07`.
Các hash gắn đúng từng lượt, không gọi navigation là đã chạy trên source sửa guard sau đó.
Unpaid probe có source hash
`9e857f648f63b5cf0aa3435fa28e67ed863b42586e6285ffd18a1d98b6362ab9`.
Latest preflight sau support-pair có source hash
`5650b097a216236f7d1c4b4319f68fde0bcffba346936a947265a3a82628ef56`.

Actual Bot API invoice message ID khác ID ở phía user MTProto. CLI đã sửa cách tìm invoice
bằng merchant + `start_param=order_UUIDHEX`; yêu cầu đúng một match và validate Test/XTR1.
Delivery tooling dùng merchant/payload/time window của persisted send attempt, không dùng
merchant message ID như buyer-side ID. Sáu focused correlation regressions PASS; actual
paid delivery vẫn chưa chạy. Clock skew bound2s có shortcut note, không tự nới để lấy PASS.

Lần mua T14_KEY gửi SendStarsForm lúc14:59:30Z trả generic BadRequestError; lúc đó chưa lưu
exact RPC identifier nên không kết luận mã lỗi cụ thể. Có1 pre-checkout approval trong DB,
không suy thành Telegram đã nhận answer hoặc đã thanh toán. Fences được giữ nguyên.
Readback15:59:11Z: Owner Test wallet0 Stars/0 nanos, full buyer history0/no next page,
merchant transactions0, payments0, fulfillments0;2 orders EXPIRED qua existing use case.
Final readback16:10:34Z vẫn wallet/history/payments/fulfillments0;3 orders EXPIRED sau probe
unpaid dưới đây, không còn overdue unpaid. Polling/worker vẫn chạy, stderr0; native import
asyncpg0.32.0 PASS và read-only SAC state1, không có policy change.

Native Test top-up form được xác minh `invoice.test=True`,15 Stars, provider Stripe và
`pk_test_` key; merchant là Test bot `izpremiumbot`, khác shop. Thử public test-token trả
BadRequestError nhưng thiếu exact tag. Sau khi tokenization bằng published fake card tạo
Stripe TEST token thật (`livemode=False`), một submission riêng trả chính xác
**BOT_PRECHECKOUT_TIMEOUT**, status400/SendPaymentFormRequest. Chưa có credit quan sát được;
không gọi đây là bằng chứng lỗi pre-checkout của TeleBotShop. Giữ cả hai top-up fences và
fence shop purchase ở ATTEMPTED, không retry/reset. Không dùng tiền/thẻ/Stars server chính.
Đối chiếu [Telegram Test Stars](https://core.telegram.org/bots/payments-stars#testing-payments)
và [Stripe test fixtures](https://docs.stripe.com/testing?numbers-or-method-or-token=tokens).

Buyer2: automatic approval review chặn lần đầu trước execution vì chưa có approval riêng
cho external identity. Owner sau đó cho phép tạo đúng một Test FAKE account; script đã chạy.
Attempt đầu trả PhoneCodeInvalidError/SignInRequest. Read-only check xác nhận session chưa
authorized. Corrected attempt theo fixed five/six-digit public Test codes cũng bị cùng lỗi,
dừng trước signup; không có account Buyer2 đã tạo/xác minh. Giữ session/attempt record riêng,
không xoay thêm số, không thay Owner hoặc dùng account main. Cơ chế public Test accounts:
[Telegram auth](https://core.telegram.org/api/auth#test-accounts),
[Telethon Test Servers](https://docs.telethon.dev/en/stable/developing/test-servers.html).
Chưa biết nguyên nhân server từ chối; không suy thành rate-limit hoặc account ban.

Final review bổ sung guard cho đơn expired:2 focused regressions RED trước fix/GREEN sau fix;
read-only check với actual DB/fence T14_KEY PASS, action client/bot cố ý không cung cấp nên
không thể phát sinh purchase trong check. Không sửa application runtime/ledger.

Unpaid probe T14_RACE lúc16:09Z có3 actual subcases PASS: reserve đúng1 key/invoice READY,
stock0/ẩn Buy trong product detail, cancel qua UI trả AVAILABLE1/không payment hoặc delivery.
Intentional duplicate dùng callback cũ sau khi keyboard đã đổi bị Telegram trả
DataInvalidError/GetBotCallbackAnswerRequest trước bot. DB vẫn1 order/reservation; probe
exit1 vì assertion yêu cầu callback UI success. Đây chưa chứng minh concurrent duplicate
đến application hoặc duplicate receipt; giữ case đó pending, không sửa app theo giả định.
Order test nội bộ `95a2eb08-9c8e-4477-acb4-205d07728b0b` đã EXPIRED qua cancel use case;
không giữ kho để chờ Owner và không chạy lại probe tạo order khác.

Support-pair tooling bổ sung sau checkpoint đó:7 regressions RED/GREEN cho wrong operator,
wrong/same actor, bot actor, wrong merchant, reply đọc bởi Buyer2 và metadata-only notification.
Mutation riêng bỏ exact notification-body guard bị1 test bắt/6 pass; main source giữ bytes.
Actual `.venv/Scripts/python.exe -m tests.e2e support-pair` exit1 ở session precondition:
Buyer2 chưa authorized, không gửi message/ticket hoặc yêu cầu code. Readback16:21:09Z vẫn
1 support ticket,3 EXPIRED orders,0 Stars/history/payments/fulfillments;3 fences ATTEMPTED.
Đây là actual fail-closed evidence, không phải PASS cross-user support hoặc notification.

Read-only blocker audit16:24:55Z xác nhận cùng hai điều kiện vẫn thiếu: Test wallet0
Stars/0 nanos và Buyer2 session chưa authorized. DB vẫn3 orders/0 payments/0 fulfillments/
1 ticket; không gửi thêm effect. Evidence ở ignored `.cache/t14-e2e/blocker-audit.json`.
Các gate còn lại cần Test Stars và user Test thứ hai; không tiếp tục tạo account, gửi OTP,
retry financial effects hoặc đánh dấu T14 done để thay cho thay đổi external state.

**Owner setup còn cần:** Test wallet có Stars dùng được và một Test user độc lập authorized
cho Buyer2. Không cần payment provider key, VPS hoặc security-policy change. Account main,
receipt giả, seed PAID và local mocks không thay hai đầu vào này. Sau khi có hai điều kiện,
đối soát các fences trước khi tiếp tục E; T14 cha vẫn chưa done, T15–T18 không mở.

| Exact external command đã chạy | Kết quả |
| --- | --- |
| `.venv/Scripts/python.exe -m tests.e2e preflight` | PASS đúng Test target/head/marker; source hash lưu riêng trong private preflight.json. |
| `.venv/Scripts/python.exe -m tests.e2e navigation --actor owner` |21 cases PASS; Customers/Home thêm1 case trong private readback probe. |
| `.venv/Scripts/python.exe -m tests.e2e support` | PASS support.owner_thread; không cross-user. |
| `.venv/Scripts/python.exe -m tests.e2e support-pair` | BLOCKED Buyer2 session unauthorized trước message/ticket; cross-user cases chưa PASS. |
| `.venv/Scripts/python.exe -m tests.e2e login --actor owner` | PASS authenticated Owner tại Test Server; OTP/2FA chỉ nhập local. |
| `.venv/Scripts/python.exe -m tests.e2e purchase --sku T14_KEY` | Invoice/approval có; SendStarsForm BadRequestError,0 matched payments; không chạy lại. |
| `.venv/Scripts/python.exe -m tests.e2e expire` | Hai lượt riêng, mỗi lượt expired_unpaid=1/recovered_invoices=0. |
| `.venv/Scripts/python.exe .cache/t14-e2e/tokenize-test-card.py` | PASS tạo Stripe TEST token, không payment. Token chỉ lưu private. |
| `.venv/Scripts/python.exe .cache/t14-e2e/topup-test-stars.py` | BadRequestError/SendPaymentFormRequest, exact tag chưa được lưu; không credit quan sát được. |
| `.venv/Scripts/python.exe .cache/t14-e2e/topup-test-stars-tokenized.py` | Read-only corrected preflight PASS, không submit. |
| `.venv/Scripts/python.exe .cache/t14-e2e/topup-test-stars-tokenized.py --submit` | BLOCKED BOT_PRECHECKOUT_TIMEOUT; fence giữ nguyên. |
| `.venv/Scripts/python.exe .cache/t14-e2e/setup-public-test-buyer.py` | BLOCKED PhoneCodeInvalidError trước signup; exclusive corrected-attempt record chặn rerun. |
| `.venv/Scripts/python.exe .cache/t14-e2e/final-readback.py` | PASS wallet/history/DB aggregates; lần probe đầu Customers PASS nhưng len(None) lỗi, đã sửa read-only probe và chạy lại PASS. |
| `.venv/Scripts/python.exe .cache/t14-e2e/unpaid-checkout.py` |3 subcases PASS, stale callback DataInvalid; exit1 ở final duplicate UI assertion. Cancel hoàn tất, stock phục hồi; concurrent duplicate chưa PASS. |

Owner bổ sung `Phone_Test_Server`/`Local_password` làm fingerprint file test đổi. Đã chứng
minh bằng SHA256 rằng bỏ đúng hai metadata fields này và chuẩn hóa line endings tái tạo
được original bytes/hash; mọi runtime field giữ nguyên. Chỉ sau proof mới cập nhật private
runtime fingerprint kèm provenance, giữ guard chặt và lệnh configured_settings PASS.
Lệnh đã chạy: `.venv\Scripts\python.exe .cache/t14-e2e/refresh-env-guard.py`.
Preflight mới PASS cùng bot/head/marker,0 payments/fulfillments; vẫn chưa có nghiệm thu pay.

Review guard: isolated control23 PASS, mutation bỏ check Telegram test environment bị1
test bắt đúng; source chính không đổi. `git diff --check` của working changes exit0;
`git diff HEAD --check` exit2 do whitespace có sẵn trong SQL literal migration0014 đã áp
dụng và Markdown hard breaks của `temp.md`. Hai file giữ nguyên bytes, không sửa migration
hoặc nội dung Owner để làm mất cảnh báo. Đây không phải failure của runtime/financial tests.

Lượt D đầu đã chạy các assertions nhưng process exit1 do quyền ghi `.pytest_cache` lúc
session finish. Lượt PASS dùng cache riêng, giữ nguyên warnings-as-errors:

```powershell
# Export APP_ENV=test/TEST_DATABASE_URL của disposable localhost55433 theo README trước.
.venv\Scripts\python.exe -m pytest tests/unit tests/contract tests/architecture -q --junitxml=.cache/t14-e2e/c.xml
.venv\Scripts\python.exe -m pytest tests/unit/test_telegram_e2e_operations.py -q -o cache_dir=.cache/t14-e2e/pytest-support-green
.venv\Scripts\python.exe -m pytest tests/integration/test_core_shop.py -q -o cache_dir=.cache/t14-e2e/pytest-d --junitxml=.cache/t14-e2e/d-core.xml
.tools\uv\Scripts\uv.exe lock --check
.tools\uv\Scripts\uv.exe audit --locked
.tools\uv\Scripts\uv.exe build --no-sources
.venv\Scripts\python.exe scripts/check_architecture.py
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe scripts/verify_harness.py
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_harness.py -v
```

Final checks: Ruff check PASS; format330 files unchanged; import architecture PASS.
C569 passed/26.95s; support-pair guards7 passed/0.48s; focused payment-fence tests5
passed/0.58s ở checkpoint trước. No-SDK probe73 passed/1.50s. Baseline HEAD/index/main `.env`, source/migrations/original
skills/`temp.md` bytes và mọi dependency version cũ giữ nguyên. Không stage/commit/push/deploy.

Sync ban đầu gặp WinError5 vì runtime giữ DLL asyncpg, để lại package thiếu file/RECORD.
Đã dừng đúng process T14, reinstall registry wheel0.32.0 với locked SHA256 ở mục8 bằng
`uv pip install --reinstall --no-deps --no-build --require-hashes`, kiểm tra đủ90 RECORD
hashes/native imports và khởi động lại. Không có thay đổi policy; không suy đây là nguyên
nhân hay cách chữa chung cho SAC. **T14 cha vẫn unchecked đến khi mọi case E bắt buộc đạt.**

Lệnh registry-wheel restore đã chạy trong phiên CLI (requirements chỉ chứa asyncpg0.32.0
và hash locked `cd7157a86817730c3239bc687abf8186a471525d695e225c187b9a523a808a98`):

```powershell
.tools\uv\Scripts\uv.exe pip install --python .venv\Scripts\python.exe --reinstall --no-deps --no-build --require-hashes --requirements .cache/t14-e2e/asyncpg-locked.txt
```

## 10. Rà lại blocker và environment — 24/10/2026

Owner yêu cầu kiểm tra endpoint trước, Buyer2 headless, tài liệu Test Stars và chạy lại
payment mocks với PostgreSQL thật. **T14 cha, E3 và E4 vẫn unchecked.** Không mua Stars,
submit payment/refund, tạo receipt giả hoặc gọi production API trong lượt này. Không sửa
application source, dependencies, `.env` hay Windows security policies; chỉ cập nhật báo cáo.

### Telegram Test API và tách môi trường

HEAD T13 còn gọi `Bot(token)` mặc định production, nhưng index/working tree T14 đang chạy
đã có `create_bot` dùng `AiohttpSession(api=TEST|PRODUCTION)` của aiogram **3.31.0**.
Polling và worker đều qua factory này; `TelegramRefundGateway` nhận cùng bot/session.
`TELEGRAM_ENVIRONMENT=test` chọn server độc lập với `APP_ENV=test`. Kiểm tra actual settings
của cả BotSettings/WorkerSettings trả đúng API/file templates:

```text
https://api.telegram.org/bot{token}/test/{method}
https://api.telegram.org/file/bot{token}/test/{path}
```

Các URL khớp [Telegram Test API](https://core.telegram.org/bots/features#dedicated-test-environment)
và [aiogram 3.31 source](https://docs.aiogram.dev/en/v3.31.0/_modules/aiogram/client/telegram.html).
Read-only `getMe` PASS với bot test đã xác minh, webhook rỗng. Guarded polling/worker đang
chạy; source hash vẫn `5650b097a216236f7d1c4b4319f68fde0bcffba346936a947265a3a82628ef56`.
Không có bằng chứng trộn production/Test DC trong runtime hiện tại, không cần viết lại cấu hình.

| Môi trường | Cấu hình và database được xác minh |
| --- | --- |
| Main | `.env`, Telegram main, `telebotshop_dev` tại localhost55432; chỉ đọc cấu hình local, không gọi API main. |
| Telegram acceptance | `.env.telegram-test`, Telegram test, `telebotshop_telegram_test` tại localhost55432; marker riêng/head0014. |
| Simulated integration | `APP_ENV=test`, `telebotshop_test` tại localhost55433; role riêng và marker `telebotshop:isolated-tests:v1`. |

Token, encryption keyring và dedup key main/test đều khác nhau. Probe phụ dùng python-dotenv
ban đầu báo keyring main invalid; kiểm tra lại bằng **đúng loader `uv --env-file .env --no-sync`**
cho kết quả main InventorySettings hợp lệ. Không coi parser phụ là bằng chứng runtime main hỏng.
`loader-separation.json` supersede trường main validation trong `environment-probe.json`.

### Buyer2 — BLOCKED

Owner Test session authenticated, Buyer2 chưa authorized; cả hai session trỏ Test DC2.
Chạy headless đúng fixture public Test đã được duyệt, giữ nguyên số đã chọn, chỉ **một**
SendCodeRequest. Server trả SentCodeTypeSms/length5; fixed Test code độ dài5 và6 trên cùng
yêu cầu đều bị `PhoneCodeInvalidError/SignInRequest`, dừng trước signup, không tạo account.
Không xoay số, không sửa Owner hoặc dùng desktop. Cơ chế đối chiếu:
[Telegram Test accounts](https://core.telegram.org/api/auth#test-accounts) và
[Telethon 1.45 Test Servers](https://docs.telethon.dev/en/stable/developing/test-servers.html).
Chưa xác định vì sao server từ chối; không suy thành account ban, rate limit hay Test DC ngừng hỗ trợ.
Owner xác nhận **chưa có tài khoản/session Test thứ hai** để thay thế. Không tự retry.

### Stars Test — hỗ trợ payment test, chưa chứng minh nạp được

[Telegram Testing Payments](https://core.telegram.org/bots/payments-stars#testing-payments)
xác nhận thanh toán Stars có thể test trong dedicated environment; digital XTR không cần
provider token. Đây không phải tài liệu cấp số dư miễn phí. Tài liệu
[Buying or gifting Stars](https://core.telegram.org/api/stars#buying-or-gifting-stars) mô tả
`getStarsTopupOptions` → `getPaymentForm(InputInvoiceStars)` → payment flow; không thấy
cơ chế faucet/free grant trong các trang chính thức đã kiểm tra.

Actual read-only lúc **00:40 ngày24/10/2026 UTC+7**: wallet0 Stars/0 nanos, full history0;
19 top-up options, thấp nhất15 Stars. GetPaymentForm trả `invoice.test=True`, currencyVND,
provider Stripe và test-provider-key guard khớp; merchant khác TeleBotShop. **Có Test top-up
form không chứng minh đã cấp/nạp Stars.** Lượt này không tokenization hoặc submit tài chính.
Lỗi top-up lịch sử `BOT_PRECHECKOUT_TIMEOUT` ở mục9 thuộc Test merchant, chưa chứng minh
lỗi shop; ba effect fences ATTEMPTED giữ nguyên, cần đối soát trước bất kỳ retry được phép nào.

### Automated và external tách riêng

| Luồng | Automated trong lượt này | External Test Server |
| --- | --- | --- |
| Payment | PASS: contract và PostgreSQL receipt/pre-checkout/dedup/commit-failure/races; Telegram/payment mocks. | BLOCKED: wallet0, DB0 matched payments; approval/invoice cũ chưa phải receipt paid. |
| Fulfillment | PASS: sáu core scenarios cho đủ năm ProductType và reusable document; entitlement/allocation/worker/faults trên PostgreSQL thật, send mock. | BLOCKED:0 paid fulfillments; chưa có delivery paid, manual completion hoặc paid recovery. |
| Refund | PASS: dispatcher/use case, primary/extra, rollback/lost ACK và concurrent refund/manual/resend trên PostgreSQL thật, gateway mock. | BLOCKED: chưa có payment thật trên Test Server để refund/đối soát. |

Các lệnh đã chạy từ workspace root; scripts/results ở ignored `.cache/t14-20261024/`:

```powershell
.venv/Scripts/python.exe .cache/t14-20261024/environment-probe.py
.venv/Scripts/python.exe .cache/t14-20261024/buyer2-login.py
.venv/Scripts/python.exe -m pytest tests/contract tests/unit/test_telegram_environment.py -q -o cache_dir=.cache/t14-20261024/pytest-contract --junitxml=.cache/t14-20261024/contract.xml
.venv/Scripts/python.exe .cache/t14-20261024/run-integration.py
```

Environment probe exit0; Buyer2 exit1/BLOCKED như trên. Contract + endpoint unit: **139 passed
trong9.95s**, không failures/errors/skips. Full integration: **446 passed trong718.66s**, không
failures/errors/skips; đây là lượt D mới, không tái dùng D446 lịch sử. Runner chỉ đặt đúng
fixture DSN local đã có và gọi `pytest tests/integration -q` với cache/JUnit riêng; không đổi tests.
JUnit: `contract.xml`, `integration.xml`; tổng hợp `automated-summary.json`.

PostgreSQL18.6 official pinned image, disposable service `postgres-test` được start riêng;
postflight xác minh đúng DB/role/marker,0 test schemas còn lại,0 clients khác. Sau suite đã
stop đúng container test, trả lại trạng thái ban đầu; không xóa volume hay reset DB acceptance/main.
Native Windows Python/asyncpg chạy hết suite; không cần đổi runtime sang WSL/Docker hoặc đổi policy.
Environment/readback chỉ thấy3 orders/0 payments/0 fulfillments/1 support ticket như checkpoint cũ.

**Để đóng T14:** cần Buyer2 Test account/session hợp lệ khác Owner và số dư Test Stars dùng
được qua cơ chế đã xác minh; chưa có hướng nạp miễn phí được chứng minh. Sau đối soát fences,
cần chạy đủ E: năm types/reusable text+document, paid receipt/delivery/manual/refund, hai buyer
cho quyền/tranh kho/support routing, duplicate/restart/uncertain recovery và review evidence.
Giữ matrix mục5 và tiêu chí plan, không thay bằng simulated PASS. Chưa cần thêm provider key,
VPS hoặc Computer Use cho hướng polling/MTProto. Không commit/push/deploy.
