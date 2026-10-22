# Database và transaction baseline

T02 dùng PostgreSQL **18.6**, SQLAlchemy **2.0.55** Async + asyncpg **0.32.0** và
Alembic **1.20.0**. T03 thêm identity/access/settings và audit repositories bằng SQLAlchemy
Core tables; không trả ORM/session ra application. Head hiện tại **`0011_history`**;
`0001_runtime` giữ nguyên, `0002_identity` thêm identity/Owner/audit, `0003_access` thêm
explicit grants, settings và confirmation. T04 thêm `0004_catalog`; T05 thêm encrypted
inventory/actions/key verifiers; T06 thêm orders/items/invoice intents và finite reservations.
T07 thêm immutable query-bound pre-checkout approval và controlled reservation extension.
T08 thêm receipt/conflict ledger, PAID/attention, permanent ALLOCATED và READY work.
T09 mở finite work/attempt/recovery/observations, giữ nguyên revisions 1–8.
T10 thêm resource versions/order bindings/manual confirmations và mở nonfinite commerce;
revisions 0001–0009 giữ nguyên.
T11 thêm resend actions và hai history indexes; không sửa revisions 0001–0010.

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
| `inventory_units` | Finite UNIQUE_KEY/LICENSE; product FK/type guard, encrypted BYTEA + nonce/version, per-product HMAC fingerprint unique, unique key-version/nonce, AVAILABLE/RESERVED/ALLOCATED/RETIRED. Identity/ciphertext/fingerprint immutable; exact reservation/allocation item FKs; ALLOCATED không release/retire hoặc đổi binding. |
| `inventory_actions` | Actor/product FKs, import hoặc retire, số lượng 1–1000, 10-minute expiry, status/completed_at. Pending import giữ encrypted envelopes JSONB; retire có composite unit/product FK. Apply/cancel clear envelopes. |
| `inventory_crypto_state` | Singleton chỉ lưu HMAC verifiers của dedup/AES keys theo version, không lưu raw keys. Khóa row khi kiểm tra/register; từ chối đổi dedup, đổi key cùng version hoặc bỏ registered version. |
| `orders`, `order_items` | Buyer/action unique, immutable quantity-one XTR/public product/price snapshot và finite unit/product FKs; DB-time TTL, guarded unpaid close và retained history. |
| `resource_versions` | Immutable encrypted file/text/document/manual source cùng public metadata snapshot; actor/product/previous-version FKs, pending/active/retired/cancelled, một ACTIVE/product; confirmed publication chặn stale version. |
| `order_resources` | Một immutable resource binding/nonfinite item; item/version FKs và unique pair; deferred trigger bắt order thiếu binding. Finite item vẫn bắt buộc exact inventory unit, nonfinite item không có unit. |
| `manual_actions` | Actor/work/attempt-bound confirmation, encrypted evidence với AAD riêng, 10-minute expiry; một applied completion/work, terminal action immutable; completion/work/order deferred atomic. |
| `resend_actions` | Actor/mode/work/original-attempt-bound 10-minute confirmation; immutable identity/history, một PENDING/SENDING/UNCERTAIN mỗi work; riêng send window/result/late observation, không tạo entitlement mới. |
| `invoice_intents` | Một opaque payload/order, durable unique attempt, READY/SENDING/SENT/REJECTED/UNCERTAIN/CANCELLED; no automatic resend, không phải payment receipt. |
| `pre_checkout_approvals` | Order PK/FK, unique opaque query ID, approved_at theo DB time; chặn UPDATE/DELETE/TRUNCATE. Một approval/order, giữ qua timeout/expiry. |
| `payments` | Append-only bounded financial receipt, unique Telegram charge, nullable payer/order, MATCHED/REQUIRES_REVIEW + reason; partial unique primary MATCHED/order, không cấm extra receipts. |
| `payment_conflicts` | Append-only minimal financial evidence + SHA-256 digest, unique payment/digest; không overwrite canonical receipt hoặc lưu raw Update/order_info PII. |
| `fulfillments` | Unique order item + immutable payment/unit/buyer/destination/type; state/due/active attempt/send count/hold/attention. Initial READY; không plaintext. |
| `delivery_attempts` | UUID đồng thời fencing token, work FK, claim/lease/send/completed times, state, safe code/message ID/retry-after; guarded transitions, terminal immutable, no delete/truncate. |
| `delivery_observations` | Append-only typed outcome theo attempt/digest; late/duplicate results không overwrite quyết định. Fixed code enum, không raw error/payload. |

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

