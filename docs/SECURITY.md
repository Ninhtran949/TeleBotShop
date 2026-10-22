# Security controls — T03–T12

Phạm vi hiện có: identity, permission, audit, public catalog/admin, encrypted key/license
inventory/resource sources, orders/approval/receipt/paid entitlement, typed delivery worker,
manual completion và development polling. Đây là controls đã
triển khai/automated-test, chưa là production hoặc toàn bộ shop security acceptance.
History/resend và support tickets có T11–T12 local evidence. Refund, webhook và release có gate riêng trong
[implementation plan](plans/shop/implementation-plan.md).

| Boundary | Control hiện tại | Evidence |
| --- | --- | --- |
| Telegram → identity | Actor từ authenticated Bot API Update; private chat ID bằng actor ID, chặn bot/group/inline/inaccessible message. Username chỉ là metadata. `/start` upsert theo unique ID, giữ UUID/registration; chỉ `get_my_profile(actor_id)` đọc profile. | Customer bot contracts, PostgreSQL concurrent identity và bot integration. |
| Config → Owner | Explicit `OWNER_ID`, persisted singleton khóa transaction, một Owner role tối đa; mismatch/missing role fail-closed, restart không khôi phục grants. | Same/different-ID bootstrap races, removed-role/mismatch tests. |
| Actor → protected use case | Default deny, đọc quyền DB hiện tại, explicit staff grants dưới role ceiling; Owner-only access/settings mutations. Actor context không chứa cached role. | Authorization matrix, direct unauthorized calls, grant/revoke và private admin contracts. |
| Customer/staff → resource | Customer ownership và SUPPORT assignment là domain/application contracts từ resource server đã load; staff không impersonate buyer. | Unit ownership matrix, native scoped order/ticket endpoints và PostgreSQL assigned/unassigned SUPPORT checks. |
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
| Document → inventory import | Current inventory.manage trước download và trong write UoW; TXT/CSV allowlist, actual memory cap 1 MiB/1.000 rows, UTF-8, bounded keys, 12s overall download deadline. SDK getFile path validate, local-file mode từ chối; không temp plaintext/URL imports. | Metadata/actual-size/path/permission contracts, revoked-during-download PostgreSQL dispatcher test. |
| Secret payload → storage | Typed key/license validation; AES-GCM 256-bit, fresh 96-bit nonce, product/SKU/unit/type/version AAD; separate HMAC-SHA256 dedup key. DTO/repr/errors/audit không chứa material. | Round-trip, wrong type/key/AAD/nonce/ciphertext, old-version read, exact material preservation tests; stored envelope assertions. |
| Crypto config → runtime | Canonical base64 32-byte keys, JSON object versions, separate dedup key; HMAC verifier guard pin trong DB, không tự rotate dedup/replace old AES key. HTTP không cần keys, polling fail trước Telegram khi cấu hình sai. | Missing/malformed/reused/changed/removed keys, CLI redaction, startup-before-network và key guard rollback tests. |
| Import/retire → stock | Actor-bound 10-minute durable confirmation, encrypted pending only, per-SKU unique fingerprint, immutable units, AVAILABLE→RETIRED conditional update; stock/audit/applied atomic. | Full 1.000-row batch, two physical DB connections, duplicate/replay/cancel/expiry/retire races, immediate/deferred audit rollback và direct-SQL constraints. |

`authorize()` là read use case cho handler/menu. Protected mutations tiếp theo phải gọi
`require_permission(work, actor_id, permission, server_loaded_scope)` trong chính UoW của
mutation; không dùng kết quả `authorize()` cũ để bỏ qua current permission check.

T04 không nhận private inventory/delivery payload trong product metadata. Mọi text trong
form là công khai; staff không được nhập secret vào description/instructions. Parser chỉ
cho fields của từng type, SQL CHECK chặn extra/missing/wrong-type fields; điều này không
tự xác định một đoạn text tự do có bí mật do người vận hành nhập nhầm hay không.
Photos chỉ dùng reference từ photo Update hoặc clear; không fetch arbitrary URLs/paths.
Catalog ACTIVE là visibility, không tự chứng minh đủ stock/payload để checkout. T06 chỉ
reserve finite AVAILABLE units; T08 có receipt/READY intent, T09 có finite worker; public Buy còn core UI T14.

