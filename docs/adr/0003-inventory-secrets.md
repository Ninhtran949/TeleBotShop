# ADR-0003: Bảo vệ inventory và delivery secrets

- Status: Accepted, design only; chưa triển khai.

## Context

`task.md` yêu cầu mã hóa tài nguyên nhạy cảm, chống key trùng trong SKU và không log dữ liệu giao hàng. Plaintext database hoặc hash không keyed của key ngắn có thể lộ inventory khi backup/DB bị đọc. Chưa có secret thực hoặc store cần migrate.

## Decision

Dùng AEAD `AESGCM` từ `cryptography`, key 256-bit, nonce ngẫu nhiên 96-bit riêng mỗi encryption; lưu nonce/ciphertext/key version. AAD gắn stable SKU/unit identifiers và payload type để ciphertext không bị tráo giữa records. Không tự viết thuật toán mã hóa.

Dùng HMAC-SHA256 với **key dedup riêng** để fingerprint nội dung key theo SKU; DB unique constraint giữ chống trùng. Key material giữ nguyên case/nội dung hợp lệ, không casefold hoặc normalize tùy ý. Dedup key không tự rotation vì sẽ phá so sánh duplicate; đổi key cần migration/recompute từ ciphertext và kiểm chứng uniqueness riêng.

Keys nạp từ environment/secret mounting qua settings/bootstrap, không nằm cùng DB dump, source, logs hoặc sample data; startup fail nếu thiếu/sai key cho môi trường cần inventory. Encryption key có version để đọc records cũ khi rotate. Authorization/ownership phải thành công trước decrypt; chỉ giải mã đủ payload của entitlement được phép giao/xem lại. Không tạo bulk plaintext export mặc định.

## Alternatives

- Plaintext/base64: không đáp ứng confidentiality.
- Chỉ SHA-256 plaintext: không phục hồi để giao hàng, đồng thời key ít entropy có thể bị đoán.
- KMS/object storage ngay từ đầu: chưa có yêu cầu vận hành; giữ boundary để chọn khi có nhu cầu thật.

## Consequences và kiểm chứng

Owner phải bảo quản/backup key riêng; mất key là không khôi phục được inventory từ DB đơn thuần. Rotation không được xóa old key khi chưa migrate/restore thử. Tests: decrypt đúng, sai key/AAD/tampering bị từ chối, duplicate import cạnh tranh, logs không chứa plaintext, restore với key version cũ. Chưa có bài test nào trong số này được chạy vì app chưa tồn tại.

Nguồn: [cryptography AEAD/AESGCM](https://cryptography.io/en/stable/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM). AESGCM là primitive cần security review khi implementation; dependency/version được pin ở bootstrap, chưa được cài tại bước thiết kế.