Application UoW port có `identity`, `access`, `catalog`, `inventory`, `orders` repositories và `commit()`;
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
demo hợp pháp giả nằm trong test fixtures/README. T05 tests thêm fake encrypted key/license
units; không nhập inventory thật hoặc migrate dev/production database trong task này.

T05 import/retire kiểm tra current `inventory.manage` trong UoW. Import prepare validate
toàn batch trước commit và seal từng unit theo ADR-0003; durable pending action không phải
AVAILABLE stock. Confirm khóa key-registration singleton rồi action; insert units theo
thứ tự fingerprint ổn định, `ON CONFLICT` trên named per-product fingerprint constraint.
Bất kỳ row trùng gây use-case error và rollback cả các rows mới trong batch. AES nonce
collision bị unique constraint từ chối; không fallback plaintext hoặc âm thầm bỏ row.
Retire dùng conditional UPDATE đúng product/unit và trạng thái AVAILABLE. Stock mutation,
redacted audit và applied marker/clear pending envelopes cùng commit; cancel chỉ clear
pending intent, action đã apply không được cancel để undo. Repositories không commit.

Key verifiers được pin ở polling startup hoặc lần import đầu; không decrypt trong các
luồng này. Guard bảo thủ serialize inventory writes, bao gồm concurrent confirmations;
thu hẹp khi có contention thực tế. Independent-connection import tests vẫn kiểm tra DB
uniqueness/atomicity, replay/retire races và audit/deferred commit rollback. Keyring đổi
version phải giữ mọi registered old key; proof chỉ chứa HMAC, raw keys giữ ngoài DB.

Stock/low-stock queries chỉ SELECT IDs/status/aggregate counts và product public fields;
không đọc ciphertext vào DTO hoặc log. 5 units/products mỗi trang; low-stock chỉ active
finite SKU có committed AVAILABLE count ≤ configured threshold, không tính pending batch.
T04→T05 migration giữ products, audit và pending product confirmation hoạt động; không
auto-seed units. `0001_runtime`–`0005_inventory` giữ nguyên. T06 thêm RESERVED/reservation
FK và last-key checkout race; T08 bổ sung ALLOCATED/payment-vs-expiry.

## T06 orders và reservations

Revision `0006_orders` thêm `orders`, immutable quantity-one `order_items`, `invoice_intents`;
unit có nullable reservation item/expiry và RESERVED state. Unique buyer/action chống
double-click; FK item→unit/product và unit→item/unit ràng buộc đúng kho. Giá/type/title/description
snapshot, buyer/action/expiry và invoice payload không đổi. Mỗi unit chỉ có một current
reservation; history của nhiều order đã expired vẫn tham chiếu cùng unit khi được bán lại.

Create khóa buyer NO KEY UPDATE rồi product và AVAILABLE unit FOR UPDATE, lấy
clock_timestamp sau locks và đặt TTL 15 phút. Không SKIP LOCKED khi chọn key cuối:
lock timeout khác hết hàng. SET LOCAL lock_timeout 2s/statement_timeout 5s giới hạn mỗi
transaction; không retry external effect từ lỗi DB. Khóa product cũng serialize price/sale edits.

Cancel/expiry khóa order trước, chỉ AWAITING_PAYMENT→EXPIRED, release bằng đúng item ID
và audit cùng commit. Trigger chặn sửa snapshot, resurrect order, release reservation còn
AWAITING và retire RESERVED. Expiry khóa theo order ID với SKIP LOCKED, mỗi batch ≤100;
không release đơn mới chỉ vì unit từng thuộc đơn cũ. T07 mở approval extension; T08 mở
paid allocation với payment evidence. Paid order và allocated unit không trở lại unpaid/available.