Inventory secret payloads là dữ liệu nhạy cảm; stock views chỉ trả IDs/status/counts.
Không có admin/customer decrypt hoặc bulk export use case trong T05. Crypto adapter có
authenticated open primitive để kiểm chứng integrity/old versions; application crypto port
T05 chỉ có seal/fingerprint/proof; T09 thêm open trong port cho authorized finite worker.
Order/payment/buyer/destination/exact allocation/type/hold/token/lease được kiểm tra trước
load envelope/decrypt, kiểm tra lease lại trước SENDING. Không có bulk/customer decrypt API;
không suy ra acceptance giao hàng thật từ crypto hoặc mocked Telegram tests.
Pending import không giữ plaintext; apply/cancel đặt encrypted envelopes về NULL. Expired
actions vẫn giữ encrypted envelope cho tới khi cancel/retention được Owner chốt; không có
background deletion policy hoặc guarantee xóa khỏi PostgreSQL WAL/backups.
Keyring, dedup key và backup key nằm ngoài DB/source/Telegram; mất key không phục hồi
inventory chỉ từ DB. Thêm version giữ old keys; bulk rotation/removal/restore chưa làm.

Owner dùng `/grant`/`/revoke` sau khi staff đã `/start`. Role ceiling không tự cấp quyền.
Đổi ADMIN/SUPPORT phải thu hồi hết quyền cũ, rồi grant dưới role mới. `payments.refund`
là permission foundation; chưa có refund operation. `tickets.manage` không tự tạo ticket
scope và không cho SUPPORT xem order/key. Quyền access/settings không delegate, Owner
transfer không có command trong T03; mismatch cần khôi phục đúng config hoặc quy trình
ownership riêng được Owner cho phép.

Development polling chỉ nhận `development`/`test`, kiểm tra schema trước xử lý, từ chối
webhook đang cấu hình và không xóa/drop updates. T07 concurrent tasks cap 32, mỗi task
có session riêng; shutdown drain/cancel trước khi đóng SDK session/DB engine. HTTP health độc lập
với BOT_TOKEN/OWNER_ID/inventory keys; token chỉ đọc khi operator chủ động chạy polling. Automated tests
dùng fake token và mock Telegram transport, không gửi messages hoặc đổi tài khoản thật.

T06 customer order use cases kiểm tra registered buyer từ DB; UUID action/order/product
không cấp quyền. Một action chỉ giữ một product; callback không có authority về giá/quantity.
Checkout/payment không đọc/decrypt ciphertext; inventory views đếm riêng AVAILABLE/RESERVED/ALLOCATED/RETIRED.
Owner check bảo vệ get/cancel/send dù caller bỏ qua handler. TTL/lock budgets dùng DB time,
guarded release chỉ chạm exact reservation; catalog price edits không sửa order snapshot.

Invoice adapter chỉ gửi private buyer ID, snapshot public text/Stars, opaque payload và
nonempty start_parameter. Không xin name/email/phone/shipping, không dùng payment provider
ngoài Stars. SDK error/body/token không đi vào DTO/log; known rejection khác unknown send.
SENDING/UNCERTAIN không tự retry, kể cả crash hoặc mất commit sau send. Claims cũ được
DB-only maintenance đánh dấu UNCERTAIN, giữ history chờ reconciliation. Chưa có permissioned
invoice resend/reconcile command hoặc public Buy; core UI còn T14, không có live validation.

T07 validate query ID (opaque, tối đa 256 ký tự, không control), server nonce payload,
positive integer amount và Telegram buyer; không log raw query/order_info/SDK error.
Buyer/current product/state/exact reservation được kiểm tra trong locked UoW. One approval
per order/query; DB chặn mutate/delete/truncate, expiry không clear query binding. Approval,
hai TTL và audit atomic; API answer sau commit, uncertainty không auto-reset/retry.
T08 nối checker thật cùng durable receipt intake; service success chỉ được chứng minh
trên PostgreSQL thật + Telegram mock. Public Buy vẫn chưa bật, core UI thuộc T14.
Giới hạn DB 4s/API 3s được kiểm tra với real lock/pool contention; chưa là Telegram live SLO.

