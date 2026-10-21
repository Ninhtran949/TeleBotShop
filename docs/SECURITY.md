# Security controls — T03/T04

Phạm vi hiện có: identity, permission, audit, public catalog/admin và development polling. Đây là controls đã
triển khai/automated-test, chưa là production hoặc toàn bộ shop security acceptance.
Payments, inventory encryption, orders/tickets, webhook và release có gate riêng trong
[implementation plan](plans/shop/implementation-plan.md).

| Boundary | Control hiện tại | Evidence |
| --- | --- | --- |
| Telegram → identity | Actor từ authenticated Bot API Update; private chat ID bằng actor ID, chặn bot/group/inline/inaccessible message. Username chỉ là metadata. `/start` upsert theo unique ID, giữ UUID/registration; chỉ `get_my_profile(actor_id)` đọc profile. | Customer bot contracts, PostgreSQL concurrent identity và bot integration. |
| Config → Owner | Explicit `OWNER_ID`, persisted singleton khóa transaction, một Owner role tối đa; mismatch/missing role fail-closed, restart không khôi phục grants. | Same/different-ID bootstrap races, removed-role/mismatch tests. |
| Actor → protected use case | Default deny, đọc quyền DB hiện tại, explicit staff grants dưới role ceiling; Owner-only access/settings mutations. Actor context không chứa cached role. | Authorization matrix, direct unauthorized calls, grant/revoke và private admin contracts. |
| Customer/staff → resource | Customer ownership và SUPPORT assignment là domain/application contracts từ resource server đã load; staff không impersonate buyer. | Unit ownership matrix và PostgreSQL assigned/unassigned SUPPORT checks. Order/ticket endpoints thật thuộc T11/T12. |
| Callback → mutation | Random UUID token lưu DB, actor/target/action/expected version/10-minute expiry; cancel và applied dedup. Không lấy target/price/role từ confirm callback. | Concurrent confirmations/grants, stale/expired/cancelled/replayed tokens, role-switch tests. |
| Mutation → audit | Mutation, redacted audit và applied marker cùng commit; unique correlation; PostgreSQL append-only trigger chặn UPDATE/DELETE/TRUNCATE. | Immediate audit/deferred commit failure rollback, audit tampering tests. |
| Transaction → Telegram | Repo không commit; use case giữ SHARE authorization lock hoặc exclusive mutation lock đến hết transaction; đóng UoW rồi mới send/edit/ack. | Real lock-timeout/revoke test, independent sessions, pool assertion tại từng mocked Telegram call. |
| Settings → Telegram | Chỉ support username nullable, regex ASCII/length và Owner confirmation; không có token/DSN/keys/owner-transfer setters hoặc views. | Settings validation, permission, stale/replay và redacted audit tests. |
| Input → resources | Một ingress; 8 message/callback actions trong fixed window 10 giây/user, tối đa 4096 active counters; đầy capacity từ chối identity mới cho tới expiry. | Rate-limit/capacity tests, guard chạy trước handler/use case. |
| SDK/DB error → user/log | SecretStr/repr-hidden config, repr-hidden profile DTO, fixed safe domain errors và transport messages; log fixed error codes, SDK log filter xóa args/tracebacks. | Malformed input, CLI errors, DB/SDK secret/PII redaction tests. |
| Catalog input → persistence | Five typed public schemas, positive integer Stars, immutable SKU/type, category FK/depth/identity, hidden-by-default products; no delivery fields. | Unit/wrong-type tests và PostgreSQL constraints, duplicate-create races, metadata round-trip. |
| Staff → catalog mutation | Current `catalog.manage` ở handler và use case; catalog-owned actor/version/expiry-bound confirmations, row locks + CAS, audit atomic. | Revoked/cross-actor/stale/cancel/replay/concurrent price, immediate/deferred audit failure tests. |
| Public lookup/search → catalog | Chỉ ACTIVE trong list/search/detail; bounded pagination/literal escaped search; current price/state lấy DB. No client-supplied price/role trong confirm. | Hidden/stale callbacks, search injection/wildcard và real DB dispatcher flow. |
| Catalog → Telegram | Plain text, disabled link previews, ảnh chỉ là validated public Telegram photo reference; callback ack trước optional photo. | HTML/photo/transport-failure contracts và pool assertions ở từng send/edit/ack. |
| Navigation → resource use | Instance-local, actor-bound opaque view token, 4096-entry cap/10-minute logical expiry; chỉ giữ browse parameters, không giữ products/roles/stock. | Expiry/cross-user/capacity tests; expiry/restart yêu cầu mở lại `/shop`. |

