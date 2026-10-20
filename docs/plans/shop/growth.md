# Spec: growth

Status: designed for phase 4, chưa triển khai; chỉ làm khi được giao sau core checkout/operations. Nguồn: `task.md` §7. Depends on: [admin-support-operations](admin-support-operations.md) và các core contracts nó sử dụng.

## Coupons

Percentage hoặc fixed Stars discount, thời hạn, usage cap, per-user limit và product/category scope được kiểm tra server-side. Percentage là integer percent; `discount_stars = base_stars * percent // 100`, total phải còn là positive integer hợp lệ. Không silent rounding khác nhau giữa UI/invoice, không tạo free-order flow khi chưa có yêu cầu.

Giữ coupon price snapshot và usage reservation cùng order transaction, finalize redemption theo successful payment; expiry/cancel/late payment đi theo orders-payments, không vượt cap vì check-then-write hoặc re-apply discount khi callback lặp. Price sau invoice không được đổi vì coupon vừa hết hạn. Nếu reservation/eligibility không còn được bảo đảm, pre-checkout reject trước thanh toán; receipt đến muộn vẫn được ghi/review.

## Referral và marketing

- Deep link referral chỉ áp dụng cho user mới theo rule server; không self-referral, không đổi referrer sau khi đã gắn hợp lệ. Stats đọc từ committed paid/refunded facts, không count invoice hoặc callback click thành revenue.
- Channel config do Owner/quyền được cấp; product posts/deep links validate active product, không nhúng secret hoặc chấp nhận arbitrary callback target.
- Restock/marketing notifications chỉ cho subscriber đủ điều kiện và opt-in được lưu; có opt-out, dedup và rate limits. Không broadcast toàn user table mặc định, không tự bật paid broadcast hoặc ngân sách Stars.
- Không thêm commission payout, financial wallet, subscription hoặc external marketing service từ một referral count. Những việc đó cần requirement và security/Owner review riêng.

Data: coupons/redemptions/referrals và notification subscriptions/intents chỉ được migrate khi feature slice tới; không dựng trước một generic campaign platform.

## Acceptance criteria

- **GR-01:** Invalid/expired/wrong-scope/over-cap coupon bị từ chối; integer rounding/snapshot nhất quán, duplicate payment không redeem hai lần.
- **GR-02:** Hai checkout tranh lượt coupon cuối và late-payment cases không vượt cap hay mất receipt.
- **GR-03:** Self-referral/reassignment bị chặn; reports xử lý refund đúng và không ghi giả doanh thu.
- **GR-04:** Chỉ opt-in user nhận eligible notifications; unsubscribe/replay/restart không tạo spam hoặc paid broadcast ngoài authorization.
- **GR-05:** Channel post tới đúng product, invalid target bị reject; production post/send chỉ thực hiện khi được cho phép.

Verification: unit rules, PostgreSQL redemption/referral races, mocked channel/notification contracts. Actual channel messaging là external gate riêng, không được suy ra từ test mock.
