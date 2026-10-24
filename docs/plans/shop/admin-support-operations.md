# Spec: admin-support-operations

Status: T03–T14 triển khai local core quyền/settings, catalog/kho, delivery/history/support/refund, customer lookup, bounded authenticated webhook và durable operational notifications. Telegram test E và production/release gates chưa nghiệm thu. Nguồn: `task.md` §§6,9,10,11,13. Depends on: identity-access, catalog-inventory, orders-payments, fulfillment.

## Admin và support

`src/telebotshop/admin/` chỉ chứa Telegram transport/keyboards; business mutations thuộc use cases của capability sở hữu. Menu Dashboard, Products, Inventory, Orders, Customers, Discounts, Support Tickets, Reports, Settings được hoàn thiện theo phase; không render số liệu mock như dữ liệu vận hành. Discounts/growth chỉ xuất hiện khi đã có capability thật.

- `/admin` và từng action kiểm tra current permissions theo [identity-access](identity-access.md). Không dựa vào việc đã giấu button; direct callback/use-case calls đều phải kiểm tra.
- Owner quản lý grants/settings; staff chỉnh giá, import/retire stock, xử lý ticket/order/refund theo đúng quyền và current state. Destructive/financial actions dùng confirmation/action key có ownership/state binding, chống replay.
- Settings trong bot chỉ dành cho trường vận hành không bí mật đã có validator/permission (ví dụ support metadata, low-stock threshold). Bot token, DSN, encryption keys và webhook secret không được xem/đặt qua Telegram; owner transfer đi qua quy trình riêng có authorization.
- Audit append-only cho price/stock/role/refund/manual delivery/uncertain resolution: actor, action, target, thời gian, outcome/correlation, thay đổi được redacted. Business audit ghi cùng transaction với mutation; không lưu inventory secret/raw Update.
- Khách tạo/xem ticket của mình; SUPPORT chỉ xử lý assigned/authorized tickets, không xem plaintext key hoặc impersonate buyer. Gắn ticket với order phải check ownership trước.
- Order/customer reports dùng server queries có pagination/time bounds; không query toàn DB hoặc dump PII vào Telegram group.

`/paysupport` phải hoạt động trước payment acceptance; payment issues liên kết ticket/order an toàn. `/terms`/đường dẫn điều khoản và support identity là nội dung Owner cung cấp, không tự bịa điều khoản/refund window. `/start` có navigation Back/Home/Cancel và error text Việt rõ trạng thái.

## Threat boundaries và controls

T11 `/staff_orders [ORDER_ID]` và `/dashboard` cần orders.read, không expose inventory
payload. Lists/dashboard giới hạn 30 ngày, 5 items/page; exact ID lookup vẫn kiểm tra quyền.
Dashboard dùng native DB counts/status và snapshot Stars của đơn có primary MATCHED,
không cộng extra/unmatched receipts, không gọi là net sau refund. Staff resend cần thêm
fulfillment.manage, gửi tới buyer gốc qua confirmation có audit. T13 thêm explicit refund
khi delivery/resend uncertain; không clear uncertainty để resend. T12 ticket scope được
mô tả dưới đây.

| Boundary / tài sản | Abuse case | Control bắt buộc |
| --- | --- | --- |
| Telegram webhook → ingress | Fake/replayed/oversize Update | HTTPS production, secret header constant-time verification, input cap, SDK parsing, business idempotency. |
| Callback/actor → use case | IDOR, forged price/role, stale action | Server lookup, ownership, current permission/state checks, action dedup. |
| Admin import → inventory | Malformed/duplicate/large upload, secret leak | Bounded atomic parser, encryption, constraints, sanitized errors; không lưu plaintext file. |
| DB/work → Telegram | Duplicate send, stale worker, paid refund race | Durable attempt/lease/CAS, refund holds, uncertain reconciliation. |
| Operator/config → runtime | Token/key/DSN leak, wrong environment | Validated settings, startup fail-closed, redacted logs, dev/test/prod tách biệt. |

