# ADR-0005: Telegram user client cho nghiệm thu T14

- Status: Accepted for isolated test tooling, theo yêu cầu Owner ngày 23/10/2026.

## Context

Runtime dùng aiogram/Bot API. Bot API không cung cấp phiên người mua để gửi command,
callback và thanh toán invoice như một user. Desktop automation đã làm chậm nghiệm thu
do focus/coordinate và không tạo bộ test lặp lại thuận tiện. Owner yêu cầu ưu tiên
Telethon + Bot API và hoàn thiện T14; gate E vẫn cần kết quả thực tế.

## Decision

Thêm Telethon pin trong dependency group `telegram-e2e` của manifest/lock hiện có.
Chỉ test tooling import Telethon; aiogram và import boundaries của application giữ nguyên.
Client chỉ kết nối Telegram Test Server, bot test riêng và database test có marker đã
xác minh. Không reuse Desktop tdata, session main hoặc bot token main.

Credentials MTProto nằm trong ignored `.env.telegram-e2e`; session trong ignored
`.cache/telegram-test/`. Số Owner có thể đọc từ Phone_Test_Server trong private bot-test
file; nếu thiếu thì nhập ẩn tại terminal. Buyer2 nhập số riêng; OTP/2FA luôn Owner tự nhập
tại terminal. Desktop local password không dùng để xác thực MTProto.
Không log body, phone, auth/session data, charge/file IDs hoặc inventory payload.
Mọi payment/refund timeout giữ checkpoint để đối soát, không tự retry external effect.
Các thao tác có tác dụng phụ được gọi rõ bằng subcommand; không nằm trong suite mặc định.

## Alternatives

- Bot API riêng: phù hợp phía merchant nhưng thiếu user client cho end-to-end buyer flow.
- Computer Use toàn bộ: có bằng chứng UI nhưng chậm và phụ thuộc foreground/rendering.
- Đưa Telethon vào runtime: không cần thiết cho kiến trúc đã chọn.

## Consequences và evidence

Giữ một lockfile và mọi phiên bản runtime hiện tại. Dependency source/build cần được
review theo ADR-0004; không blanket-enable third-party builds. Session là credential và
phải được bảo vệ như token. Hai-buyer tests cần hai user Test riêng; API client không bỏ
qua Telegram login limits. Vẫn cần smoke hiển thị trên client khi đánh giá giao diện.

Client reuses pinned SDK initialization metadata/layer cho read-only initConnection/GetConfig
trước auth và sau thời gian chờ input; xác minh test_mode trước credential submission.
Private `_get_dc`/`_init_request` cần review khi nâng SDK. Không bật RPC effect retries để
chữa lỗi connection; actual authentication và gate E vẫn cần bằng chứng riêng.

Evidence setup: pin Telethon1.45.0, pyaes1.6.1, rsa4.9.1, pyasn1 0.6.4; không đổi runtime
versions. Pyaes chỉ có sdist; setup helper chặn source builds của mọi locked dependency khác,
kiểm tra reviewed source hash và pin setuptools80.9.0 cho build. Native audit53 packages
không known CVE, nhưng rsa archived. Chấp nhận giới hạn này cho Test Server/fake fixtures;
không đưa test SDK vào production, đánh giá lại khi nâng Telethon hoặc trước khi mở scope.

Implementation và kết quả thực tế được ghi ở [T14](../plans/shop/implementation-plan.md)
và [hướng dẫn nghiệm thu](../BOTFATHER-SETUP.md); ADR này không chứng minh gate E đã pass.

Nguồn: [Telethon Test Server](https://docs.telethon.dev/en/stable/developing/test-servers.html),
[session security](https://docs.telethon.dev/en/stable/concepts/sessions.html),
[Telegram API credentials](https://core.telegram.org/api/obtaining_api_id),
[Stars payment](https://core.telegram.org/method/payments.sendStarsForm).
