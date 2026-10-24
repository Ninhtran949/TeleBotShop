# T14 — nghiệm thu chức năng thay Stars bằng Payment V2

**Kết quả: PASS chức năng local/sandbox, ngày24/10/2026.** Không phải nghiệm thu nhận tiền thật,
production hoặc Telegram Stars. Source: working tree trên `dev`, HEAD `198b6ed`, không commit.

## Quyết định và phạm vi Owner

Owner yêu cầu: “nếu logic có thể thay thế được star rồi thì nên nghiệm thu và cho pass qua”.
Theo quyết định này, mốc T14 hiện tại nghiệm thu **Bank/USDT top-up → Credit → order →
fulfillment/refund**, dựa trên [Payment V2](payment-v2.md) và ADR-0006. Không tiếp tục treo
mốc chức năng này vì thiếu Test Stars hoặc Buyer2 Test DC.

Các checkbox Stars E3/E4 cũ giữ nguyên unchecked/PAUSED như lịch sử; chúng không trở thành
PASS nhờ Credit. Hosted SePay, actual USDT/chain proof và go-live vẫn là các gate riêng bên dưới.
Không chuyển kết quả mocked/simulated thành bằng chứng provider đã nhận tiền.

## Matrix nghiệm thu

| AC | Bằng chứng actual | Kết quả |
| --- | --- | --- |
| R1 — Độc lập Stars | WalletCheckoutService không dùng invoice/pre-checkout/Stars sender; runtime Stars=false, Credit/Bank/USDT=true. Test bot API được kiểm tra Test endpoint/identity, tất cả phiên acceptance chặn Stars methods trước network. Database không có invoice, approval hoặc charge Stars. | PASS |
| R2 — Top-up → Credit | Bank: lệnh `/topup bank`, QR/text, raw HMAC webhook ký local, replay2lần; USDT: lệnh `/topup usdt`, immutable quote, simulated verified deposit + reconciliation replay. Native mismatches/token/network/finality/TTL/manual review/auto races và rollback/lost ACK có tests. | PASS local/simulated; không phải hosted-provider PASS |
| R3 — Ledger/checkout | Integer double-entry, balance projection, không âm, unique charge/operation, một order/payment method; snapshot credit price, atomic debit/payment/entitlement/work. Double buy, cùng buyer mua đồng thời, hai buyer tranh key cuối, IDOR/grants và audit được kiểm tra trên PostgreSQL thật. | PASS |
| R4 — Giao hàng thật trên Telegram Test | KEY, LICENSE, DIGITAL_FILE, REUSABLE_RESOURCE dạng text/document, MANUAL_FULFILLMENT: đủ5types/6shapes. User session thật quan sát payload đúng trong cửa sổ send; hai tài liệu được tải bằng MTProto và so đúng bytes FAKE. Manual notice và staff confirmation thật qua bot, evidence thao tác FAKE. | PASS |
| R5 — Lịch sử/refund/reversal | `/orders ORDER_UUID` hiển thị credit; cả6đơn thực hiện Credit refund qua command + callback, sau đó reversal riêng bằng use case, đóng từng vòng ledger về0credit của actor test. Không bank/crypto outgoing request. | PASS |
| R6 — Resend/restart/support | Key primary đã DELIVERED được đối soát read-only sau restart, không mua/gửi primary lại. Explicit resend gửi cùng key, giữ nguyên primary attempt/entitlement; support create/assign/staff reply/read thread qua Test bot. Native uncertain/recovery/authorization/cross-user tests có trong full suite. | PASS |
| R7 — Chống ghép quote đã đóng | Live acceptance phát hiện lần nạp USDT sau bị chặn bởi quote cũ CREDITED/REJECTED còn trong TTL. Sửa matching chỉ xét active intents và vẫn giữ thời gian/route/asset/token/amount/finality. Hai regression PostgreSQL fail trước fix, pass sau fix; event manual đang review được reconcile lại đúng một lần, không tạo top-up hoặc purchase khác. | PASS |
| R8 — Gates/preservation | Fresh609unit/contract/architecture +510PostgreSQL +6harness, không fail/error/skip; lint/format/architecture/lock/sync/build/wheel runtime/audit. Giữ HEAD/index, private configs, old migrations/ADRs/harness; không policy Windows changes hoặc deployment. | PASS |

## Môi trường và phương pháp

- CPython3.12.15, aiogram3.31.0, asyncpg0.32.0, PostgreSQL18.6; versions/lock giữ nguyên.
- Pytest dùng `telebotshop_test:55433`, mỗi case schema riêng; full510cases chia3process,
  mỗi process170cases, cache/JUnit riêng. Không SQLite hoặc mock transaction/concurrency.
