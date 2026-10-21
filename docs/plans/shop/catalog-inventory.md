# Spec: catalog-inventory

Status: T04 public catalog/admin, T05 encrypted finite inventory và T06 finite reservation/expiry đã triển khai; public checkout/payment/fulfillment và nonfinite secret payload tiếp tục theo T07–T10. Chưa nghiệm thu toàn CI-01..06 hoặc live Telegram. Nguồn: `task.md` §§3,4,8,10. Depends on: [identity-access](identity-access.md).

## Catalog và product contracts

Category/subcategory, product/SKU, ảnh/description, giá Stars, trạng thái bán và availability được đọc từ PostgreSQL; hỗ trợ search và inline pagination có giới hạn. Không expose inventory/delivery payload ở public read models, search results, callbacks hoặc preview.

SKU identity phải ổn định, unique; đổi tên hiển thị không đổi phạm vi duplicate checking. Price là positive integer. Sửa giá/sale status/inventory phải đi qua use case có permission và audit, không update trực tiếp từ admin router. UI mặc định Việt, Back/Home/Cancel; dùng edit message khi phù hợp và escape nội dung không đáng tin khi render.

| Product type | Payload/validation riêng | Availability và fulfillment |
| --- | --- | --- |
| `UNIQUE_KEY` | Một key bí mật cho một inventory unit | Finite stock; reserve rồi allocate đúng unit cho một order item. |
| `LICENSE` | License key cùng metadata hướng dẫn áp dụng | Finite stock; chia sẻ cơ chế allocation với UNIQUE_KEY, vẫn giữ type/validator riêng. |
| `DIGITAL_FILE` | Telegram `file_id`, tên/mime và metadata được kiểm soát | Reusable file reference, hiển thị có sẵn khi active/valid; gửi document, không đưa URL có BOT_TOKEN cho khách. |
| `REUSABLE_RESOURCE` | Typed resource/content reference được phép phân phối nhiều lần | Availability chủ động, entitlement theo order; payload nhạy cảm được mã hóa. |
| `MANUAL_FULFILLMENT` | Hướng dẫn/metadata công việc của staff, không giả key | Admin bật availability; khách thấy rõ cần xử lý thủ công trước khi thanh toán. |

Không dùng một chuỗi text cho tất cả product types. Dùng typed payload/validator và dispatch đơn giản theo enum; không tạo strategy/factory framework. Digital file/reusable/manual không hiển thị một số tồn kho giả; quota hữu hạn cho chúng chỉ thêm khi có requirement cụ thể.

T04 public schemas riêng: UNIQUE_KEY `{}`; LICENSE `instructions`; DIGITAL_FILE
`file_name`/`mime_type`; REUSABLE_RESOURCE `format=TEXT|DOCUMENT`; MANUAL_FULFILLMENT
`customer_notice`. Đây là metadata công khai, không phải delivery payload trong bảng trên.
T05 có typed UNIQUE_KEY/LICENSE secret payload/validation/encryption riêng trong inventory,
không đưa key/file_id/content vào products. Nonfinite secret payload thuộc T10.
Photo reference chỉ dành cho ảnh minh họa công khai.

Contract hiện tại: root/subcategory hai cấp, immutable case-sensitive code/SKU/type;
new product hidden, Owner-delegated `catalog.manage` + confirmation/audit cho writes.
CatalogService cung cấp public categories/products/product và protected admin/prepare/
confirm/cancel use cases. Pagination 5 items/page (0–9999), search literal 1–80 ký tự;
private router giữ actor-bound bounded navigation parameters, không cache business data.
Stock/low-stock chỉ đọc qua protected InventoryService; OrderService là internal T06 use case,
chưa có order transport API hoặc nút mua.
Chi tiết command/schema/limits tại [README](../../../README.md), [DATABASE](../../DATABASE.md)
và [SECURITY](../../SECURITY.md); CI-01 finite stock/order gating có ở T06, nonfinite còn T10.

## Inventory và reservation

`inventory_units` thuộc SKU, có encrypted payload, key version/nonce, HMAC duplicate fingerprint theo [ADR-0003](../../adr/0003-inventory-secrets.md), trạng thái `AVAILABLE`, `RESERVED`, `ALLOCATED`, `RETIRED`. Unit reserved gắn order item và expiry; unit allocated giữ vĩnh viễn quan hệ issuance với order item, kể cả delivery lỗi/refund.

