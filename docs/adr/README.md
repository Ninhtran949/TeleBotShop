# Architecture decision records

Các quyết định dưới là baseline kỹ thuật do SE/SA chọn theo yêu cầu Owner ngày 2026-10-21. `Accepted` nghĩa là đã chọn làm chuẩn thiết kế; không phải đã triển khai, đã test hoặc đã được phép tác động production.

| ADR | Quyết định | Trạng thái |
| --- | --- | --- |
| [0001](0001-modular-monolith.md) | Modular monolith, import boundaries, composition root | Accepted; chưa triển khai |
| [0002](0002-payment-and-fulfillment-safety.md) | Transactional allocation + durable fulfillment, xử lý uncertain | Accepted; chưa triển khai |
| [0003](0003-inventory-secrets.md) | Inventory encryption, duplicate fingerprints và key ownership | Accepted; chưa triển khai |
| [0004](0004-toolchain-and-quality-gates.md) | Python/toolchain/lock và quality gates | Accepted; chưa triển khai |

ADR mới dùng số tiếp theo, gồm status, context, decision, alternatives, consequences, evidence. Giữ lịch sử; thay đổi material phải thêm ADR superseding và cập nhật link/status ADR cũ, không âm thầm viết lại quyết định. Sửa typo không cần ADR mới. Quy tắc kỹ thuật chi tiết ở ARCHITECTURE/CONVENTIONS và capability specs; ADR lưu lý do, không copy các tài liệu đó.