Invoice claim READY→SENDING/attempt commit trước API. Kết quả SENT/REJECTED/UNCERTAIN
persist trong transaction khác; known reject close/release chỉ nếu order còn unpaid.
Cancel/expiry trong lúc network chạy không bị kết quả SENT phục hồi order. SENDING quá
30s được recovery thành UNCERTAIN, không resend; DB failure sau send giữ durable claim.
`telebotshop expire-orders` chạy một batch expire + một batch recovery, không cần Bot/keys.
Tests seed historical rows hợp lệ và dùng DB time, không disable trigger hoặc đổi clock.
T05→T06 giữ ciphertext/proofs/audit/pending imports; empty downgrade/re-upgrade được test.
Downgrade khi có order history bị từ chối để không tự xóa đơn hoặc restock keys.

## T07 pre-checkout approval

`0007_pre_checkout` giữ nguyên orders/items/invoice/ciphertext; chỉ thêm approval table và
thay guard để cho phép một lần extension hợp lệ. Application lock order theo unique invoice
payload, rồi current product và exact unit; DB time đọc sau locks. Chỉ registered buyer,
XTR/snapshot amount, active product, AWAITING_PAYMENT/unexpired và invoice
SENDING/SENT/UNCERTAIN được approve. Không dựa vào tổng AVAILABLE khi order đã giữ key cuối.

Order PK ngăn hai approvals; unique query ID ngăn dùng một query cho hai orders. Same query
replay vẫn kiểm tra current owner/amount/state/reservation, không thêm audit/gia hạn. Query
khác cho order approved bị reject. Insert approval + max(old expiry, approved_at+15m) trên
order và exact RESERVED unit + redacted audit cùng commit. Trigger chặn tự tăng TTL ngoài
approval, đổi binding hoặc xóa lịch sử. Expiry SKIP LOCKED bỏ qua approval in-flight, sau
commit đọc TTL mới. Cancel unpaid vẫn guarded; replay sau cancel/expiry không approve lại.

DB/validation budget 4s bao gồm pool/locks/commit; SET LOCAL lock 2s/statement 5s từ order
port vẫn áp dụng bên trong total budget. Answer có 3s budget riêng, không network dưới lock.
API timeout hoặc lost answer không xóa approval; different query không được tự mở lại.
Invoice REJECTED tới sau approval là evidence mâu thuẫn: lưu UNCERTAIN, giữ reservation thay
vì tự release. Approval chưa phải receipt/PAID; T08 xác nhận bằng successful_payment.
T06 upgrade preservation, metadata drift và rollback hai TTL/approval/audit được test trên
PostgreSQL thật. Downgrade 7→6 chỉ khi approval table rỗng; có approval history thì từ chối.

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


## T08 receipts và committed fulfillment

`0008_payments` giữ nguyên revisions 1–7, identity/catalog/ciphertext/invoice/approval history.
Charge-scoped advisory transaction lock dùng signed SHA-256 prefix; collision chỉ serialize,
unique charge vẫn là authority. Tiếp theo lock order rồi exact reserved unit, cùng lock order
của expiry/cancel. Different charges/order serialize để chỉ một MATCHED; các charge còn lại
được lưu review. Same charge khác nội dung tạo conflict evidence duy nhất theo digest.

Một transaction lưu MATCHED → PAID → RESERVED→ALLOCATED → READY work + general audit.
Deferred constraint trigger kiểm tra khi commit: snapshot payer/payload/XTR/amount đúng và
hoặc exact allocation/work, hoặc PAID + RESERVATION_LOST không có work giả. Failure ở receipt,
state, allocation, intent, audit hoặc commit rollback toàn bộ. General audit vẫn cần actor thật;
unmatched/conflicting financial events có append-only ledger riêng, không gán cho Owner giả.

TTL tự nó không release stock. Receipt thắng lock khi reservation vẫn thuộc order có thể
allocate; nếu expiry/cancel đã release hoặc unit thuộc buyer khác thì PAID + attention, không
lấy lại AVAILABLE/RESERVED/ALLOCATED unit. Paid giữ nguyên closed_at/reason trước đó để
không xóa lịch sử expiry/cancel. Pending expiry/cancel không thể release paid allocation.
Stock views đếm riêng ALLOCATED; payment use case không đọc ciphertext.