T08 receipt là financial event riêng: bypass ordinary message rate/private/profile checks
trước handler để không mất bằng chứng nhận tiền; application vẫn xác minh payer từ Telegram ID,
snapshot owner/XTR/amount/payload trước credit. Missing payer, unknown/wrong/extra/unsupported
receipts lưu REQUIRES_REVIEW. Same charge conflict giữ canonical, append minimal evidence;
không lưu raw Update, name/email/phone/address/order_info, plaintext key hoặc secret vào audit.
Receipt DTO có byte/type/control bounds, repr ẩn; malformed ngoài policy fail trước ack,
cần operator reconciliation thay vì silently drop. Charge/provider/shipping cap 1024 bytes,
currency 16 và invoice payload 128 bytes là input policy, không gán cho Telegram published cap.
Provider reference được giữ như evidence, không là authority về price/stock hoặc lý do reject XTR.

Polling persist trước SDK offset; DB failure dừng fetch trước confirm receipt. Handler await
commit trước return, errors không bị ordinary guard nuốt. ASGI harness 500/re-delivery test
chưa chứng minh authenticated production webhook. Không delivery/send/decrypt trong handler.
Unique charge + primary/order + item/work, row locks, immutable ALLOCATED và deferred commit
constraint giữ ledger/entitlement atomic; paid không release key. Conflict/attention cần
permissioned reconciliation/refund ở task sau, chưa có command tự giải quyết.

T09 worker dùng attempt UUID như private fencing capability, không dùng order/callback ID
để tự cấp quyền decrypt. Claim commit riêng trước order→work prepare; repo không commit.
Finite sender chỉ gửi đúng private destination/type, nguyên văn secret, parse_mode=None,
disabled previews, deadline/timeout có giới hạn; DTO repr ẩn, attempts/audit/log không chứa key,
token, SDK body hoặc profile PII. Worker kiểm tra schema/Owner/key verifiers trước tạo Bot;
settings có lease/send/timeout/retry bounds, không tự biến APP_ENV=test thành Bot API test server.

Known rate rejection có retry-after + bounded exponential jitter/count; unknown timeout,
SENDING expiry hoặc result quá lease về UNCERTAIN, không auto send/refund/reallocate. Claim
chưa gửi hết lease mới requeue. Fault tests phủ rollback cả ba commit và mất ACK sau result
commit; cancellation giữ durable work, Bot/engine đóng. Observation enum/shape/immutability
chặn nhét freeform SDK errors vào ledger. Hold thắng order lock chặn decrypt/send; in-flight/
uncertain chặn automatic hold/refund. Refund/reconcile/resend authorization/UI còn task sau.
Operational alert hiện là durable attention/audit + warning work UUID/fixed code; staff
notification delivery T14. Two-worker và two-order-lock-direction tests dùng PostgreSQL thật.

T10 source registration yêu cầu current `inventory.manage` trong use case, actor/product/
expected-version-bound confirmation. Source context tách khỏi finite inventory, immutable
ciphertext và order binding; sửa nguồn không cấp nội dung khác cho paid buyer. File chỉ
nhận bounded opaque `file_id` từ registered document, kiểm tra tên/MIME và typed format,
không fetch URL, download local file hoặc fallback wrong-type sang text. SendDocument ACK
thiếu document hoặc sai chat về UNCERTAIN. Các giới hạn reference/content là input policy
local, không gán thành Telegram API published caps.

