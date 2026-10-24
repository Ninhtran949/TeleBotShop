# Payment V2 — vận hành local/sandbox và evidence

Bank/VietQR và USDT là hai cách **nạp ví**; Credit là cách **trả tiền đơn hàng**.
Checkout Credit không cần Stars invoice, pre-checkout hoặc successful_payment.
Stars đang PAUSED/BLOCKED theo Owner24/10; T14 E3/E4 vẫn unchecked.
[Spec/plan](plans/shop/payment-v2.md), [ADR-0006](adr/0006-payment-v2-wallet.md).

**Nghiệm thu T14 hiện tại: PASS chức năng thay Stars** theo yêu cầu mới của Owner.
[Matrix R1–R8](plans/shop/t14-replacement-acceptance.md) ghi fresh609unit/contract/architecture,
510PostgreSQL,6harness và actual Telegram Test5types/6shapes, Credit refund/resend/support.
Mốc chức năng này đã đóng; các gate hosted SePay/actual USDT/production bên dưới vẫn riêng.

```mermaid
flowchart LR
    B[Bank / QR + signed webhook] --> T[Verified top-up]
    U[USDT + deposit evidence] --> T
    T --> L[Immutable credit ledger]
    L --> C[Credit checkout transaction]
    C --> W[Existing durable fulfillment worker]
    W --> D[Customer delivery / manual completion]
```

## Cấu hình đang có trên máy

`.env.payment-v2` đã được tạo, bị Git ignore, dùng keyring/operator/HMAC secrets mới.
Token bot Test Server và Owner được lấy từ cấu hình Test Server hiện có; không sửa ba file
`.env`, `.env.telegram-test`, `.env.telegram-e2e`. Lần nghiệm thu T14.r đã chạy bot/worker
thật qua Test API và existing Owner MTProto session. HMAC secret hiện tại phục vụ signed-local
simulation; chưa liên kết SePay Dashboard. Không gửi production message hoặc tạo payment Stars.

Database mới `telebotshop_payment_v2_sandbox` ở `127.0.0.1:55432`, role riêng không superuser,
marker `telebotshop:payment-v2-sandbox:v1`, schema0020. Không migrate/reset database cũ.
Database Pytest vẫn là disposable `telebotshop_test` ở55433, mỗi test schema riêng.

Flags trong sandbox: Stars=false, Credit/Bank/USDT=true, external-live=false,
Binance read-only=false. Public template: [`.env.payment-v2.example`](../.env.payment-v2.example).
Settings từ chối production/main database và startup kiểm tra marker + exact schema head.
`BotSettings` mặc định wallet=false để các cấu hình cũ không tự trỏ vào flow mới.

Chạy từ repository root với uv0.13.4 (máy này có `.tools/uv/Scripts/uv.exe`):

```powershell
docker compose --profile dev up -d --wait postgres
uv run --env-file .env.payment-v2 --no-sync python -m telebotshop payments-api
```

API chỉ bind localhost8015; `/health/live`, `/health/ready` và POST
`/payments/sepay/webhook`. Smoke đã dừng API sau khi kiểm tra. Các PostgreSQL services
được trả về trạng thái dừng sau verification; dữ liệu sandbox ở dev volume được giữ.
Không chạy lại script smoke để tự cộng thêm số dư; dữ liệu hiện có chỉ là FAKE fixtures.

Operator CLI độc lập Telegram. Mỗi lần chạy tự nhập `PV2_OPERATOR_TOKEN` ở prompt ẩn;
không đưa token vào arguments/chat. Actor là `PV2_OPERATOR_ID`, vẫn phải có current grants.

```powershell
uv run --env-file .env.payment-v2 --no-sync python -m telebotshop payments-operator status
uv run --env-file .env.payment-v2 --no-sync python -m telebotshop payments-operator history --customer CUSTOMER_ID
uv run --env-file .env.payment-v2 --no-sync python -m telebotshop payments-operator reviews
```

Owner sandbox đã bootstrap. `init-owner` chỉ dùng khi tạo DB mới, không đổi Owner của DB cũ.
Trong DB mới khác, tạo role/login riêng, database đúng tên và comment marker ở trên; export
đúng `.env.payment-v2` rồi chạy `python -m alembic upgrade head`. Không dùng DSN dev/main
để chạy migration. Không downgrade database đã có financial history.

