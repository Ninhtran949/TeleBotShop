# Spec: orders-payments

Status: T06–T13 đã triển khai local order/invoice, serialized approval, durable receipts/paid entitlement, typed delivery và privileged full refund/reconciliation. Polling có durable financial intake; public Buy/core UI và Telegram test acceptance còn T14. Chưa nghiệm thu toàn OP hoặc live Stars. Nguồn: `task.md` §§3,5,8,11. Depends on: [identity-access](identity-access.md), [catalog-inventory](catalog-inventory.md). Quyết định an toàn: [ADR-0002](../../adr/0002-payment-and-fulfillment-safety.md).

## Order và invoice

Baseline checkout một active product, quantity 1 và một order item; không giỏ hàng/subscription/multi-item. `orders`/`order_items` lưu buyer, product/type/title và integer Stars price snapshot tại tạo order. Client truyền reference/action nonce, không truyền authority về giá, permission hoặc payment state.

`create_order` idempotent theo buyer + checkout action key; cùng action không tạo hai order/reservation/invoice intents. Lock/reserve unit cho finite stock, ghi AWAITING_PAYMENT + invoice intent trước external call; Telegram invoice gửi ngoài transaction. Failure/timeout của invoice không được ghi PAID hay bỏ receipt đến sau. Trạng thái invoice/attempt giúp phân biệt cần xử lý và không vô tình gửi lại cùng action.

Reservation TTL 15 phút theo DB time; pre-checkout approval gia hạn tới ít nhất 15 phút từ approval. Khách cancel/expire trước payment có thể release qua guarded transaction. Giá catalog đổi sau invoice không đổi snapshot; reprice phải tạo order/action mới, không tự tính lại số tiền callback.

