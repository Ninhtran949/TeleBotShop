# Spec: fulfillment

Status: T09–T10 đã triển khai typed worker đủ năm loại, claim/lease/recovery/retry/uncertain, encrypted source snapshots và permissioned manual completion. History/resend còn T11, actual refund/reconciliation T13; chưa toàn FD hoặc Telegram live acceptance. Nguồn: `task.md` §§4,5,11. Depends on: committed work contract của [orders-payments](orders-payments.md). [ADR-0002](../../adr/0002-payment-and-fulfillment-safety.md) quyết định delivery guarantee.

## Entitlement và dispatch

Một order item sở hữu một entitlement/work. Finite inventory đã allocated chỉ thuộc order item đó; không reserve/allocate key mới khi retry send hoặc phục hồi. Resource/type snapshot quyết định validator/dispatch; wrong-type payload fail closed.

- UNIQUE_KEY/LICENSE: decrypt đúng allocated unit sau quyền/ownership check, gửi tới private destination của buyer.
- DIGITAL_FILE: gửi registered Telegram file_id/document; không trả URL download có bot token hoặc nhận URL tùy ý để fetch.
- REUSABLE_RESOURCE: giao reference/content được phép phân phối từ entitlement đã lưu.
- MANUAL_FULFILLMENT: tạo staff work có audit, thông báo đang chờ; chỉ staff có quyền mới complete với bằng chứng phù hợp, không tự mark DELIVERED ngay khi payment tới.

Public order-history/resend gọi cùng authorized entitlement reader, không đọc inventory trực tiếp bằng ID từ callback. Resend sau delivery được ghi như attempt/action riêng, dùng cùng payload; refunded order không được tự mở lại entitlement bằng callback cũ.

## Durable worker và attempts

`fulfillments` là durable work ledger trong PostgreSQL; `delivery_attempts` lưu attempt ID, work ID, lease/fencing token, state, timestamps, sanitized error/result metadata và Telegram message ID nếu có. Không lưu plaintext payload trong attempts.

| Work/attempt state | Ý nghĩa và bước tiếp theo |
| --- | --- |
| READY / RETRY_WAIT | Có thể claim khi due, order hợp lệ và không bị refund hold. |
| CLAIMED | Lease ngắn đã commit; chưa được gửi. Stale worker phải fail ownership/expiry check. |
| SENDING | Attempt được persist và commit trước HTTP call; không worker thứ hai gửi work này. |
| SENT | API success + kết quả đã persist, order có thể DELIVERED. |
| UNCERTAIN | Không biết side effect đã xảy ra; dừng auto-send, báo staff và reconcile. |
| FAILED | Known permanent failure hoặc hết retry budget; paid entitlement giữ nguyên. |
| AWAITING_MANUAL | Staff work chờ authorized completion, không là đã giao. |

Claim dùng `FOR UPDATE SKIP LOCKED`/conditional claim và lease token; transaction ngắn, không network trong lock. Claim transaction commit trước transaction lấy order lock để không đảo lock order. Trước send, recheck order/payment/refund hold và lease, rồi CAS sang SENDING trong transaction đã lock order/work; send sau commit. Worker cũ không được vượt CAS hoặc persist đè attempt mới.

Lease hết khi CLAIMED và chưa gửi có thể requeue theo token; lease hết khi SENDING phải UNCERTAIN, không requeue mù. Nếu Telegram đã nhận message nhưng DB chưa ghi SENT, local state không đủ để chứng minh chưa gửi. Refund không tự đi tiếp khi attempt còn SENDING/UNCERTAIN.

Known transient rejection có bằng chứng chưa accepted (ví dụ rate-limit response) được bounded exponential backoff/jitter, tôn trọng retry-after. Network timeout/connection loss sau khi có thể đã gửi không thuộc nhóm retry tự động. Budget/timeout pin trong settings và tests, không loop vô hạn hoặc nuốt lỗi; known permanent failure tạo attention/support path.

Recovery scan durable rows khi startup; không dựa vào task/FSM/in-memory queue cũ. Kết quả muộn chỉ được ghi cho đúng attempt/token còn có quyền; nếu đã có manual intervention thì lưu observation an toàn, không overwrite quyết định mới. UNCERTAIN muốn resend cần authorized action rõ ràng và cảnh báo cùng nội dung có thể đã được gửi trước đó.

## T09 implementation evidence và policy

Work states READY/RETRY_WAIT/CLAIMED/SENDING/SENT/UNCERTAIN/FAILED đã có. Attempts thêm
RETRYABLE và ABANDONED (claim chưa gửi bị recovery/hold); attempt UUID đồng thời fencing token.
Giữ cùng CommittedWork của T08, exact allocation không đổi, encrypted reader chỉ chạy sau
server-side entitlement checks. Work SENT đi cùng order DELIVERED; FAILED vẫn paid trên ledger.
Late/stale result có append-only typed/digest observation, không overwrite newer/manual state.