Upgrade tests so sánh tất cả cột/rows cũ, thêm NULL allocation/attention, giữ approval và
chạy payment sau upgrade; metadata không drift. Khi thêm cột, preservation tests dùng
projection cũ tường minh thay vì prepared SELECT * có result type thay đổi. Downgrade 8→7
chỉ khi không có receipt/conflict/work/PAID/ALLOCATED history; không xóa receipt để downgrade.
T09 triển khai worker states/lease/attempts; reconciliation/refund và app/migration role
separation vẫn thuộc task sau. READY chưa là giao hàng thành công.


## T09 finite delivery ledger

`0009_fulfillment` thêm delivery_attempts/observations và active attempt/due/send count/hold/
attention trên fulfillments; order mở FULFILLING/DELIVERED/FAILED. Payment và allocated key
không đổi: delivery FAILED không có nghĩa unpaid. Snapshot/ciphertext/binding vẫn immutable.
Work/attempt/order state và actual-send count được deferred constraint kiểm tra cùng commit;
observations chỉ nhận typed fixed error codes, không text lỗi. Sending timestamp và lease đã
durable không được rewrite lúc hoàn tất; terminal history không reset/delete/truncate.
Indexes theo attempt work ID, fulfillment order ID/active attempt hỗ trợ history count,
transition guard và recovery; due/expiry queues có partial indexes theo eligible state.

Claim work-only FOR UPDATE SKIP LOCKED, insert CLAIMED attempt với UUID làm fence và commit.
Prepare khóa order trước work, xác minh payment/owner/destination/allocation/type/hold/current
attempt/unexpired lease rồi mới load encrypted envelope và decrypt. Recheck lease sau decrypt,
chuyển SENDING + send count + order FULFILLING + audit cùng commit. Không DB connection qua API.
Complete lưu result observation, chỉ đúng active SENDING token còn lease được apply; SENT cùng
order DELIVERED, known rejection RETRY_WAIT hoặc FAILED, unknown/late UNCERTAIN. Late result
không overwrite state mới hoặc xóa evidence. Retry giữ nguyên unit, budget rechecked sau restart.

Recovery chọn expired candidates theo DB clock, lock orders SKIP LOCKED rồi work, recheck lease.
Expired CLAIMED→ABANDONED + READY; expired SENDING→UNCERTAIN + attention/audit. T13 hold serialize
theo order→work; hold thắng trước prepare chặn decrypt/send, SENDING/UNCERTAIN chặn auto hold/refund.
Chưa có public setter/refund/reconcile operation. Alerts gồm attention/audit + sanitized warning.

Upgrade preservation so sánh toàn bộ old columns/rows của T08, sau đó claim work thật; metadata
drift và downgrade/re-upgrade có tests. Downgrade 9→8 chỉ khi chưa có attempts/observations,
mọi work vẫn READY/zero sends/no hold và order chưa vào delivery lifecycle; không xóa history.
Tests clock-controlled seed historical valid attempts hoặc inject DB-time result tại UoW,
không SQLite, disable triggers hoặc sleep theo lease TTL thật. Two-physical-connection tests
chứng minh SKIP LOCKED và cả hai thứ tự order lock với hold; gửi Telegram vẫn mock.

## T10 resource entitlements và manual completion

ResourceService đăng ký ciphertext dưới AAD `resource:v1` + product/SKU/resource UUID/type/
key version; không đổi namespace `inventory:v1` của T05. Reuse keyring/key verifiers hiện có.
Public metadata riêng giúp kiểm tra file name/MIME, reusable format và manual notice trước
publication; payload/nonce/version/identity không được sửa hoặc xóa sau insert.
Permission `inventory.manage` được giữ qua transaction; product lock serialize publication
và checkout. Confirmation recheck actor, current permission, product version, expected
source và deadline. Nguồn cũ ACTIVE→RETIRED vẫn thuộc các order đã chốt.

Checkout nonfinite giữ `inventory_unit_id=NULL`, tạo `order_resources` trong cùng UoW;
finite vẫn bắt buộc unit/product/reservation như T06. Pre-checkout giữ buyer/giá/XTR/TTL/
active-product checks, kiểm tra exact bound source và gia hạn order. Receipt lưu PAID +
resource work atomic; không reserve/allocate/release kho hữu hạn cho nonfinite. Source
ACTIVE/RETIRED được dùng cho late payment của order đã expiry/cancel; không đổi sang nguồn
mới. Nếu entitlement không còn hợp lệ thì giữ receipt/PAID + `ENTITLEMENT_UNAVAILABLE`.

