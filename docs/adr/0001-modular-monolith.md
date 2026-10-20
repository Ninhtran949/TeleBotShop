# ADR-0001: Modular monolith và Clean Architecture tối thiểu

- Status: Accepted, design only; chưa triển khai.
- Authority: Owner giao SE/SA tự thiết kế baseline; không bao gồm thực thi ứng dụng hoặc production.

## Context

`task.md` yêu cầu bot bán hàng, Stars, kho, delivery và admin; ưu tiên Python/aiogram/FastAPI/PostgreSQL và modular monolith. Workspace chưa có app để phải giữ một framework/layout cũ. Các nghiệp vụ tiền/kho cần cùng transaction, còn SDK/DB phải thay đổi được mà không kéo vào domain.

## Decision

Dùng package `src/telebotshop` theo cây và import matrix ở [ARCHITECTURE](../ARCHITECTURE.md). Business models thuần Python, use cases sở hữu transaction qua ports; adapters thực hiện persistence/Telegram/security. `bootstrap.py` là composition root duy nhất. Admin là transport của các use cases nghiệp vụ, không phải layer được phép bỏ qua authorization.

Một codebase/image; ingress và worker chạy riêng process với cùng contract. Initial ingress một replica, không Redis/FSM phân tán cho tới khi thật sự cần. Chỉ thêm Protocol ở external boundary cần fake hoặc tách implementation; không generic CRUD/base-service/DI framework.

## Alternatives

- Handler gọi SQLAlchemy trực tiếp: ít dòng lúc đầu nhưng trộn quyền, transaction và Telegram; khó test failure/race độc lập.
- Microservices/message broker: tách deployment nhưng tăng distributed transaction và vận hành chưa cần.
- Clean Architecture với interface/entity/mapper cho mọi thứ: nhiều lớp không tạo thêm boundary có ích.

## Consequences và kiểm chứng

Có một số mapping ORM/read DTO và wiring thủ công, đổi lại domain/use cases không phụ thuộc SDK. T01/T02 thêm import checks; mỗi slice chứng minh authorization và invariant qua application entrypoint, không chỉ Telegram router. Các port/adapter cụ thể được viết khi slice cần, không scaffold toàn cây.