- DB constraint chống fingerprint trùng trong SKU và unit/order-item allocation trùng; khóa row + conditional updates khi reserve/allocate/release.
- Chỉ release reservation trước payment theo expiry/cancel flow đã lock order. Worker expiry và payment handler phải serialize cùng order; không thấy AWAITING_PAYMENT từ một read cũ rồi release sau PAID.
- Chọn unit bằng transaction, không tính `count > 0` rồi lấy key ngoài transaction. Contention/lock timeout phân biệt với hết hàng, bounded retry khi chưa có external effect.
- Paid allocation không được đưa lại AVAILABLE tự động. Unit đã được reference không hard-delete; retire/audit theo quyền.
- Low-stock signal dựa vào committed availability, không gửi hàng hoặc tạo invoice thay cho cảnh báo.

## Import contract

CSV/TXT từ staff có quyền, bounded input (baseline tối đa 1 MiB và 1.000 rows/batch), parse/validate toàn batch trước khi commit. Row lỗi/trùng làm batch rollback; thông báo row number/error code, không echo key. Không tự casefold/Unicode-normalize key material. Không lưu plaintext temporary file, không log upload. Limit là cấu hình được document, không bỏ giới hạn để xử lý import lớn; lớn hơn cần thiết kế batch job riêng.

Unique constraint bắt cả hai import đồng thời vượt qua application precheck. Mọi import/sửa kho/giá có audit metadata, không có plaintext key. Encryption errors fail closed, không fallback lưu plaintext.

T05 inventory contract: InventoryService prepare_import/prepare_retire/confirm/cancel/stock/
low_stock; current inventory.manage trong UoW và handler. Confirmation actor/target-bound,
10 phút; pending import chỉ giữ sealed envelopes, chưa AVAILABLE; apply/cancel clear
envelopes, applied không thể cancel để undo. Unit identity/material immutable, không delete;
retire chỉ AVAILABLE→RETIRED. T06 có RESERVED với order item FK/expiry; stock trả cả reserved
count, reserved unit không retire. ALLOCATED và payment-vs-expiry thuộc T08.
CSV header chính xác `key` hoặc `license_key` theo type; TXT một key mỗi dòng; UTF-8/BOM,
LF/CRLF, key ≤4096 UTF-8 bytes, không control/blank, không trim/normalize material. Toàn batch
parse trước commit; error chỉ row/code. Download capped memory-only qua SDK, không temp file.
Crypto guard pin HMAC verifiers ngoài payload: dedup/version-key changes hoặc thiếu old
version fail closed, keys không nằm trong DB. Low-stock chỉ active finite SKU có committed
AVAILABLE ≤ LOW_STOCK_THRESHOLD (mặc định 5); query/flag chưa tự dispatch notification.
Không có decrypt/export use case trước authorized entitlement; old-version/tamper primitive
được test tại crypto adapter. README/DATABASE/SECURITY sở hữu command/config/evidence chi tiết.

## Acceptance criteria

- **CI-01:** Catalog/search/pagination trả dữ liệu thật, không lộ secrets; hết hàng/inactive không cho tạo order hợp lệ.
- **CI-02:** Hai khách reserve key cuối cùng bằng hai DB connections không cùng giữ một unit; không oversell khi rollback/retry.
- **CI-03:** Duplicate/invalid/oversize import và import đồng thời được xử lý atomic; lỗi/logs không lộ nội dung inventory.
- **CI-04:** Cả năm type có schema/validator hợp lệ; wrong-type payload bị từ chối, không được cast tất cả thành text.
- **CI-05:** Expiry chạy cạnh PAID không release allocation; retire không xóa mất entitlement lịch sử.
- **CI-06:** Tamper ciphertext/sai AAD/sai key bị từ chối; permitted entitlement mới được decrypt; low-stock alert không dùng secret payload.

Verification T04–T06: unit metadata/key/license/parser/AEAD/settings/UX; PostgreSQL thật cho
import/retire races, constraints và rollback; mocked Telegram dispatcher với DB thật, download/
send ngoài transaction, finite last-key/double-click/expiry/cancel races và snapshot rollback.
Allocation/payment-vs-stock và entitlement decryption còn T08/T09; tests hiện tại không
thay thế các gates đó. Evidence tại implementation plan.
