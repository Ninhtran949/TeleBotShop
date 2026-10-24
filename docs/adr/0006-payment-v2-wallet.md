# ADR-0006: Bank/USDT top-ups và checkout bằng closed-loop credit

- Status: Accepted for local/sandbox implementation, Owner 24/10/2026.
- Extends ADR-0001/0002/0003; giữ nguyên Stars module, migrations và evidence T14.
- Supersedes lựa chọn Stars-only cho scope local/sandbox Owner24/10; không sửa ADR Stars gốc.

## Context và audit

T14.a–d có core local; external Stars E3/E4 chưa đạt. Owner tạm dừng Stars và chọn
Bank/VietQR, Binance/USDT để nạp ví; wallet credit là payment của order. Authorization này
cho phép thiết kế và triển khai sandbox, không phải nhận tiền thật, trade/withdraw hoặc deploy.

Reuse identity/current grants/audit/UoW, catalog, inventory encryption, order snapshots,
permanent allocations, resource bindings, durable fulfillment/manual/history/resend. OrderService
hiện commit reservation trước invoice; PaymentService chỉ nhận Telegram receipt XTR; không
gọi hai use cases đó liên tiếp để giả lập wallet payment. Wallet checkout cần một transaction.

Coupling phải mở: order_items.price_stars/currency constraint, order query inner-join invoice,
payments receipt-only fields, deferred check_committed_payment và revenue projection Stars.
Delivery dùng payment UUID/entitlement, không cần Telegram successful_payment: giữ contract này.

## Decision

1. Modular monolith và existing UoW, không thêm framework/service hay dependency.
2. Ledger double-entry immutable, integer credit (sandbox 1 credit = 1 VND). Customer balance
   không âm; system clearing/sales accounts đối ứng. DB triggers giữ balance = postings,
   journal cân bằng tại commit, cấm sửa/xóa/truncate ledger và cập nhật balance trực tiếp.
3. Bank/USDT evidence và top-up tách order payments. Unique canonical transaction + unique
   credit journal/top-up ngăn auto/manual replay. Amount/account/reference/TTL mismatches review.
4. Credit prices được operator cấu hình riêng, không quy đổi price_stars. Orders/payment có
   immutable method; wallet order không cần invoice row, Stars charge hoặc pre-checkout.
   payments trở thành disjoint order-payment union: Stars giữ receipt fields; wallet có
   ledger journal, CRD amount và NULL external receipt fields. Không tạo PaymentReceipt giả.
5. Wallet checkout: access/identity → buyer → product → order/unit → wallet accounts theo ID;
   kiểm tra price/stock/balance, debit + PAID + allocation/resource + READY work + audit atomic.
   Worker T09/T10 gửi sau commit, giữ fencing, UNCERTAIN và cùng entitlement khi resend.
6. Credit refund là compensating journal, serialize với order/work/resend; in-flight/uncertain
   send bị từ chối để reconcile. Không restock key. Top-up reversal là nghiệp vụ riêng; không
   có bank/crypto outgoing request trong adapter. Cash refund có append-only operator evidence
   REQUESTED/UNCERTAIN/CONFIRMED/REJECTED; positive source attestation + prior top-up reversal
   mới ghi CONFIRMED, không coi đây là independently verified external payout.
7. SePay Sandbox: raw timestamp.body HMAC, bounded freshness/body, immutable event digest,
   canonical bank reference dedup; reconciliation API qua cùng intake, network ngoài UoW.
8. Binance Pay Merchant unavailable cho Việt Nam theo nguồn dưới. Exchange deposit là API
   signed read-only khác Pay; on-chain cần provider/finality/token contract riêng. Không gán
   shared-address deposit theo amount; thiếu buyer binding hoặc finality → review. Sandbox
   fixtures ghi rõ simulated, không gọi đó là nạp USDT thật vào Binance. Không chọn network thật
   hoặc địa chỉ thật thay Owner. Quote dùng Decimal/rate snapshot/TTL và explicit rounding.
9. Stars default paused ở runtime UI/new checkout; receipt/refund intake lịch sử còn hoạt động.
   Payment V2 có flags/gates riêng, không dùng kết quả PV2 để tick T14 E3/E4.

## Nguồn và giới hạn đã xác minh ngày24/10/2026

- [Telegram digital payments](https://core.telegram.org/bots/payments-stars#faq): hàng số trong
  bot/mini app yêu cầu Stars. Không tuyên bố credit Bank/USDT checkout production được Telegram
  cho phép; không dùng hạn chế này để chặn triển khai sandbox Owner yêu cầu. Web bán hàng độc lập
  là ranh giới kênh khác, cần review riêng trước go-live.
- [SePay Test Mode](https://developer.sepay.vn/vi/tien-ich-khac/test-mode): credentials/data sandbox
  riêng; [HMAC](https://developer.sepay.vn/vi/sepay-webhooks/xac-thuc) ký timestamp + raw body.
- [Binance Pay restrictions](https://www.binance.com/en/support/faq/detail/942c5c12e00b494e9bc3e2ab31f7da19):
  Vietnam trong unsupported countries; không xin merchant credentials giả.
- [Exchange deposit API](https://developers.binance.com/en/docs/catalog/core-trading-wallet/api/rest-api/capital):
  signed GET /sapi/v1/capital/deposit/hisrec; không phải merchant webhook hoặc proof token contract
  trên chain. Account/network/token binding và independently verified settlement là release gates.
- [Chính phủ: khung tài sản mã hóa](https://xaydungchinhsach.chinhphu.vn/tai-san-ma-hoa.html): có
  khung thí điểm/điều kiện riêng; closed-loop bookkeeping không tự chứng minh được phép cung cấp
  dịch vụ ví/thanh toán/crypto. Owner cần review pháp lý/tài khoản/chính sách trước tiền thật.

## Alternatives và consequences

Không giả Telegram receipt, không fork fulfillment worker, không xóa Stars constraints để
né tests, không dùng float hoặc mutable balance làm source of truth. Additive migrations
0015+ giữ dữ liệu và predecessor triggers; downgrade từ chối khi có history PV2. Sandbox
implementation và provider-hosted E2E/production acceptance là bằng chứng khác nhau.

Wallet permissions tách legacy Stars permission enum/grants, không tự cấp quyền tài chính khi
nâng schema hoặc dựa trên role ADMIN. Migrations0016/0018 tạo disjoint triggers/guards cho wallet;
original Stars function texts giữ nguyên. Current head0020 thêm manual external-refund evidence.
