# ADR-0002: Lưu payment, cấp phát và giao hàng có thể phục hồi

- Status: Accepted, design only; chưa triển khai.

## Context

Telegram có thể gửi lại update; worker có thể chết, và Telegram request có thể thành công trước khi DB lưu kết quả. PostgreSQL transaction không bao gồm Telegram HTTP API. Yêu cầu không cấp trùng key phải được giữ cả khi có race, restart và kết quả mạng không rõ.

## Decision

DB transaction ghi valid payment, chuyển reservation thành allocation và tạo một fulfillment work duy nhất cho order item. `payments` cũng lưu receipt cần review khi chưa match được order; không đánh mất bằng chứng đã nhận tiền. Payment charge ID và allocation/work ownership được bảo vệ bằng unique constraints + row locks.

Worker dùng PostgreSQL work ledger/lease; không thêm Celery/Redis/broker. Commit trước network send và ghi attempt/result sau send. Một allocation được issue một lần; transport không được gọi là exactly-once. Expired `SENDING` attempt chuyển uncertain để reconcile, không tự cấp key/gửi/refund lại. Resend luôn tham chiếu cùng entitlement.

Reservation có TTL nội bộ **15 phút** ở checkout, được gia hạn tới ít nhất 15 phút từ lúc chấp thuận pre-checkout; đó không phải bảo đảm về thời điểm Telegram settlement. Payment đến sau expiry vẫn được lưu; nếu reservation không còn thuộc order thì PAID + attention, chờ giải quyết/refund, không lấy key của người khác. Chính sách cụ thể ở [orders-payments](../plans/shop/orders-payments.md) và [fulfillment](../plans/shop/fulfillment.md).

Refund là intent bền vững, side effect ngoài transaction, chỉ confirmed mới chuyển REFUNDED. Trạng thái uncertain phải reconcile trước retry. Không tự restock key từng gắn với paid order; không refund tự động trong khi delivery còn in-flight/uncertain.

## Alternatives

- Gửi hàng trong payment handler trước commit: mất receipt hoặc giao trùng khi retry.
- Background task trong memory: mất việc khi restart.
- Giữ DB transaction qua Telegram request: vẫn không atomic, lại giữ lock lâu.
- Auto-retry mọi timeout: có thể gửi lặp hoặc thực hiện refund không rõ trạng thái.

## Consequences và kiểm chứng

Cần work/attempt/refund records và một số case cần admin reconcile; đây là chi phí giữ tiền/kho đúng thay vì lời hứa transport không chứng minh được. Test PostgreSQL hai connection cho last-key, duplicate/late payment, lease expiry và refund/delivery race. Fault injection phải phủ crash trước/sau send/commit.

Nguồn đối chiếu: [Telegram Stars](https://core.telegram.org/bots/payments-stars), [webhook retry](https://core.telegram.org/bots/api#setwebhook), [refundStarPayment](https://core.telegram.org/bots/api#refundstarpayment). TTL và reconciliation policy là quyết định của dự án, không phải quy tắc Telegram.