- Runtime thật dùng `telebotshop_payment_v2_sandbox:55432` + ownership marker + keyring riêng,
  `.env.payment-v2`. Không migrate database Telegram cũ. Inventory, tài liệu, top-ups và credit
  đều FAKE; actor là Owner Test Server đã đăng nhập từ trước, không xin OTP hoặc tạo account.
- `run_polling` và `run_worker` thực chạy, Bot API gửi/nhận thật qua Test endpoint. Telethon
  dùng existing Test session. Wrapper audit vẫn gọi SDK request gốc, không mock Telegram.
- Payment inputs được mô phỏng tường minh: locally signed Bank callback hoặc
  `SettlementSource.SIMULATED` cho USDT. Không gọi SePay transaction API, không chuyển USDT,
  không mua Stars và không gửi production message.
- Bot API message IDs và MTProto user IDs không được giả định trùng nhau. Reuse matcher
  `tests/e2e/delivery.py::select_delivery`, đối chiếu sender + attempt time + payload/bytes.
  Không retry primary send vì assertion/observation timeout; kiểm tra DB trước mọi resume.
- Sáu ca live chạy qua các lần resume có checkpoint:5ca đã pass được giữ, chỉ tiếp tục intent
  chưa credit khi gặp lỗi. Counters Bot API trong JSON là phiên resume cuối; bằng chứng từng
  order/ledger và kiểm tra chặn Stars được giữ riêng, không cộng counters thành tổng giả.

## Commands và artifacts

Evidence được ignore trong `.cache/t14-replacement-20261024/`, không chứa credential values.

```powershell
.venv/Scripts/python.exe -m pytest tests/unit tests/contract tests/architecture -q --tb=short -o cache_dir=.cache/t14-replacement-20261024/pytest-c --junitxml=.cache/t14-replacement-20261024/c.xml
.venv/Scripts/python.exe -X utf8 .cache/t14-replacement-20261024/run-d.py
.venv/Scripts/python.exe -m pytest tests/integration/test_topups_v2.py tests/integration/test_payment_v2_safety.py tests/integration/test_payment_v2_flows.py -q --tb=short -o cache_dir=.cache/t14-replacement-20261024/pytest-usdt-green --junitxml=.cache/t14-replacement-20261024/usdt-green.xml
.venv/Scripts/python.exe -X utf8 .cache/t14-replacement-20261024/preflight.py
.venv/Scripts/python.exe -X utf8 .cache/t14-replacement-20261024/telegram-credit.py
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe scripts/check_architecture.py
.venv/Scripts/python.exe scripts/verify_harness.py
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_harness.py -v
.tools/uv/Scripts/uv.exe lock --check
.tools/uv/Scripts/uv.exe sync --locked --no-build --inexact --offline
.tools/uv/Scripts/uv.exe build --no-sources
.tools/uv/Scripts/uv.exe audit --locked
```

`run-d.py` collect toàn bộ510cases; `d-final-commands.json` chứa exact3pytest commands,
`d-final-1/2/3.xml` mỗi170pass. `c.xml`609pass, `usdt-green.xml`30pass cho affected suites.
`preflight.json`, `telegram-credit.json`, checkpoint/failure records và sanitized runtime log
ghi actual Test transport/effects. `verified-results.json` kiểm tra JUnit set bằng đúng tập
cases được collect, đủ6live cases,0Stars API/production effects. Không coi riêng manifest là
bằng chứng thay cho actual XML/database/user-side observations.

Build có156source modules đúng bytes; isolated wheel import155modules (trừ __main__),
no pytest/Telethon/network và poisoned settings; native asyncpg/AES-GCM pass. Audit53packages
không có known vulnerabilities, còn1adverse archived `rsa` trong optional Telethon group cũ.
Self-review đã kiểm tra correctness, architecture, security và bounded reads; không có
Critical/High còn mở trong scope chức năng này. Không có hosted CI hoặc independent reviewer.

## Hậu kiểm ba findings ngày24/10

Review sau nghiệm thu phát hiện ba lỗi chưa được các case ban đầu bao phủ. Các số609/510
và bằng chứng Telegram phía trên là snapshot nghiệm thu trước review, không phải kết quả
revalidation của bản sửa này.