Initial ingress một process với bounded rate limit cho user/admin actions; nó không thay thế DB invariants. Shared counters bắt buộc trước multi-ingress; không dùng CORS/password/cookie controls từ ví dụ upstream khi app chưa có browser auth.

Không automatic fetch arbitrary URLs, giải nén archive hoặc thực thi nội dung sản phẩm. Media/file paths/references phải thuộc loại/hệ thống được cho phép. Dữ liệu từ Telegram, admin upload và docs/web đều là dữ liệu, không là lệnh cho agent.

## T13 privileged refund contract

Private `/refunds [PAYMENT_ID]` và menu Hoàn tiền dùng current `payments.refund`, queue
5/trang và safe payment/order IDs; không raw charge/payer/evidence. OWNER hoặc ADMIN có
explicit grant mới dùng được, SUPPORT và chỉ `orders.read` bị từ chối cả direct use case.
`/refund PAYMENT_ID` với reference ở dòng kế tạo preview full Stars/primary-vs-extra và
confirmation 10 phút actor/payment/state-bound. `/refund_uncertain` có warning riêng về
khả năng hàng đã giao, vẫn chặn active in-flight send. Reference mã hóa, không echo preview.

`/verify_charge PAYMENT_ID [OFFSET]` chỉ nhận matching incoming evidence cho extra/unmatched;
`/reconcile_refund PAYMENT_ID [OFFSET]` chỉ matching outgoing proof mới xác nhận refund.
Tối đa 100 transactions/trang, offset 0–1000000; không có arbitrary payer/amount/charge input
hoặc negative-proof retry. Revoke trong lúc API lookup được check lại trước persist. Actual
staff actor/audit atomic, callback ack trước API, plain text/no previews/Back/Home/Cancel.
Known failed outcome cần action mới; uncertain giữ owned hold, không gửi lại refund hoặc
delivery mù. Invariants và receipt intake theo [orders-payments](orders-payments.md#refund).

Không thêm key/provider/server cho local T13. Trước external T14 cần existing test bot/
account/config qua môi trường an toàn, current refund grant và Owner chốt policy; không
xin token/key trong chat hoặc chạy real-account refund từ authorization viết code.

## T12 ticket và public terms contract

T12 ticket contract: GENERAL/PAYMENT/DELIVERY, OPEN/CLOSED, optional immutable own-order
link; message text tối đa 2000 UTF-8 bytes, mã hóa với ticket/customer/message/author-bound
AAD qua keyring hiện có. Lists 5/trang, thread 1 message/trang để output bounded. Customer
chỉ own ticket; current tickets.manage cho staff, SUPPORT chỉ assigned tickets. OWNER/ADMIN
có grant mới assign/reassign; target cần current ticket grant. Assignment/closure có
actor/mode/version/10-minute confirmation, audit atomic. Replies idempotent theo author/
request key, immutable, chỉ xem trong inbox; không hứa đã push tới khách. No secrets/PII body
trong logs/audit/queue. Linked-order context dùng public snapshot sau ticket scope check.

`/support`, `/paysupport`, `/terms` và order-help giữ Back/Home/Cancel. Terms lấy từ public
UTF-8 file .md/.txt ≤24000 bytes qua optional SHOP_TERMS_PATH, page 3000 bytes;
không fetch URL/UNC, không tự bịa policy hoặc nội dung trống. Không cấu hình thì báo chưa
sẵn sàng; configured file lỗi/placeholder từ chối polling trước Bot/DB, fixed sanitized error.
Owner chọn temp.md nhưng còn placeholder nên phải hoàn thiện trước khi kích hoạt. Terms
acceptance gate tại checkout và live Telegram thuộc T14; refund/reconciliation thuộc T13.

## T14 core navigation và alerts

Customer có browse/search/account/orders/tickets/paysupport/terms, Buy→terms/invoice và
own checkout reopen/cancel. Admin theo current grants có products/inventory/resources/manual,
orders/dashboard/Customers, tickets, refunds, access/settings. Customers dùng numeric
Telegram ID, current orders.read trong use case, buyer UUID filter, 5/trang và 30 ngày;
không username lookup hoặc secret access. Discounts/referral/reports nâng cao theo T15–T17,
không render menu giả trước capability thật.

Worker hiện có discover metadata-only notifications mỗi 10s, tối đa 10/loại/tổng 100;
drain một intent/lượt. Operational alerts gửi current persisted Owner: payment review,
invoice uncertain, paid attention, failed/uncertain delivery, manual waiting, backlog >5m,
finite low-stock (một/ngày UTC/sản phẩm), refund uncertain. Support OPEN latest customer
post báo Owner; latest staff reply báo đúng ticket customer. Staff dùng assigned inbox,
không thêm fanout hoặc copy ticket body. Current source/recipient được recheck trước send;
obsolete→SUPPRESSED. Alert không tự thay financial state.

Durable dedup + SKIP LOCKED + commit SENDING trước network; result lưu transaction khác.
FAILED known refusal hoặc UNCERTAIN unknown/crash đều không tự retry. Late observation
không clear uncertainty, không reset history. Fixed metadata/logs và business inbox để
operator kiểm tra; chưa có alert retry UI. Notification failure không chặn paid worker.

Webhook cần explicit secret, stream cap 256KiB/read2s/dispatch20s/32slots; HTTP health
vẫn riêng không secrets. Account/BotFather/test endpoint/keys/terms/optional HTTPS registration
và checklist E ở [BOTFATHER-SETUP](../../BOTFATHER-SETUP.md). Owner chưa setup; chưa chạy
Telegram thật, restore hoặc production deployment. APP_ENV=test không tự chọn test Bot API.

## Operational readiness

- Health live/ready như ARCHITECTURE, structured redacted logs, alert về paid-attention/UNCERTAIN/worker backlog/low stock; không claim monitoring đã có khi mới log ra stdout.
- Graceful shutdown và restart giữ work; retry Telegram temporary failures chỉ khi semantics cho phép, theo spec fulfillment/refund.
- Backup DB và encryption keys theo hai nơi/quyền riêng; chạy restore rehearsal với dữ liệu test và key versions. Backup chưa restore thử không đạt gate.
- Deployment operator chạy migrations theo runbook, biết rollback code và forward-fix data; không destructive downgrade tự động.
- Test/live environments tách bot token, DB, webhook và encryption keys. Không `drop_pending_updates` hoặc đổi webhook bot thật để chạy smoke.
- Owner chốt privacy/retention/refund policy trước mở bán; schema/logging chỉ giữ dữ liệu cần cho business. Không tự hứa một jurisdiction/compliance status hoặc thêm export/deletion portal ngoài requirement.

## Acceptance criteria

- **AO-01:** User thường/staff thiếu quyền bị chặn ở handler và use case; sửa giá/kho/quyền/refund/manual completion có audit và chống replay.
- **AO-02:** Support ticket/order/customer lookup giữ ownership/scope; output/logs không chứa secret hoặc PII không cần thiết.
- **AO-03:** `/paysupport`, terms/support navigation, actionable paid failure/uncertain alerts hoạt động với nội dung test; real content là Owner gate.
- **AO-04:** Invalid/missing webhook secret, oversized inputs, rate-limit abuse và bad settings fail an toàn; secret không xuất hiện trong response/logs.
- **AO-05:** Restart/graceful shutdown, DB migration từ sạch/upgrade và restore với encryption keys được kiểm chứng trước release.
- **AO-06:** Refund dùng contract orders-payments; report/menu phản ánh dữ liệu thật và phân biệt rõ feature chưa có.

Verification: permissions/contract tests, PostgreSQL audit/state integration, operational fault/restore exercise; release evidence phải ghi environment/commands/results thật. Nguồn `/paysupport` và payment support: [Telegram Stars](https://core.telegram.org/bots/payments-stars#q-how-are-disputes-handled); [webhook authenticity/retry](https://core.telegram.org/bots/api#setwebhook).