## Luồng khách và vận hành

Polling/worker dùng cùng `.env.payment-v2`; đã được chạy cho T14.r trên Test Server và dừng
sau nghiệm thu. Khi được giao test tiếp, dùng:

```powershell
uv run --env-file .env.payment-v2 --no-sync python -m telebotshop polling
uv run --env-file .env.payment-v2 --no-sync python -m telebotshop worker
```

Khách dùng `/wallet`, `/topup bank 100000`, `/topup usdt 1.5`, lịch sử ví/nạp, catalog và
**Mua bằng Credit**. QR có caption chuyển khoản và text fallback nếu Telegram từ chối ảnh.
Credits sandbox1:1 VND; USDT dùng configured rate25000, Decimal/ROUND_DOWN, snapshot/TTL5phút.
Đây chưa phải live FX quote. Quote USDT đang chờ settlement/review không được thay bằng quote
khác. Quote hết hạn chưa có evidence được đóng/audit để tạo quote mới; deposit cũ không tự
ghép vào quote mới. Deposit đã phát hiện vẫn chờ finality của quote gốc, kể cả sau TTL.

Credit price được cấu hình riêng bằng `/wallet_price PRODUCT_UUID CREDITS` rồi dòng lý do,
hoặc CLI `price --product UUID --amount CREDITS --action UUID --reason TEXT`.
Không lấy tỷ lệ tùy ý từ `price_stars`. Catalog maintenance cũ vẫn giữ Stars metadata;
Credit order có `price_stars=NULL`, không cần invoice/charge. Stock/key/resource/entitlement
và worker/manual/resend hiện có được tái sử dụng.

USDT cần recipient/network/token riêng được Owner xác minh cho từng buyer. CLI sandbox:
`route --customer ID --network NAME --token-contract CONTRACT --recipient ADDRESS`.
Không dùng shared recipient + amount để suy buyer. Fixtures hiện tại dùng FAKE_NETWORK/TOKEN/
ADDRESS; không coi đây là network hay địa chỉ có thể chuyển USDT thật.

Admin `/wallet_reviews`, `/wallet_ledger CUSTOMER_ID`, queue/fraud alerts, review detail.
Manual commands gồm `/wallet_approve`, `/wallet_reject`, `/wallet_escalate` với
`EVENT_UUID TOPUP_UUID` (dùng `-` thay topup cho reject/escalate không gắn request), dòng kế
lý do, dòng cuối source reference; sau đó bấm xác nhận actor-bound10phút.
Không gửi secrets/PII trong reason/source. Approve vẫn kiểm tra trusted event, amount,
destination/asset/network/token/finality/TTL; không override mismatch bằng screenshot.
Late/under/over/ambiguous settlement giữ review, không tự đổi quote hoặc cộng tiền.

CLI tương đương, sau authentication:

```text
review-detail --event EVENT_UUID
review-prepare --event EVENT_UUID --intent TOPUP_UUID --action ACTION_UUID --reason TEXT --source SOURCE --decision APPROVE
review-confirm --action ACTION_UUID
grant --customer ADMIN_ID --permission REVIEW --allow --reason TEXT
adjust --customer CUSTOMER_ID --delta CREDITS --action ACTION_UUID --reason TEXT --source SOURCE
```

Permissions riêng: ADJUST/PRICE/REVIEW/REFUND/AUDIT. Owner cấp cho registered ADMIN;
bỏ `--allow` để revoke. ADMIN/Stars refund grant không tự có quyền tài chính Wallet.
Adjust/price/route/reverse/grant/cash record cần typed `CONFIRM SANDBOX`; mọi thay đổi có audit.
Action UUID phải giữ nguyên khi retry sau lost ACK, không tạo action mới để thử lại tiền.

## Coupon Credit (T15)

Spec đã chuyển sang Credit trước code tại [growth](plans/shop/growth.md). Coupon không giảm
top-up Bank/USDT và không mở Stars. Không cần provider/key mới. Bản mới yêu cầu schema
`0021_coupons`; phiên T15 chỉ migrate database test cô lập, chưa migrate các database runtime
trong private config. Khi được giao chạy runtime mới, nâng cấp đúng database sandbox đã
xác minh theo quy trình migration, trước polling/worker:

```powershell
uv run --env-file .env.payment-v2 --no-sync alembic upgrade head
```

Owner hoặc ADMIN đã được cấp **Wallet PRICE** hiện có vào Quản trị → Ví / Bank / USDT →
Quản lý coupon Credit, hoặc `/coupons`. Không dùng catalog/Stars refund grant thay quyền này.
Lệnh dưới đây chỉ tạo preview; phải bấm Xác nhận trong10phút để lưu:

```text
/coupon_create SAVE10 PERCENT 10 2026-12-31T23:59:59Z 100 1 ALL
/coupon_create SAVE500 FIXED 500 2026-12-31T23:59:59Z 20 1 CATEGORY:DEMO
/coupon_state SAVE10 off
/coupon_state SAVE10 on
```

Scope là ALL, PRODUCT:UUID hoặc CATEGORY:CODE chính xác (không ngầm gồm danh mục con).
PERCENT1–99, FIXED là số Credit nguyên dương; không free order hoặc stacking. Config bất
biến, code không tái sử dụng cho config khác; muốn thay rule thì tạo code mới. Danh sách có
phân trang và nút xem chi tiết; code không được liệt kê công khai cho khách.

Khách vào Mã giảm giá hoặc gửi `/coupon SKU CODE`, ví dụ `/coupon DEMO-1 SAVE10`.
Bot hiện base/discount/net và điều khoản trước nút trả Credit. Preview không giữ lượt hoặc
trừ ví; checkout kiểm tra lại current price/product/scope/expiry/caps. Giá đổi hoặc coupon
không hợp lệ bị từ chối, không tự charge giá đầy đủ. Token gắn actor và hết hạn sau10phút;
restart cần mở preview mới, chưa xác nhận thì không có financial effect.

Redemption, net debit/payment, PAID, entitlement/READY và audit cùng UoW. Double-click/lost
ACK dùng cùng action trả order cũ. Refund chỉ hoàn **Credit thực trả** và **không hoàn lượt
coupon**, theo lựa chọn Owner24/10; checkout rollback không tiêu lượt. Stars vẫn PAUSED,
receipt lịch sử không tác động usage/snapshot coupon. T15 Telegram transport được mock;
chưa có actual Test bot smoke cho tính năng coupon hoặc hosted CI.

## Refund và reconciliation

`/credit_refund ORDER_UUID`, dòng lý do/source, rồi xác nhận; hoặc CLI
`refund-prepare --order UUID --action UUID --reason TEXT --source SOURCE` và
`refund-confirm --action UUID`. Hoàn đủ Credit bằng compensating journal, giữ key allocated,
hold work, không restock/gửi thêm hoặc hoàn cash. SENDING/UNCERTAIN primary/resend cần reconcile.

CLI `reverse --topup UUID --reason TEXT --source SOURCE` đảo phần Credit nạp, chỉ khi đủ số dư.
Đây không phải hoàn tiền ngân hàng/crypto. Manual cash refund dùng
`cash-refund-record --topup UUID --action UUID --state REQUESTED --reason TEXT --source SOURCE`,
sau đó UNCERTAIN nếu kết quả chưa rõ, CONFIRMED chỉ với source report và prior top-up reversal;
`cash-refund-history --topup UUID` đọc records. Không có API gửi tiền/trade/withdraw hoặc
automatic retry. CONFIRMED ở đây là operator attestation, chưa phải external verification của agent.

Read-only reconciliation (API token Sandbox cần được cấu hình riêng):

```powershell
uv run --env-file .env.payment-v2 --no-sync python -m telebotshop payments-reconcile
uv run --env-file .env.payment-v2 --no-sync python -m telebotshop payments-reconcile --since 2026-10-24T00:00:00Z
uv run --env-file .env.payment-v2 --no-sync python -m telebotshop payments-reconcile --usdt-fixture .cache/payment-v2-20261024/simulated-usdt.json
```

Mỗi batch1giờ, overlap5phút, tối đa10trang×100; cursor chỉ tiến sau durable ingestion.
Lần đầu mặc định lookback1giờ; dùng `--since` để backfill cũ hơn. Giữ request bounded, không
truyền future/naive timestamp; overflow/error giữ cursor, cần operator thu hẹp window.
Chạy một SePay reconciler mỗi IP; reader pace≤3req/s,429/timeout dừng batch an toàn.
Production endpoint chưa được mở; không đổi allowlist để tự gọi Live.