`authorize()` là read use case cho handler/menu. Protected mutations tiếp theo phải gọi
`require_permission(work, actor_id, permission, server_loaded_scope)` trong chính UoW của
mutation; không dùng kết quả `authorize()` cũ để bỏ qua current permission check.

T04 không nhận private inventory/delivery payload trong product metadata. Mọi text trong
form là công khai; staff không được nhập secret vào description/instructions. Parser chỉ
cho fields của từng type, SQL CHECK chặn extra/missing/wrong-type fields; điều này không
tự xác định một đoạn text tự do có bí mật do người vận hành nhập nhầm hay không.
Photos chỉ dùng reference từ photo Update hoặc clear; không fetch arbitrary URLs/paths.
Catalog ACTIVE là visibility, không tự chứng minh đủ stock/payload để checkout. T04 chưa
có checkout/invoice hoặc fulfillment, không invent stock numbers hoặc nhận thanh toán.

Owner dùng `/grant`/`/revoke` sau khi staff đã `/start`. Role ceiling không tự cấp quyền.
Đổi ADMIN/SUPPORT phải thu hồi hết quyền cũ, rồi grant dưới role mới. `payments.refund`
là permission foundation; chưa có refund operation. `tickets.manage` không tự tạo ticket
scope và không cho SUPPORT xem order/key. Quyền access/settings không delegate, Owner
transfer không có command trong T03; mismatch cần khôi phục đúng config hoặc quy trình
ownership riêng được Owner cho phép.

Development polling chỉ nhận `development`/`test`, kiểm tra schema trước xử lý, từ chối
webhook đang cấu hình và không xóa/drop updates. Process xử lý tuần tự từng update; SDK
session và DB engine đóng qua context/finally kể cả lỗi/cancellation. HTTP health độc lập
với BOT_TOKEN/OWNER_ID; token chỉ đọc khi operator chủ động chạy polling. Automated tests
dùng fake token và mock Telegram transport, không gửi messages hoặc đổi tài khoản thật.

Giới hạn cần hoàn thiện ở task tương ứng:

- Webhook HTTPS/secret/body cap, shared counters trước multi-ingress, operational
  monitoring và live Telegram/Stars evidence chưa có; T03 không nhận production webhook.
- Lock singleton ưu tiên correctness cho access/settings ít ghi; đo contention trước khi
  thu hẹp locks. PostgreSQL role dùng local tests có quyền DDL; superuser/table owner có
  thể disable trigger. Production app/migration roles và backup/restore thuộc T18.
- Không tự xóa audit/actions hoặc profile: retention/privacy policy cần Owner chốt trước
  mở bán. Expired action bị chặn nhưng row vẫn được giữ; chưa có cleanup/retention job.
- Support username chỉ kiểm tra cú pháp, không kiểm tra ownership/account tồn tại. Owner
  phải cung cấp contact thật, terms/refund/retention policy và test/production credentials
  qua kênh an toàn. Không ghi chúng vào logs, docs hoặc fixtures.
- Locked dependency audit chỉ phản ánh advisories tại thời điểm chạy; chưa thay thế source,
  container, deployment review hoặc bằng chứng release trong T18.

Nguồn SDK/transport: [aiogram 3.31.0](https://pypi.org/project/aiogram/3.31.0/),
[Dispatcher/polling](https://docs.aiogram.dev/en/dev-3.x/dispatcher/dispatcher.html),
[Telegram User identity](https://core.telegram.org/bots/api#user).