Manual sender chỉ nhận customer notice; staff instructions được giữ trong encrypted source.
Queue/detail/prepare/confirm/cancel cần current `fulfillment.manage` trước ciphertext/decrypt;
read quyền `orders.read` không thay thế quyền mutation. ADMIN phải có explicit grant mới;
không backfill user grants. Private staff detail dùng plain text, disabled previews và read
audit. Evidence tối đa 500 UTF-8 bytes, mã hóa dưới work/action-bound AAD, không echo vào
preview/log/audit; general audit chỉ lưu digest. Confirmation actor/attempt/TTL-bound, giữ
access lock qua order→work→action mutation; hold/state/revoked/replay checks và PostgreSQL
deferred/unique guards bảo vệ completion. DB/audit failure rollback, lost ACK replay không
giao lần hai. Bằng chứng là staff attestation về xử lý bên ngoài, chưa được bot tự xác minh.

T11 history chỉ select public order snapshot/status/IDs, không invoice payload/ciphertext.
Customer scope được gắn trong SQL theo profile hiện tại; staff cần orders.read, không dựa
vào callback mode hoặc menu đã nhìn thấy. Staff resend cần thêm fulfillment.manage; giữ
access lock tới commit, buyer có ownership check riêng. Actor/action binding không cho
staff khác/Owner xác nhận hộ một pending action. Staff không nhận delivery payload qua UI.

Resend chỉ nhận DELIVERED + SENT nonmanual, không hold/attention; shared reader recheck
primary receipt/buyer/item/type/unit/resource trước decrypt. Không đổi destination hoặc
allocation, không mở lại refunded/non-delivered work. Begin→API→complete tách transactions;
DB unique/immutable/state guards bảo vệ replay và hold race. SENDING quá deadline được
hiển thị uncertain và persist khi action được gọi lại; crash/lost ACK/cancellation không
auto retry. Late result chỉ thêm observation, không clear uncertainty. Audit giữ action/work
IDs và fixed events, không lưu payload. PostgreSQL tests có hai physical connections.

Refund persistence/resolver vẫn T13; test REFUNDED hiện là injected repository DTO cho
fail-closed allowlist, chưa native refund ledger/race acceptance. Không suy live Telegram
hoặc exactly-once delivery từ mock contracts. Staff manual work không tự replay.

T12 ticket body có thể chứa dữ liệu riêng tư: giới hạn 2000 UTF-8 bytes, AES-GCM với AAD
ticket/customer/message/author/staff/key-version, repr-hidden; không plaintext/hash body
trong logs/audit hoặc metadata queue. Current tickets.manage và actual DB assignment kiểm
tra trước ciphertext/decrypt. SUPPORT không self-assign/global browse hoặc đọc inventory;
OWNER/ADMIN assign cần grant hiện tại, target cũng cần current ticket grant.

Assign/close confirmation ràng buộc actor/mode/ticket/version/TTL, giữ access→ticket→action
locks tới atomic commit/audit. Replay, reassignment/revocation, audit/commit failure/lost ACK,
cross-user/IDOR, tamper và native two-connection close/reply có tests. Independent review
không Required finding; concurrent revoke-vs-detail và reassign-vs-reply chưa có dedicated
two-connection tests, current shared access/ticket locks được kiểm tra qua code và các
focused sequential/lock tests. Queue 5/thread 1 và plain text/no previews giữ bounded output.
Reply lưu thread, không tự push; không đổi financial/allocation/delivery state.

Public terms là trusted operator file được cấu hình rõ, UTF-8 .md/.txt ≤24000 bytes, không
URL/UNC. Startup đọc bounded snapshot trước Bot/DB; malformed/missing/placeholder file
từ chối với fixed error, không echo đường dẫn/body. Không config thì báo chưa sẵn sàng;
không công bố draft temp.md tự động. Owner đã chọn nội dung nhưng còn phải điền placeholder.
Checkout consent/live acceptance T14, không suy ra legal/compliance status từ loader/tests.

Giới hạn cần hoàn thiện ở task tương ứng:

- Webhook HTTPS/secret/body cap, shared counters trước multi-ingress, operational
  monitoring và live Telegram/Stars evidence chưa có; T03 không nhận production webhook.
- Lock singleton ưu tiên correctness cho access/settings ít ghi; đo contention trước khi
  thu hẹp locks; inventory key registration cũng đang serialize writes. PostgreSQL role dùng local tests có quyền DDL; superuser/table owner có
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
