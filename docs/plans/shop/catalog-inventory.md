# Spec: catalog-inventory

Status: design baseline; chưa triển khai. Nguồn: `task.md` §§3,4,8,10. Depends on: [identity-access](identity-access.md).

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

## Acceptance criteria

- **CI-01:** Catalog/search/pagination trả dữ liệu thật, không lộ secrets; hết hàng/inactive không cho tạo order hợp lệ.
- **CI-02:** Hai khách reserve key cuối cùng bằng hai DB connections không cùng giữ một unit; không oversell khi rollback/retry.
- **CI-03:** Duplicate/invalid/oversize import và import đồng thời được xử lý atomic; lỗi/logs không lộ nội dung inventory.
- **CI-04:** Cả năm type có schema/validator hợp lệ; wrong-type payload bị từ chối, không được cast tất cả thành text.
- **CI-05:** Expiry chạy cạnh PAID không release allocation; retire không xóa mất entitlement lịch sử.
- **CI-06:** Tamper ciphertext/sai AAD/sai key bị từ chối; permitted entitlement mới được decrypt; low-stock alert không dùng secret payload.

Verification: unit payload/validation/UX tests; PostgreSQL thật cho allocation/expiry/import races và constraints; mocked Telegram cho admin/catalog contracts. Các tests này chưa tồn tại ở bước thiết kế.