T06 thực hiện UNIQUE_KEY/LICENSE, quantity 1; T10 mở ba loại nonfinite. `0006_orders`
ghi trực tiếp AWAITING_PAYMENT trong atomic create, chưa cần persisted PENDING state.
Invoice READY→SENDING có durable attempt trước network; SENT không mark PAID, known
REJECTED close unpaid order, UNCERTAIN không auto-resend. `expire-orders` xử lý tối đa
100 due orders và 100 SENDING claims quá 30s mỗi lần; không scheduler tự chạy.
Đơn/payload history không xóa sau expiry để receipt T08 còn đối chiếu được. Test Telegram
mock kiểm tra request XTR/one price/empty provider token/nonempty start parameter theo
[sendInvoice](https://core.telegram.org/bots/api#sendinvoice); forwarded invoice phải mở
lại bot thay vì giữ Pay button. Pre-checkout vẫn phải kiểm tra buyer, không dựa vào tham số đó.
T06 chưa wire Buy/invoice vào polling trước khi T07/T08 có intake an toàn.

## Pre-checkout contract

Check order ownership, active product, snapshot amount, `XTR`, invoice payload/order reference, order/payment state và reservation còn thuộc order. Dùng DB timeout/budget ngắn; trả `answerPreCheckoutQuery` kịp giới hạn Telegram **10 giây**, không chờ worker hoặc lock không giới hạn. Reject có thông báo Việt, không expose internals.

Serialize approval theo order: persist pre-checkout query ID đã được chấp thuận; cùng query ID được xử lý idempotent, query khác cho order đã approved bị reject. Không tự reset approval để approve invoice thứ hai khi chưa biết payment outcome; khách muốn checkout mới phải có action/order mới. Cách này giảm double-charge từ hai invoice cùng order; extra receipt vẫn phải được lưu nếu xảy ra.

Approval chỉ cho phép tiếp tục thanh toán, không chứng minh đã trả tiền. Invoice dành cho buyer của order; forwarded invoice/different user không được dùng để nhận entitlement. Stars invoice dùng currency XTR và một price component; optional provider token bỏ trống/không dùng third-party provider cho flow này.

T07: `0007_pre_checkout` lưu một immutable approval/order và unique query ID; gia hạn
order/exact reserved unit/audit atomic, DB time sau order→product→unit locks. Cùng query
không kéo dài TTL/audit và vẫn kiểm tra current validity; khác query bị từ chối. Invoice
SENDING/SENT/UNCERTAIN được xét theo owner/price/reservation, READY/REJECTED không được.
Known invoice rejection sau approval giữ UNCERTAIN thay vì release. Cancel/expiry giữ
approval history; replay của order expired/cancelled không approve lại.
Adapter có 4s DB/validation + 3s API answer budget theo
[answerPreCheckoutQuery](https://core.telegram.org/bots/api#answerprecheckoutquery), không
gọi network trong transaction; timeout/lost answer không reset binding hoặc blind retry.

T10 nonfinite create khóa buyer→product, yêu cầu ACTIVE resource đúng metadata và ghi
order_resources cùng order/item/invoice/audit. Không có finite unit, reservation hoặc
allocation giả. Pre-checkout kiểm tra immutable source binding thay exact unit, giữ
buyer/XTR/giá/TTL/current active product và query-id idempotency; gia hạn order TTL cùng
approval/audit. Cancel/expiry giữ source binding để receipt đến muộn còn được đối chiếu.
Payment hợp lệ lưu PAID + primary receipt + bound resource work atomic; nguồn ACTIVE/RETIRED
giữ entitlement cũ, không đổi sang nguồn mới khi catalog/resource được sửa. Nếu source không
còn hợp lệ thì giữ PAID + ENTITLEMENT_UNAVAILABLE để reconcile, không làm mất receipt.
Query ID cap 256 ký tự là input policy dự án, không phải giới hạn Telegram công bố.
T08 nối checker thật sau khi đăng ký durable receipt; Buy/core UI còn T14.

## Payment receipt và atomic transition

Chỉ authenticated `successful_payment` hợp lệ xác nhận incoming payment; không dùng screenshot, button click hay pre-checkout approval. Dedup bằng unique Telegram charge ID; update ID chỉ hỗ trợ chống lặp transport, không thay business idempotency.

Trong UoW:

1. Lock charge trước, rồi order nếu match được; đọc current state và đối chiếu buyer/amount/currency/payload snapshot từ server.
2. Persist receipt tối thiểu. Nếu charge đã có, so sánh identity/content: cùng event là no-op; conflicting replay tạo security/audit attention, không ghi đè receipt hoặc credit order khác.
3. Receipt khớp buyer/amount/currency/payload và order chưa có primary matched payment: lưu MATCHED và chuyển PAID. Nếu exact reservation vẫn thuộc order thì đổi đúng unit thành allocation và tạo work duy nhất trong cùng commit; TTL tự nó không release key. Nếu guarded expiry/cancel đã làm mất reservation thì vẫn MATCHED + PAID nhưng có `attention_reason`, không READY work hoặc lấy unit đã cấp cho người khác.
4. Unknown order, extra charge cho order đã có primary payment hoặc amount/user/payload không khớp: giữ durable receipt với `REQUIRES_REVIEW`, không credit/giao hàng cho một order đoán được. Tách payment matching khỏi tình trạng stock: stock lỗi không được làm mất primary payment của order đã xác minh.
5. Chỉ acknowledge webhook sau durable receipt/business outcome. DB failure trước commit trả non-2xx. Telegram update có thể gửi lại, không giả định retry là vô hạn hoặc bảo đảm đã lưu khi chưa commit.

`payments` là receipt ledger: charge ID unique toàn hệ thống, nullable order reference cho unmatched receipt; chỉ **một matched payment cho mỗi order** bằng conditional uniqueness, không cấm ghi extra receipt cần review. Không đặt UNIQUE(order_id) trên mọi receipt khiến mất thông tin charge thứ hai.

Paid-attention không có entitlement hợp lệ thì không tạo READY work giả. Chỉ reconciliation có quyền mới có thể hoàn tất allocation/work hoặc thực hiện refund theo receipt; mọi quyết định giữ audit và không overwrite allocation của order khác.

T08: immutable receipt/conflict tables, conditional primary/order và unique item/work.
Missing payer được giữ nullable để đối soát, không tạo fake user. Unsupported recurring hoặc
shipping/order-info flow được giữ review; chỉ giữ flag, không raw PII. Provider reference là
evidence được lưu, không giả định phải empty để credit XTR. PAID + allocation/work/audit
atomic, deferred PostgreSQL constraint xác nhận shape ở commit. Financial ledger tự audit
event chưa có actor; general order audit vẫn dùng buyer thật.

Native aiogram polling tăng getUpdates offset sau yield; T08 wrapper persist receipt trước
yield, lỗi DB không confirm. Router dùng cùng parser/service, duplicate handler không allocate
lại. ASGI test harness await dispatcher chứng minh 500 khi commit fail và replay tạo đúng một
work; production webhook/auth/body cap còn T14/T18, không gọi harness là live webhook.
Sources: [SuccessfulPayment](https://core.telegram.org/bots/api#successfulpayment),
[getUpdates confirmation](https://core.telegram.org/bots/api#getupdates),
[webhook retry](https://core.telegram.org/bots/api#setwebhook), đối chiếu 2026-10-21 với SDK đã pin.

## State machine

Payment/refund ledger là nguồn sự thật tài chính; order status là tiến độ fulfillment, không phải số dư tiền.

| Current → next | Điều kiện |
| --- | --- |
| PENDING → AWAITING_PAYMENT | Snapshot/reservation và invoice intent đã lưu. |
| PENDING/AWAITING_PAYMENT → EXPIRED | Chưa có payment; guarded expiry/cancel, release chỉ reservation của order. |
| AWAITING_PAYMENT → PAID | Successful payment matched, atomic allocation/work hoặc paid-attention nếu không thể giữ stock. |
| EXPIRED → PAID + attention | Late payment xác minh được đúng order; không tự bán lại unit đã mất. |
| PAID → FULFILLING → DELIVERED | Có allocation/work; send thành công đã được persist hoặc manual completion được audit. |
| FULFILLING → FAILED | Known delivery failure hết retry budget; payment/allocation vẫn giữ, cần xử lý. |
| FAILED → FULFILLING | Retry được phép, cùng entitlement, không tạo order/payment/allocation mới. |
| Paid lifecycle → REFUNDED | Refund confirmed theo contract dưới; không tự restock hoặc xóa receipt. |

Uncertain delivery/refund là state/attention trên work/refund record, không chuyển order về unpaid. Unknown/extra receipt không làm lùi order DELIVERED/REFUNDED hoặc tái giao. Mọi transition khác phải bị từ chối hoặc explicit no-op với event lặp.

## Published fulfillment contract

Sau commit, worker có thể đọc work gồm `work_id`, `order_id`, `order_item_id`, buyer/destination đã xác minh, product type snapshot, allocation/resource reference, initial state READY. Không đặt plaintext key/raw Update vào work payload. Unique `order_item_id` bảo vệ một entitlement/work; các attempt là records khác, không tạo entitlement mới.

T09 worker tiêu thụ cùng `CommittedWork` và thêm fenced attempts/observations; finite delivery
không gọi ngược payment handler hoặc cấp key mới. Failed delivery vẫn giữ ledger và allocation.
Đây là DB/domain contract mà [fulfillment](fulfillment.md) tiêu thụ; payment use case không import hoặc gọi worker. `fulfillments` và delivery attempts được xử lý qua application ports trong cùng domain model, không copy ORM/contract sang module thứ hai.

## Refund

Chỉ full refund của verified received charge; Bot API hiện không nhận amount cho `refundStarPayment`. Có thể là primary matched charge hoặc extra/unmatched receipt đã được authorized reconciliation xác minh payer/charge. Persist refund intent/action dedup + actor/audit trước gọi API. Verify current permission và policy, serialize với order/delivery. Không tự gửi refund của primary payment khi send đang in-flight/uncertain; cần resolve attempt hoặc explicit manual decision có audit.

Confirmed API result hoặc authenticated refund event khớp charge mới mark refund SUCCEEDED. Chỉ refund primary charge mới chuyển order REFUNDED; hoàn extra charge không revoke order/entitlement đã thanh toán hợp lệ bằng charge khác. Timeout/unknown outcome → UNCERTAIN, giữ record và dừng automatic retry; reconcile từ Telegram transaction evidence trước quyết định tiếp. Repeated button/update không tạo hai refund intents đang chạy hoặc refund lại charge đã hoàn. Key từng allocated không tự restock; hàng đã giao không thể được giả định thu hồi bằng việc đổi DB state.

T13 local: `payment_verifications`, immutable `refund_receipts` và `refund_intents` có
current `payments.refund` (OWNER hoặc explicit ADMIN grant), không dùng `orders.read`
thay quyền tài chính. Primary MATCHED receipt là verified; extra/unmatched phải có positive
incoming transaction evidence khớp charge/amount/payload/payer đã biết. Chỉ evidence này mới
bổ sung payer còn thiếu; không sửa canonical receipt hoặc nhận payer từ staff input.

Prepare lưu encrypted decision reference ≤500 UTF-8 bytes với payment/action/version AAD,
actor/payment/exact order-work-resend snapshot và TTL 10 phút. Confirm giữ access→charge→
order→work→active resend→intent locks, recheck grant/state/expiry trước decrypt; một active
hoặc succeeded intent/payment. Begin commit SENDING + owned hold + audit trước API; không
giữ DB session qua network. API timeout 10s, send window 60s; chỉ true là success, known
rate-limit rejection là FAILED, mọi kết quả chưa chắc accepted là UNCERTAIN. Không auto
retry; known failure chỉ nhả hold của chính intent và muốn thử lại phải tạo action mới.

Delivery/resend SENDING còn lease chặn cả explicit override. Expired SENDING được chuyển
UNCERTAIN; `/refund_uncertain` cần decision reference, warning và confirmation riêng để
hoàn tiền dù có thể đã giao hàng. Không clear delivery uncertainty, sửa attempts, mở resend
hoặc lấy key khác. Primary success đổi REFUNDED, giữ entitlement/allocation/history;
extra success không đổi order. Late API success được ghi nhận; late failure không xóa
uncertainty hoặc success. Expired refund SENDING chỉ normalize uncertainty, không resubmit.

`/verify_charge` và `/reconcile_refund` đọc tối đa 100 transactions/trang, offset bounded;
direction incoming/outgoing và identity đầy đủ mới là positive proof. Không thấy record
trên một trang không chứng minh chưa refund và không mở retry. Verify/reconcile lưu actual
staff actor + redacted audit atomic với thay đổi; system event giữ provenance trong receipt.
Authenticated `refunded_payment` lưu/dedup trước polling offset, kể cả unknown charge,
missing profile/group hoặc tới trước successful payment. Wrong currency/amount/payload/
provider được giữ nhưng không apply. Refund đã lưu được apply trong cùng payment UoW:
primary REFUNDED + held work atomic, không mở cửa cho worker. Nếu message đã in-flight,
event không thu hồi được nó; completion đến sau chỉ lưu observation, không đổi DELIVERED.

Native PostgreSQL coverage gồm đủ năm product types, late paid-attention không work,
primary/extra/before-payment receipts, rollback/lost ACK, actor/grant/expiry/stale action,
hai physical connections cho refund-vs-worker/resend/manual và duplicate confirm. Migration
0013 giữ nguyên predecessor data/functions, từ chối downgrade khi có refund history.
Đây là local/mock evidence; external Stars refund vẫn là gate T14, không tự đặt business
refund window hoặc tuyên bố đã thu hồi tài nguyên sau refund.

## Acceptance criteria

- **OP-01:** Double-click cùng action tạo một order/reservation; sửa callback price/user/product không tạo thanh toán sai.
- **OP-02:** Price change sau invoice vẫn theo snapshot; inactive/out-of-stock/expired/wrong-currency/user bị reject ở pre-checkout; answer trong deadline với DB contention có giới hạn.
- **OP-03:** Duplicate charge/update đồng thời chỉ credit, allocate và tạo work một lần; conflicting receipt không overwrite.
- **OP-04:** DB failure/rollback trước receipt commit không acknowledge thành công; retry sau commit không gọi delivery trực tiếp.
- **OP-05:** Payment sau expiry, unknown order hoặc wrong amount không bị mất; có durable review/attention và không cấp nhầm key.
- **OP-06:** Refund authorization/replay/timeout/refund-vs-delivery race giữ đúng ledger, không tự retry ambiguous hoặc restock paid key; refund extra charge không hủy primary paid entitlement.
- **OP-07:** State transitions qua application entrypoints được test; không suy paid/unpaid chỉ từ FAILED/EXPIRED status của một read cũ.

Verification: unit/state/contract tests với Telegram mocked; PostgreSQL thật cho duplicate/last-key/expiry/refund races; external smoke riêng trong Telegram test environment. Không có live/test payment nào đã chạy ở bước thiết kế.

Nguồn kiểm tra 2026-10-21: [Stars flow, deadline và test environment](https://core.telegram.org/bots/payments-stars), [Bot API invoice](https://core.telegram.org/bots/api#sendinvoice), [successful payment](https://core.telegram.org/bots/api#successfulpayment), [refund](https://core.telegram.org/bots/api#refundstarpayment), [transaction evidence](https://core.telegram.org/bots/api#getstartransactions). Kiểm tra lại khi triển khai; design TTL/quantity là policy dự án.
