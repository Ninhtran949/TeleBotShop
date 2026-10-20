# Spec: fulfillment

Status: design baseline; chưa triển khai. Nguồn: `task.md` §§4,5,11. Depends on: committed work contract của [orders-payments](orders-payments.md). [ADR-0002](../../adr/0002-payment-and-fulfillment-safety.md) quyết định delivery guarantee.

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
