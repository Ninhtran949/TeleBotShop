# Spec: growth

Status: T15 Coupons hoàn tất local theo Owner24/10/2026; referral/marketing chưa triển khai. Nguồn: `task.md` §7, phạm vi Credit thay Stars theo ADR-0006 và yêu cầu Owner. Depends on: [admin-support-operations](admin-support-operations.md) và [Payment V2](payment-v2.md).

## Coupons

Coupon chỉ áp dụng **Credit checkout**, không giảm số tiền top-up Bank/USDT và không nối vào
invoice/pre-checkout Stars. Stars tiếp tục PAUSED cho tới khi Owner bật lại; historical
payment/refund receipts vẫn được giữ theo orders-payments.

Percentage1–99 hoặc fixed Credit discount. Mọi giá/discount/snapshot là integer bounded:
`discount_credits = base_credits * percent // 100`, hoặc fixed amount; discount phải dương
và total=base-discount phải dương. Không float, free order, stacking hay silent clamping.
Code ASCII3–32 ký tự, chuẩn hóa uppercase; config bất biến sau tạo, có enable/disable.
Expiry dùng UTC và DB clock tại checkout; now>=expires_at bị từ chối. Caps global và
per-buyer là positive integers. Scope toàn shop, một product UUID hoặc đúng category code
hiện tại; category không ngầm mở rộng sang con. Server kiểm tra product/resource/giá hiện tại.

Preview/confirmation không giữ lượt hoặc trừ ví. Khách dùng `/coupon SKU CODE` để xem
base/discount/total và xác nhận trả Credit bằng actor-bound token10phút. Buy thường vẫn dùng
giá base. Xác nhận coupon phải kiểm tra lại đúng coupon/product/base/discount/total, expiry,
active, scope và caps; giá hoặc điều kiện đổi thì từ chối để mở preview mới. Callback không
mang số tiền đáng tin cậy, không tự fallback sang giá đầy đủ khi coupon không hợp lệ.

Một UoW sở hữu buyer→product→coupon row lock→stock/order→wallet locks. Redemption finalized,
immutable base/discount/total snapshot, debit, payment, PAID, entitlement/READY work và audit
cùng commit. Unique order redemption + buyer/action idempotency; cùng action trả order cũ
không redeem/debit lại, kể cả coupon sau đó hết hạn/tắt hoặc order đã refund. Hai buyers
tranh lượt cuối được serialize bằng PostgreSQL row lock; counts đọc sau lock. Không có
reservation/late-payment coupon state cho Credit vì payment đồng bộ trong cùng transaction.
Stars receipt đến muộn không consume/release coupon hoặc đổi snapshot của đơn đã commit.

**Refund policy Owner chốt24/10:** refund Credit chỉ hoàn số Credit thực trả; **không hoàn
lượt coupon**, kể cả global/per-user cap. Redemption/audit giữ nguyên để chống mua–refund
lặp. Checkout thất bại/rollback không tiêu lượt và không debit. Không xóa/sửa redemption.

Admin dùng quyền tài chính **WalletPermission.PRICE** hiện có (Owner hoặc ADMIN explicit grant),
recheck trong transaction và audit. Tạo/list, bật/tắt coupon qua Telegram có actor-bound
confirmation/action key; không cấp quyền mới ngầm. Preview public không liệt kê mã bí mật;
history đơn giữ net Credit snapshot; không xây campaign/referral/marketing trong T15.

## Referral và marketing

- Deep link referral chỉ áp dụng cho user mới theo rule server; không self-referral, không đổi referrer sau khi đã gắn hợp lệ. Stats đọc từ committed paid/refunded facts, không count invoice hoặc callback click thành revenue.
- Channel config do Owner/quyền được cấp; product posts/deep links validate active product, không nhúng secret hoặc chấp nhận arbitrary callback target.
- Restock/marketing notifications chỉ cho subscriber đủ điều kiện và opt-in được lưu; có opt-out, dedup và rate limits. Không broadcast toàn user table mặc định, không tự bật paid broadcast hoặc ngân sách Stars.
- Không thêm commission payout, financial wallet, subscription hoặc external marketing service từ một referral count. Những việc đó cần requirement và security/Owner review riêng.

Data: coupons/redemptions/referrals và notification subscriptions/intents chỉ được migrate khi feature slice tới; không dựng trước một generic campaign platform.

## Acceptance criteria

- **GR-01:** Invalid/expired/disabled/wrong-scope/over-cap coupon bị từ chối; integer base/discount/total đúng, stale preview không tự đổi giá; direct calls và admin grants được kiểm tra.
- **GR-02:** Hai physical PostgreSQL transactions tranh lượt cuối hoặc per-user cap không over-redeem; double-click/lost ACK không debit lại; failure/commit rollback không mất lượt. Refund giữ usage và chỉ hoàn net Credit; late Stars receipts không tác động coupon.
- **GR-03:** Self-referral/reassignment bị chặn; reports xử lý refund đúng và không ghi giả doanh thu.
- **GR-04:** Chỉ opt-in user nhận eligible notifications; unsubscribe/replay/restart không tạo spam hoặc paid broadcast ngoài authorization.
- **GR-05:** Channel post tới đúng product, invalid target bị reject; production post/send chỉ thực hiện khi được cho phép.

Verification: unit rules, PostgreSQL redemption/referral races, mocked channel/notification contracts. Actual channel messaging là external gate riêng, không được suy ra từ test mock.