Binance `--binance-readonly` chỉ khi flag + read-only API key/secret + network/contract được
cấu hình tường minh. Adapter chỉ signed GET deposit history, không Pay Merchant/trade/withdraw.
Exchange history không chứng minh contract on-chain: token_verified=false, actual deposit
vẫn pending/review. Chưa có chain-proof provider được Owner chọn và tích hợp; không tự bật
token_verified hoặc giả nhận tiền. Configured fixtures gắn source SIMULATED_BINANCE.

## Evidence ngày24/10/2026

Mốc mới T14.r:609 C +510PostgreSQL (full3process, mỗi170case) +6harness pass. Sáu cases
KEY/LICENSE/FILE/TEXT/DOC/MANUAL thật trên Telegram Test,6Credit refunds/reversals,1safe key
resend và support thread pass;0Stars API/production effects. Body/file bytes được quan sát
phía user, không chỉ dựa status DB hoặc mocked sender. [Report](plans/shop/t14-replacement-acceptance.md)
và `.cache/t14-replacement-20261024/` giữ commands/JUnit/preflight/live cases/source evidence.

Nghiệm thu phát hiện quote USDT CREDITED/REJECTED vẫn bị xét khi có deposit cho quote mới.
Đã thêm2failing PostgreSQL regressions rồi sửa active-intent filter, giữ time/route/token/
finality checks.30affected cases và full510pass. Không tạo order hoặc primary send khác để
vượt observation lỗi; kiểm tra trạng thái thật rồi chỉ resume intent/case chưa hoàn tất.

Các số liệu508 và native-smoke-only bên dưới là mốc triển khai ban đầu, được giữ làm lịch sử.

Evidence local được ignore tại `.cache/payment-v2-20261024/`; không chứa credential values.
Commands chính đã chạy, CPython3.12.15/asyncpg0.32.0/aiogram3.31.0, PostgreSQL18.6:

```powershell
.venv/Scripts/python.exe -m pytest tests/unit tests/contract tests/architecture -q --tb=short -o cache_dir=.cache/payment-v2-20261024/pytest-c-final --junitxml=.cache/payment-v2-20261024/c-final.xml
$env:APP_ENV='test'
$env:TEST_DATABASE_URL='postgresql+asyncpg://telebotshop_test:test-local-only@127.0.0.1:55433/telebotshop_test'
.venv/Scripts/python.exe -X utf8 .cache/payment-v2-20261024/run-d.py
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe scripts/check_architecture.py
.venv/Scripts/python.exe scripts/verify_harness.py
.venv/Scripts/python.exe -B -m unittest discover -s tests -p test_harness.py -v
.tools/uv/Scripts/uv.exe lock --check
.tools/uv/Scripts/uv.exe build --no-sources
.tools/uv/Scripts/uv.exe audit --locked
```

Kết quả: **609 unit/contract/architecture**, **508 distinct PostgreSQL**, **6 harness** pass,
0fail/error/skip trong final evidence. `run-d.py` collect integration cases và chia files thành3process dùng
schema/cache/JUnit riêng; exact commands và counts ở `d-final-commands.json`/`d-final-*.xml`.
Full run gồm506cases (169+169+168); quote-expiry review bổ sung2cases và rerun29top-up cases
sau lần full đó. `test-coverage.json` so JUnit union với toàn bộ508cases được collect hiện tại,
không đếm trùng reruns. `topups-final.xml` là evidence cho các source changes cuối của quote matching.

Lock check/sync, Ruff/format/architecture, harness, build và isolated wheel runtime pass.
`sync --locked --no-build --group telegram-e2e` không thực hiện được do pyaes không có binary
distribution; `sync --locked --no-build --inexact --offline` đã pass49packages, giữ nguyên các
E2E packages đã cài. Không đổi dependency/lock hay build thêm third-party source.
Audit đọc OSV:53packages,0known vulnerabilities,1adverse status **rsa archived** trong optional
Telethon group cũ; Payment V2 runtime không import/cài Telethon. Không gọi audit là warning-free.
Wheel/sdist có156source modules đúng bytes, README khớp, không env/cache/tests/temp; isolated
runtime import155modules (trừ __main__), poisoned settings/no network/no pytest/Telethon và native
asyncpg0.32.0/AES-GCM pass. Package/runtime summaries ở `package.json`/`wheel-smoke.json`.