`fulfillments.resource_id` có FK tới phiên bản và composite FK tới đúng item/resource;
XOR check tách finite unit và nonfinite resource. SQL helper `fulfillment_entitlement_matches`
giữ đúng order/item/primary payment/buyer/destination/type/source khi insert và commit.
Finite allocation vẫn immutable. Worker resource reader chỉ load ciphertext sau entitlement
authorization; corrupt envelope/format/AAD dừng FAILED và giữ receipt/binding.

Manual waiting notice dùng attempt/fence/retry policy chung; successful notice giữ attempt
SENT, work AWAITING_MANUAL và order FULFILLING. Staff chỉ đọc/complete với current
`fulfillment.manage`; migration thêm role ceiling cho OWNER/ADMIN, không tự cấp grant cho
ADMIN có sẵn. Detail read có audit, không expose evidence/source qua queue.

Manual action seal evidence dưới AAD `manual-evidence:v1` + product/SKU/work/action UUID/key
version. Confirm khóa access→order→work→action, recheck actor/attempt/state/hold/expiry và
decrypt evidence; action APPLIED + work SENT + order DELIVERED + audit digest commit cùng
nhau. DB không cho tự chuyển manual work SENT thiếu action APPLIED đúng attempt. Lost ACK
sau commit replay idempotent; lỗi audit/commit trước commit rollback toàn bộ. Cancel chỉ
đóng pending confirmation. Staff attests việc giao bên ngoài; đây không là Telegram receipt
chứng minh nội dung công việc đã hoàn thành. Failed/uncertain notice vẫn cần reconciliation.

Upgrade 9→10 được kiểm tra từ READY/CLAIMED/SENDING/SENT/RETRY_WAIT/UNCERTAIN/FAILED finite
work, giữ nguyên old columns/rows/payment/ciphertext. Downgrade chỉ khi chưa có resource,
manual/grant history; không xóa dữ liệu để downgrade. Bốn predecessor function definitions
được freeze trong migration để phục hồi chính xác các guard T08/T09. Metadata không drift.

## T11 history và resend actions

History projection chỉ đọc snapshot của orders/order_items cùng current delivery status;
buyer filter ở SQL, deterministic created_at/id order, limit 6 để trả 5 + has_more. Index
created_at/id và buyer/created_at/id hỗ trợ customer/staff lists. Staff list/dashboard bound
30 ngày theo DB time; aggregates từ order snapshot + tồn tại MATCHED primary receipt,
không cộng extra/unmatched payments hoặc gán thành net revenue sau refund.

`resend_actions` giữ actor, staff mode, work và original SENT attempt bất biến. Partial
unique mỗi work cho PENDING/SENDING/UNCERTAIN. Actor-bound 10-minute confirmation chuyển
PENDING→SENDING dưới order/work locks sau current authorization; send window được lưu
trước network. Result/late observation và fixed audit event commit riêng, không sửa primary
fulfillment, order DELIVERED hoặc inventory allocation/resource binding. Không plaintext
delivery payload trong table/audit. Terminal rows không update/delete/truncate; late
UNCERTAIN chỉ nhận một observation, giữ nguyên state/deadline/error/completed_at.

Guard lấy work lock khi insert/begin và kiểm tra original delivered paid binding; hold
trigger chặn hold mới khi action SENDING/UNCERTAIN. Cùng order→work→action lock order với
application/hold. Pending hết hạn được cancel khi yêu cầu lại; expired SENDING không retry,
history hiển thị uncertain và confirm/prepare persist UNCERTAIN. Reconcile tới T13.

Upgrade/downgrade rỗng giữ nguyên cả bảy T10 finite states/attempt/payment/ciphertext/audit.
Downgrade từ chối nếu có bất kỳ resend history; không xóa dữ liệu để vượt guard. Không sửa
0001–0010 hoặc các predecessor functions. Refund schema/state transitions vẫn tới T13.
