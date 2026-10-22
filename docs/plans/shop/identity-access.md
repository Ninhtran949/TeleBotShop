# Spec: identity-access

Status: T03 identity/permission foundation, T11 history/resend, T12 scoped support và T13 privileged refund đã triển khai local; Telegram external acceptance thuộc T14. Nguồn: `task.md` §§3,6,9,10. Depends on: không. Scope và coding/commands theo [index](README.md), [ARCHITECTURE](../../ARCHITECTURE.md), [CONVENTIONS](../../CONVENTIONS.md).

## Contract

- Actor là Telegram user ID nhận từ authenticated Update, không phải username, callback-supplied user ID hoặc một header do client tự đặt.
- `/start` idempotently tạo/lấy profile; unique Telegram ID, optional username/display name, language, registration timestamps. Tiếng Việt là locale mặc định, không tạo password/login endpoint.
- Profile lookup/permission checks là application contracts; bot/admin/API không tự quyết định business authorization từ menu hoặc role flag gửi từ client.
- Owner đầu tiên bootstrap từ OWNER_ID khi chưa có owner. Restart không tự cấp lại quyền đã revoke hoặc grant thêm owner khi environment đổi; mismatch cần controlled ownership transfer, không tự sửa DB.
- Permissions là tập enum/quyền cụ thể, default deny. Không triển khai generic policy engine. Mutation kiểm tra quyền hiện tại trong use case; actor context chỉ mang identity, không mặc định tin cached roles.

## Vai trò mặc định

| Hành động | OWNER | ADMIN | SUPPORT |
| --- | --- | --- | --- |
| Quản lý role/permission, operational settings | Có | Không | Không |
| Catalog/giá và import/retire inventory | Có | Chỉ quyền được Owner cấp | Không |
| Tra cứu order và metadata phục vụ công việc | Có | Theo quyền | Theo ticket/phạm vi được cấp |
| Refund | Có, theo state/policy | Chỉ explicit refund permission | Không |
| Support tickets | Có | Theo quyền | Ticket được phân công/phạm vi được cấp |
| Bulk xem/export inventory plaintext | Không có tính năng mặc định | Không | Không |

Khách hàng chỉ xem entitlement/order/ticket của mình. Staff role không tự động cho phép mở plaintext inventory hoặc bỏ qua order ownership; privileged access phải có use case/quyền riêng và audit, không tận dụng endpoint khách để impersonate.

## Dữ liệu và API nội bộ tối thiểu

`users`, `roles`, `permissions` và quan hệ user-role/role-permission. Telegram ID là BIGINT, duy nhất; username thay đổi không tạo account mới. Contracts tối thiểu: `get_or_create_customer`, `authorize(actor_id, permission, resource_scope)`, `grant_or_revoke_permission` có audit. Tên/signature cuối cùng được chốt cùng slice, không thêm API tổng quát trước nhu cầu.

T03 dùng `IdentityService.get_or_create_customer/get_my_profile/bootstrap_owner`;
`AccessService.authorize/admin_access/settings/prepare_permission/prepare_support/confirm/cancel`.
Explicit grants ở `user_permissions` dưới ceilings của `role_permissions`; normal customer không
có staff role. Mutations gọi `require_permission` trong cùng UoW, confirmation có actor/target/
version/expiry và audit. Chi tiết schema và giới hạn hiện tại ở [DATABASE](../../DATABASE.md),
[SECURITY](../../SECURITY.md); không suy ra order/ticket integration hoặc live acceptance từ matrix tests.

Ứng dụng chỉ nhận customer flows nhạy cảm trong private chat; group callback không được dùng làm delivery destination. `/admin` có thể từ chối sớm ở router để UX tốt, nhưng service vẫn phải từ chối direct unauthorized call.

T12 SupportService xác thực customer ownership hoặc current tickets.manage trong cùng UoW.
SUPPORT cần actual persisted assignee, không nhận scope từ callback hoặc tự assign. OWNER/
ADMIN có grant mới assign/reassign tới người có current ticket grant; confirm lại actor,
mode, ticket version và TTL. Scope check trước ciphertext/decrypt; linked order chỉ public
context sau authorized ticket, không mở orders.read hoặc quyền xem delivery payload.
Sequential revoke/reassign tests và native close/reply/duplicate races giữ failure boundaries.

## Acceptance criteria

T11 history/detail: customer chỉ own buyer, staff current orders.read; plaintext không nằm
trong metadata projection. Staff resend cần thêm fulfillment.manage và actor-bound action,
customer chỉ resend về private buyer đã lưu. Confirmation recheck grant/state/hold trước
shared authorized entitlement reader/decrypt. Hai native connections, IDOR/callback replay,
revoked grant và mock Telegram contracts có coverage; native refund ledger thuộc T13,
live gates vẫn riêng.

T13 mọi queue/detail/prepare/begin/complete/cancel/verify/reconcile refund kiểm tra current
`payments.refund` trong UoW; OWNER hoặc ADMIN explicit grant, SUPPORT bị role ceiling chặn.
`orders.read` không cấp quyền refund; refund metadata không expose delivery payload. Action
gắn actual actor/payment/state/expiry, không confirm hộ bằng Owner hoặc staff khác. Giữ
access lock tới commit; verify/reconcile qua network phải reauthorize khi persist. Staff
audit ghi đúng người thực hiện, không thay bằng actor đã tạo intent. Authenticated system
financial receipts là nguồn riêng, giữ provenance và không invent staff actor. Native tests
phủ direct deny, wrong actor, revoke-before-decrypt/during lookup, expiry/cancel/stale,
atomic audit failure và financial replay; không coi mock Update là production auth proof.

- **IA-01:** Hai `/start` đồng thời cho cùng ID chỉ tạo một user; đổi username vẫn là cùng user, không tăng quyền.
- **IA-02:** User thường gọi `/admin`, sửa callback hoặc gọi trực tiếp protected use case đều bị từ chối và không mutate DB/expose dữ liệu.
- **IA-03:** Grant/revoke có hiệu lực ở lần gọi tiếp theo; ADMIN không grant/refund khi chưa có đúng permission; SUPPORT không vượt ticket scope.
- **IA-04:** Owner bootstrap lặp lại không tạo thêm owner; thay OWNER_ID trái với persisted owner fail có thông báo vận hành an toàn.
- **IA-05:** Private/customer ownership và staff scope có tests riêng; không có token/password/profile payload nhạy cảm trong logs/errors.

## Verification khi triển khai

Unit tests cho authorization matrix và malicious actor inputs; PostgreSQL integration cho unique identity/permission mutation + audit; adapter contract tests cho `/start`, `/admin`, private-chat guards. Chạy gates thật trong PROJECT sau bootstrap, không ghi pass từ tài liệu này.