Self-review correctness/architecture/security/performance đã xử lý missing-schema projection,
shared-catalog readiness marker, buyer notification routing, SePay pacing và quote replacement
không rebind old deposit. Không còn finding chặn scope local; external chain proof/hosted
configuration/deployment nằm ngoài claim này. Không có independent reviewer hoặc hosted CI run.

HEAD/index/old private configs/dependency files/migrations0001–0014/harness/temp giữ nguyên.
`preservation.json`, `changed-files.json`, `secret-scan.json` ghi kiểm tra và91files thay đổi.
Scan không thấy private credential values; bốn matches là demo DSNs đã có trong tài liệu.

Trước khi sửa: full D có15 lỗi cùng truy vấn history trên schema cũ thiếu price_credits.
Đã sửa projection, không đổi tests refund/support. Riêng notification migration oracle thêm
default mới và dùng SCHEMA_HEAD, vẫn kiểm tra mọi field Stars cũ và predecessor function text.
Ledger/stock/overspend/idempotency/commit rollback/lost ACK, HMAC replay/mismatch, auto/manual,
5product types + text/document/manual completion, Credit refund-vs-worker và reversal có tests.

Native smoke `runtime-smoke.json`: actual localhost HTTP health/readiness200, invalid HMAC401,
locally signed webhook replay-safe, simulated USDT CLI reconciliation replay-safe, authenticated
operator/reconcile balanced, non-superuser runtime. Zero Telegram/provider money calls.
Đây **không** là provider-hosted SePay hoặc real USDT/Telegram delivery evidence.

## Những điều kiện còn thiếu

| Gate | Trạng thái / Owner setup |
| --- | --- |
| Hosted SePay Test Mode | BLOCKED: chưa cấu hình Sandbox account/API token/receiving account/HMAC webhook và public HTTPS callback trong môi trường này. Owner điền private `.env.payment-v2`, không gửi secrets vào chat. Cần chạy Dashboard simulated transaction thật → callback → ledger → checkout/delivery và replay/reconcile. |
| USDT actual settlement | BLOCKED: chưa có network/token/recipient hợp lệ được Owner chọn, exclusive buyer binding, read-only credentials và independent chain proof/finality provider. Adapter independent chain proof chưa tích hợp, không chỉ thiếu key; actual Binance evidence vẫn pending. Hiện chỉ simulated fixtures; không claim exchange deposit testnet hỗ trợ SAPI. |
| Telegram Payment V2 delivery | PASS trên Test Server cho5types/6shapes, manual confirmation, Credit refund, safe key resend và support. Existing Owner session, không login/OTP/Computer Use; payment inputs locally signed/simulated. Actual hai Test users vẫn chưa có, native two-identity/concurrency tests giữ riêng. |
| Tiền thật / production | Chưa được giao: kiểm tra tài khoản/provider/FX/terms/refund/retention/platform/legal, least-privilege deployment/restore/TLS và authorization trước activation. |
| Stars T14 | PAUSED/BLOCKED; không đổi E3/E4, Buyer2/Test Stars evidence còn thiếu, không tự tiếp tục. |

[SePay Test Mode](https://developer.sepay.vn/vi/tien-ich-khac/test-mode) tách Live;
[HMAC](https://developer.sepay.vn/vi/sepay-webhooks/xac-thuc),
[API v2](https://developer.sepay.vn/vi/sepay-api/v2/giao-dich/danh-sach) và
[rate limits](https://developer.sepay.vn/vi/sepay-api/v2/xac-thuc) là contracts được dùng.
[Binance deposit history](https://developers.binance.com/en/docs/catalog/core-trading-wallet/api/rest-api/capital)
khác Pay Merchant/on-chain proof; [Pay country restrictions](https://www.binance.com/en/support/faq/detail/942c5c12e00b494e9bc3e2ab31f7da19)
không hỗ trợ merchant ở Việt Nam theo lần kiểm tra này. Không cần merchant credentials giả.