Default claim lease 30s, send lease 60s, API timeout 10s, tối đa 3 sends (config cap 5), idle
poll 1s. Known rate-limit response: 2s exponential tới 60s + jitter, không sớm hơn retry_after;
retry-after ngoài 1–86400s dừng FAILED theo input policy, không nhận là Telegram published cap.
Budget được recheck trước decrypt sau restart. CLAIMED hết lease requeue; SENDING hết lease
hoặc result quá lease UNCERTAIN, không blind retry. Sender kiểm tra absolute UTC deadline trước
API; production cần clock sync. Cancellation không giả SENT/FAILED, durable SENDING chờ recovery.

`telebotshop worker` chỉ development/test; startup/mỗi lượt recover DB ledger, claim/send
tuần tự mỗi process, nhiều process cạnh tranh qua PostgreSQL. API không giữ UoW/connection.
Refund hold là DB pre-send boundary có tests cả hai order-lock outcomes, chưa có refund setter/
operations UI trước T13. Alerts hiện là attention/audit + redacted warning, staff Telegram
notification routing T14. Public Buy/core navigation còn T14.

## T10 typed resource và manual contract

`resource_versions` giữ typed encrypted DIGITAL_FILE (file_id/name/MIME), REUSABLE_RESOURCE
TEXT hoặc DOCUMENT, MANUAL_FULFILLMENT (customer notice + private staff instructions).
Nguồn có public metadata snapshot, protected actor/product/expected-source confirmation;
order_resources chốt một source UUID/item trước invoice. Nguồn retired còn hiệu lực với
order đã chốt. Finite unit và nonfinite resource dùng XOR/FKs riêng; không giả finite stock.

Worker mở đúng bound resource sau paid/buyer/item/destination/type/hold/fence checks,
kiểm tra payload/metadata và gửi document hoặc plain text. File chỉ nhận opaque file_id,
không fetch URL hoặc fallback sang text; file ACK thiếu document là UNCERTAIN. Manual sender
chỉ nhận ManualNotice, không nhận staff instructions. Success của waiting notice giữ
attempt SENT nhưng work AWAITING_MANUAL và order FULFILLING; không tự mark DELIVERED.

Manual queue/detail/complete cần current `fulfillment.manage`, ADMIN có explicit grant;
`orders.read` không cho đọc hướng dẫn/giao hàng. Staff xử lý bên ngoài rồi tạo confirmation
với nonempty evidence ≤500 UTF-8 bytes. Evidence được mã hóa dưới product/SKU/work/action/
key-version AAD riêng; preview/log/audit không echo reference. Confirm trong 10 phút, cùng
actor/attempt và paid waiting work, không hold, giữ quyền tới commit. Action APPLIED,
work SENT, order DELIVERED và audit digest atomic; replay chỉ trả đã hoàn tất. Hai action
hoặc hai connection không thể complete hai lần; cancelled/expired/revoked/held/stale bị chặn.
Staff attestation không thay thế live evidence về công việc thực tế. Failed/uncertain notice
không được complete qua command này trước khi reconciliation thuộc task sở hữu giải quyết.

Verify local: native crypto context/format/tamper tests; PostgreSQL source-vs-checkout,
publication/completion/hold races, commit/lost-ACK/immutable-history/migration tests;
runtime admin/message/document contracts mock Telegram, bảo đảm không network trong UoW.
Không có thêm key/provider/server cho T10 local; Telegram external acceptance còn T14.

Nguồn API đối chiếu 2026-10-22: [sendMessage](https://core.telegram.org/bots/api#sendmessage),
[retry_after](https://core.telegram.org/bots/api#responseparameters),
[aiogram 3.31.0 sendMessage](https://docs.aiogram.dev/en/latest/api/methods/send_message.html).
Đây là local/mock evidence, không bảo đảm exactly-once network delivery hoặc production readiness.

## Guarantee và giới hạn

Cam kết duy nhất ở issuance/ownership: một unit không cấp cho hai khách, một order item không được cấp key khác vì delivery lỗi. DB và Telegram không có chung transaction. Vì vậy không thể tuyên bố message được gửi chính xác một lần trong mọi crash/timeout; duplicate message khi manual resend không được biến thành duplicate resource allocation.

## Acceptance criteria

- **FD-01:** Hai worker không cùng send một active work; token stale không gửi hoặc overwrite kết quả.
- **FD-02:** Restart trước claim/sau claim/sau SENDING/sau Telegram success trước DB result không mất paid work hoặc tạo allocation mới; ambiguous case về UNCERTAIN.
- **FD-03:** Retry của known failure giữ cùng payload/unit và có giới hạn; uncertain/permanent failure không auto-resubmit.
- **FD-04:** Đủ năm type có delivery/manual completion đúng; file reference sai và payload/AAD sai không fallback sang text.
- **FD-05:** History/resend chỉ cho đúng buyer và state được phép; staff không dùng order ID để đọc secrets ngoài quyền.
- **FD-06:** Refund hold race chặn send mới; in-flight/uncertain phải resolve hoặc explicit manual decision, không giả transaction có thể thu hồi message.

Verification: fake Telegram port cho known/uncertain outcomes; PostgreSQL tests với competing workers/expired leases và fault injection tại từng commit/send boundary; official test bot smoke cho actual message/document delivery. Không test bằng sleep theo TTL thật; dùng controlled timestamps/clock phù hợp.
