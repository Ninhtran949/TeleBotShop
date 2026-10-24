# Shop design baseline và capability map

## Mục tiêu và phạm vi

**Cập nhật Owner24/10:** Stars external acceptance PAUSED/BLOCKED. Phạm vi đang được giao
là [Payment V2](payment-v2.md): Bank/VietQR và USDT nạp ví, Credit checkout độc lập với Stars.
[ADR-0006](../../adr/0006-payment-v2-wallet.md) mở rộng baseline bên dưới cho local/sandbox;
các contract/evidence Stars vẫn được bảo toàn.

Một khách có thể mở bot, xem hàng thật trong database, thanh toán **ở Telegram test environment**, nhận đúng entitlement; admin quản lý giao dịch, kho, support/refund an toàn. Bán tài nguyên hợp pháp và có quyền phân phối; tiếng Việt mặc định. Nguồn yêu cầu: [task.md](../../../task.md), sections 1-13.

Owner giao SE/SA tự thiết kế rules/spec/plan. Các file này là baseline kỹ thuật được chọn cho agent sau; **không phải yêu cầu chạy app, thiết lập tài khoản hoặc giao dịch ngay**. Không có application implementation hoặc external validation tại thời điểm thiết kế. Các tiêu chí bên dưới là acceptance cần đạt, không phải test results.

## Capability map

| Module ID / spec | Phạm vi, dữ liệu/contract chính | Depends on | task.md |
| --- | --- | --- | --- |
| [identity-access](identity-access.md) | Telegram identity, roles/permissions, actor context | — | §§3,6,9 |
| [catalog-inventory](catalog-inventory.md) | categories/products, typed payloads, inventory units/reservations | identity-access | §§3,4,8,10 |
| [orders-payments](orders-payments.md) | orders/items, price snapshots, receipts/refunds, committed fulfillment intent | identity-access, catalog-inventory | §§3,5,8,11 |
| [fulfillment](fulfillment.md) | Consume committed work, attempts/lease, dispatch và recovery | orders-payments | §§4,5,11 |
| [admin-support-operations](admin-support-operations.md) | Admin transport, tickets/audit queries, health/security/backup gates | identity-access, catalog-inventory, orders-payments, fulfillment | §§6,9,10,11 |
| [growth](growth.md) | Coupons/redemptions, referrals, opt-in channel/notification/reporting | admin-support-operations | §7 |
| [payment-v2](payment-v2.md) | Bank/USDT top-ups, immutable credit ledger, Credit checkout, manual review/refund/reconcile | identity-access, catalog-inventory, orders-payments, fulfillment | Owner24/10 |

Build order: identity → catalog/inventory → orders/payments → fulfillment → complete operations → growth. Admin writes đơn giản được dựng sớm trong slice tương ứng; không cần chờ hoàn thiện toàn bộ admin menu mới có catalog hoặc kho thật.

Các ID là ổn định. Đây là capability/work ownership trong **một monolith**, không phải sáu deployment/database. Import dependency theo ARCHITECTURE. `orders-payments` publish work bằng DB contract trong spec của nó; không import/gọi worker, nên không có vòng phụ thuộc orders ↔ fulfillment. Fulfillment tham chiếu order/domain contracts, không tự tạo phiên bản model thứ hai.

## Baseline được chọn và phần để sau

- Checkout ban đầu một sản phẩm, số lượng 1, một order item; giữ `order_items` vì đó là dữ liệu yêu cầu và đơn vị entitlement. Chưa có giỏ hàng/multi-item checkout hoặc subscription.
- Core nghiệm thu gồm đủ năm product types, payment/stock/recovery, quyền, admin/support/refund và vận hành cần thiết; một prototype chỉ giao mock key không đạt.
- Coupons/referral/channel/reporting nâng cao là phase sau core; không xóa yêu cầu, không làm sớm để che core chưa hoàn tất. Mini App/Web Admin/React và commission payout không nằm trong baseline ban đầu.
- Stars là lựa chọn gốc. Owner đã giao triển khai Bank/QR/USDT và Credit local/sandbox theo
  ADR-0006; không áp dụng Stars invoice/pre-checkout gates cho Credit checkout. Thông số API
  phải kiểm tra bằng official docs. Go-live và tiền thật vẫn cần authorization riêng.

## Quyền sở hữu tài liệu và kế hoạch

[ARCHITECTURE](../../ARCHITECTURE.md) sở hữu layout/import/transaction boundaries; [CONVENTIONS](../../CONVENTIONS.md) sở hữu cách code/check; [ADR](../../adr/README.md) lưu lý do chọn. Mỗi capability spec sở hữu contract và AC của mình; [implementation plan](implementation-plan.md) là task list/status duy nhất. Không tạo thêm root PLAN/SPEC, tasks/todo hoặc một tracker song song.

Thông tin cần Owner trước external/live gates: inventory/quyền phân phối, shop/support identity, terms/refund/retention policy, test/production settings. Đây là đầu vào triển khai/vận hành, không được agent tự invent hoặc yêu cầu gửi secrets trong chat.