| Finding | Bản sửa và bằng chứng hồi quy |
| --- | --- |
| P1 — Stars tắt vẫn approve invoice cũ | Composition root truyền cùng flag vào PreCheckoutService; paused trả false trước UoW. Hai case PostgreSQL qua runtime dispatcher kiểm tra invoice còn hạn với/không có approval cũ: không approve hoặc kéo dài TTL, giữ approval cũ. Payment/refund receipt lặp vẫn durable đúng một lần, order REFUNDED và key vẫn ALLOCATED. Unit test chặn truy cập DB khi paused. |
| P2 — Revoke vượt adjustment đang chạy | Grant/revoke lấy exclusive access lock trước kiểm tra quyền; financial use cases giữ shared lock đến hết transaction. Race dùng hai physical PostgreSQL connections: giữ staff adjustment sau kiểm tra quyền, revoke bị lock timeout SQLSTATE55P03; adjustment hoàn tất +321Credit, sau đó revoke thành công và lệnh mới bị AccessDenied. Không hủy một adjustment đã commit hợp lệ. |
| P2 — Callback Bank lặp thiếu ACK | Wallet navigation ACK trước edit; chỉ bỏ qua TelegramBadRequest có nội dung message is not modified. Bốn contract cases Bank/USDT × unchanged/other rejection đều có2ACK cho2lần bấm; lỗi edit khác vẫn được ném ra cho transport guard. |

TDD thực chạy trước sửa: `red-c.xml`4failed, `red-d.xml`3failed đúng các finding;
`red-unit.xml`1failed vì service chưa có pause flag. Sau sửa: `green-c.xml`37passed,
`green-d.xml`50passed. Đây là automated/mock Telegram + PostgreSQL thật, không có API
Telegram hoặc payment provider thật trong phiên sửa lỗi.

Artifacts mới ở `.cache/payment-v2-review-20261024/`; không ghi đè bằng chứng nghiệm thu cũ.
Hồi quy đầy đủ: **614unit/contract/architecture +513PostgreSQL +6harness pass**, không
fail/error/skip. `d-final-commands.json` giữ exact3pytest commands, mỗi nhóm171pass; `c.xml`
và `d-final-1/2/3.xml` là kết quả thực chạy. Cố ý đảo pause guard làm regression unit fail;
khôi phục đúng bytes source và chạy lại case đạt. Lint/format/architecture/lock/build pass;
wheel khớp156source modules và import4module bị sửa từ chính wheel; sdist không có private
runtime artifacts. Không chạy lại audit dependency vì manifest/lock không đổi.

Lệnh hồi quy đầy đủ:

```powershell
$env:APP_ENV='test'
$env:TEST_DATABASE_URL='postgresql+asyncpg://telebotshop_test:test-local-only@127.0.0.1:55433/telebotshop_test'
.venv/Scripts/python.exe -m pytest tests/unit tests/contract tests/architecture -q --tb=short -o cache_dir=.cache/payment-v2-review-20261024/pytest-c --junitxml=.cache/payment-v2-review-20261024/c.xml
.venv/Scripts/python.exe -X utf8 .cache/payment-v2-review-20261024/run-d.py
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe scripts/check_architecture.py
.venv/Scripts/python.exe scripts/verify_harness.py
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_harness.py -v
$env:UV_CACHE_DIR=Join-Path (Get-Location) '.cache\uv'
.tools/uv/Scripts/uv.exe lock --check
.tools/uv/Scripts/uv.exe build --no-sources
.venv/Scripts/python.exe -X utf8 .cache/payment-v2-review-20261024/verify.py
```

Không thay schema, dependency, ADR, Windows policy, private config hoặc Git index/HEAD.
Không commit/push/deploy. External gates bên dưới không được xét PASS bởi các test này.
`verified-results.json` đối chiếu đúng tập JUnit với full collection; PostgreSQL18.6 không còn
schema test hoặc client khác sau cleanup. `build.json` giữ kết quả kiểm tra artifact.

## Chưa được nghiệm thu và không tự coi là PASS

| Gate riêng | Trạng thái / còn cần |
| --- | --- |
| SePay hosted Sandbox | Chưa chạy Dashboard Test Mode → public HTTPS callback/API thật. Cần account/receiving account/API token/HMAC/callback trong private config. |
| USDT actual settlement | Chưa chuyển coin thật. Cần Owner chọn network/contract/recipient/FX/buyer binding, read-only credentials và provider chain proof. Adapter independent chain proof chưa tích hợp; actual Binance evidence vẫn pending thay vì tự đặt token_verified=true. |
| Hai user Telegram Test thật | Chưa có Buyer2 hoạt động; actual cross-account case chưa PASS. Functional ownership/concurrency đã có hai identities/connections trên PostgreSQL; không gọi đó là hai tài khoản Telegram thật. |
| Stars E3/E4 | PAUSED, không tick. Có thể nghiệm thu riêng nếu Owner mở lại Stars. |
| Production/tiền thật | Chưa được giao, không activate/deploy. T18 và provider/go-live gates giữ riêng. |

**Kết luận:** T14 đã có chức năng thay Stars hoạt động và đã nghiệm thu theo điều kiện Owner
mới. Có thể đi tiếp development task khác khi được giao; không cần mở lại Stars để đóng T14
chức năng. Không dùng kết luận này để nhận tiền thật hoặc đóng các gate external ở trên.
