# TASK: Xây dựng Telegram Digital Goods Shop Bot — Production-oriented

Bạn là Senior Backend Engineer, Telegram Bot Developer và Software Architect.

## 1. Mục tiêu

Xây dựng một Telegram Shop Bot tự động bán các tài nguyên MMO hợp pháp, bao gồm sản phẩm số, license key, mã kích hoạt, tệp tin, template, công cụ và tài nguyên được phép phân phối.

Hệ thống phải hỗ trợ:

- Khách xem và mua hàng ngay trong Telegram.
- Quản lý danh mục, giá và số lượng tồn kho.
- Thanh toán bằng Telegram Stars.
- Giao sản phẩm tự động sau khi xác nhận thanh toán.
- Quản lý đơn hàng, khách hàng và hỗ trợ.
- Admin điều hành qua Telegram.
- Khả năng mở rộng sang Telegram Mini App sau này.

Chỉ hỗ trợ phân phối tài nguyên có nguồn gốc hợp pháp và được phép bán lại. Không xây dựng chức năng phát tán thông tin đăng nhập đánh cắp hoặc sản phẩm xâm phạm quyền truy cập.

Yêu cầu quan trọng: Triển khai hệ thống thực sự hoạt động, không chỉ làm mockup hoặc prototype.

## 2. Công nghệ

Ưu tiên:

- Python 3.12+
- aiogram 3.x
- FastAPI cho webhook và health endpoints
- PostgreSQL
- SQLAlchemy 2.0 Async
- Alembic migrations
- Pydantic Settings
- Redis tùy chọn nếu cần FSM hoặc rate limiting phân tán
- Pytest, Ruff
- Docker và Docker Compose

Kiến trúc modular monolith, phân chia rõ:

- bot/routers
- bot/keyboards
- application/services
- domain/models
- infrastructure/database
- infrastructure/payments
- infrastructure/delivery
- infrastructure/security
- admin
- tests

Không gộp database, payment logic, business logic và Telegram handlers vào một file lớn.

## 3. Chức năng khách hàng

### /start

- Tự động tạo hoặc lấy hồ sơ theo Telegram User ID.
- Lưu username, display name, language, thời gian đăng ký.
- Hiển thị menu chính bằng inline keyboard.
- Hỗ trợ Vietnamese mặc định, kiến trúc sẵn sàng i18n.
- Không yêu cầu tạo mật khẩu vì Telegram đã cung cấp danh tính người dùng qua Bot API.

Menu chính:

🛍️ Cửa hàng  
🔎 Tìm sản phẩm  
📦 Đơn hàng của tôi  
🎟️ Mã giảm giá  
👤 Tài khoản  
💬 Hỗ trợ  
📜 Điều khoản

### Cửa hàng

- Danh mục và danh mục con.
- Trang sản phẩm có tên, ảnh, mô tả, giá Stars, số lượng còn lại.
- Phân trang bằng inline keyboard.
- Tìm kiếm sản phẩm.
- Nút Mua ngay.
- Xem sản phẩm còn hàng hoặc hết hàng.
- Không bao giờ hiển thị dữ liệu bí mật của sản phẩm trước khi khách thanh toán.

### Checkout

- Tạo order duy nhất.
- Chụp snapshot giá tại lúc tạo order.
- Khóa hoặc giữ chỗ tài nguyên bằng cơ chế giao dịch database phù hợp.
- Hiển thị thông tin đơn và xác nhận thanh toán.
- Hỗ trợ trạng thái PENDING, AWAITING_PAYMENT, PAID, FULFILLING, DELIVERED, FAILED, REFUNDED, EXPIRED.
- Không tin vào giá hoặc sản phẩm do Telegram callback của client truyền lên.

### Lịch sử mua hàng

- Hiển thị đơn hàng đã mua.
- Mã đơn, tên sản phẩm, giá, trạng thái, thời gian.
- Xem lại nội dung đã giao theo quyền của chính chủ đơn hàng.
- Hỗ trợ gửi lại dữ liệu khi phù hợp.
- Không cho người khác xem dữ liệu đơn hàng qua ID tự sửa.

## 4. Hệ thống kho hàng

Hỗ trợ các loại sản phẩm:

A. UNIQUE_KEY — mỗi khách nhận một mã khác nhau.

B. DIGITAL_FILE — khách nhận file hoặc link tải có kiểm soát.

C. LICENSE — cấp một license key từ kho.

D. REUSABLE_RESOURCE — tài nguyên được phép giao cho nhiều người.

E. MANUAL_FULFILLMENT — cần admin xử lý thủ công.

Các loại trên phải có schema và fulfillment strategy rõ ràng, không dùng một chuỗi text duy nhất để mô phỏng mọi loại.

Admin có thể:

- Tạo, sửa, ẩn sản phẩm.
- Thêm và giảm giá.
- Import key theo CSV/TXT có validation.
- Xem tồn kho và lịch sử cấp phát.
- Nhận cảnh báo sắp hết hàng.
- Khóa hoặc vô hiệu hóa sản phẩm.
- Kiểm tra key trùng trong phạm vi SKU.

Ràng buộc quan trọng:

- Mỗi unique key chỉ giao tối đa một lần.
- Không bán vượt tồn kho.
- Hai khách thanh toán đồng thời không thể nhận cùng một key.
- Một giao dịch thanh toán gửi lại nhiều lần không làm giao nhiều sản phẩm.
- Nếu Telegram gửi tin nhắn thất bại sau khi cấp phát, phải có cơ chế retry an toàn và khôi phục đơn hàng.
- Tài nguyên nhạy cảm phải mã hóa khi lưu trữ và không xuất hiện trong log.

## 5. Thanh toán Telegram Stars

Tuân thủ tài liệu chính thức:

https://core.telegram.org/bots/payments-stars

Triển khai:

1. sendInvoice với currency XTR.
2. Nhận pre_checkout_query.
3. Kiểm tra order, user, amount, currency và khả năng cung cấp hàng.
4. Phản hồi answerPreCheckoutQuery trong giới hạn thời gian Telegram yêu cầu.
5. Chỉ xác nhận PAID khi nhận successful_payment hợp lệ.
6. Lưu telegram_payment_charge_id duy nhất.
7. Fulfillment sau khi thanh toán thành công.
8. Hỗ trợ refundStarPayment qua quy trình admin có kiểm soát.

Áp dụng idempotency theo payment charge ID và order ID.

Không dùng screenshot thanh toán làm bằng chứng tự động xác nhận đã trả tiền.

Không sử dụng thanh toán crypto, QR ngân hàng hoặc bên thứ ba để thay thế Telegram Stars cho hàng số bán bên trong bot.

Nếu có lỗi sau khi thanh toán, phải lưu trạng thái cần xử lý và báo admin; không được làm mất giao dịch hoặc tự ý giao trùng hàng.

## 6. Admin Panel trong Telegram

Lệnh /admin chỉ mở với danh sách Telegram User ID được phân quyền.

Menu:

- Dashboard
- Products
- Inventory
- Orders
- Customers
- Discounts
- Support Tickets
- Reports
- Settings

Vai trò:

OWNER: toàn quyền.

ADMIN: quản lý bán hàng theo các quyền được cấp.

SUPPORT: xem và xử lý yêu cầu hỗ trợ trong giới hạn quyền.

Các thao tác thay đổi giá, hoàn tiền, sửa kho, thay đổi quyền phải ghi audit log.

MVP ưu tiên admin qua Telegram. Web Admin chỉ bổ sung sau khi phần core hoạt động ổn định.

## 7. Coupons, Referral và Marketing

Triển khai sau core checkout.

Coupon:

- Giảm giá theo phần trăm hoặc số lượng Stars.
- Giới hạn lượt sử dụng.
- Thời gian hết hạn.
- Giới hạn theo sản phẩm hoặc danh mục.
- Chống lạm dụng sử dụng nhiều lần.

Referral:

- Link deep link `/start` với referral code.
- Lưu referrer hợp lệ khi user mới bắt đầu.
- Chặn self-referral.
- Theo dõi số đơn và doanh thu từ referral.
- Không tự tạo cơ chế chuyển tiền hoa hồng chưa được xác nhận là hợp lệ với chính sách Telegram.

Marketing:

- Admin đăng sản phẩm mới vào Channel được cấu hình.
- Deep link từ bài đăng đến đúng sản phẩm trong bot.
- Thông báo sản phẩm nhập kho trở lại.
- Chỉ nhắn tin tiếp thị cho người dùng phù hợp và đã cho phép nhận thông báo.

## 8. Database

Thiết kế ít nhất những thực thể cần thiết:

users  
roles / permissions  
categories  
products  
inventory_units  
orders  
order_items  
payments  
fulfillments  
coupons  
coupon_redemptions  
referrals  
support_tickets  
audit_logs

Thiết kế quan hệ, foreign keys, indexes, constraints và migration bằng Alembic.

Dùng transactions và database-level constraints cho các bất biến về tiền và tồn kho.

Không dùng file JSON hoặc biến global làm nguồn dữ liệu chính của đơn hàng và tồn kho.

Giá Stars là số nguyên. Không sử dụng floating-point để tính giá hoặc số dư tài chính.

## 9. Security & Reliability

Bắt buộc:

- BOT_TOKEN và các secret lưu qua environment variables.
- Không commit .env.
- Telegram admin xác thực bằng Telegram ID và kiểm tra quyền tại server.
- Webhook xác minh Telegram secret token.
- Mini App nếu bổ sung phải xác minh Telegram initData trên backend.
- Rate limiting và input validation.
- Audit log với hành động quản trị quan trọng.
- Không tiết lộ API key, inventory secrets hoặc thông tin giao hàng trong logs.
- Health check, structured logs, backup và quy trình restore.
- Retry Telegram API với backoff cho lỗi tạm thời.
- Không retry mù quáng các thao tác tài chính không idempotent.
- Môi trường dev và production tách riêng.
- Xử lý graceful shutdown.

## 10. UI/UX

Telegram bot cần giao diện đơn giản, nhất quán:

- Ưu tiên inline keyboard.
- Hạn chế gửi quá nhiều tin nhắn khi điều hướng; dùng editMessageText khi thích hợp.
- Có nút Back, Home và Cancel.
- Callback buttons phải hoạt động đúng trong các trạng thái.
- Thông báo lỗi dễ hiểu.
- Hiển thị trạng thái thanh toán và giao hàng rõ ràng.
- Nội dung tiếng Việt tự nhiên, không dùng câu trả lời mẫu chung chung.

Không cần xây dựng React frontend trong giai đoạn đầu.

## 11. Kiểm thử

Viết automated tests cho các trường hợp:

- Khách mua sản phẩm còn hàng.
- Sản phẩm hết hàng.
- Hai khách mua key cuối cùng đồng thời.
- Payment callback lặp lại.
- Thanh toán thành công nhưng gửi hàng thất bại.
- Bot restart trong lúc fulfillment.
- Không đủ điều kiện pre-checkout.
- Giá sản phẩm thay đổi khi order đang chờ.
- Khách truy cập đơn hàng của người khác.
- User thường cố gọi admin commands.
- Admin hoàn tiền.
- Mã giảm giá không hợp lệ, hết hạn, vượt lượt.
- Import kho với dữ liệu lỗi hoặc key trùng.

Dùng mock Telegram API và PostgreSQL test environment để xác minh flow. Hướng dẫn kiểm thử Telegram Stars bằng môi trường test chính thức.

Không tuyên bố kiểm thử live thành công khi chưa thực hiện.

## 12. Triển khai theo giai đoạn

Phase 0: Khảo sát repository, xác định kiến trúc và tạo tài liệu PLAN.md.

Phase 1: BotFather integration, cấu hình, database, migrations, /start, catalog.

Phase 2: Products, Inventory, Orders, Stars payment, atomic fulfillment.

Phase 3: Admin, support, refunds, operational security.

Phase 4: Coupons, referral, reporting, channel integration.

Phase 5: Production deployment, tests, monitoring và review.

Sau mỗi phase:

- Chạy test liên quan.
- Kiểm tra lỗi critical/high.
- Ghi lại thay đổi.
- Đánh giá các rủi ro.
- Không để tính năng core chỉ hoạt động với mock data.

## 13. Deliverables

Tạo:

- Source code hoạt động.
- README.md hướng dẫn chạy.
- ARCHITECTURE.md
- DATABASE.md
- SECURITY.md
- BOTFATHER-SETUP.md
- DEPLOYMENT.md
- .env.example
- Dockerfile và docker-compose.yml
- Alembic migrations
- Automated tests
- Sample product seed data (dữ liệu giả, không chứa secret thật).

Chỉ yêu cầu tôi cung cấp BOT_TOKEN, Telegram OWNER_ID hoặc credentials khi thực sự cần. Không yêu cầu gửi secret vào chat hay commit vào Git.

Hãy chủ động triển khai đến mức có thể kiểm chứng được. Với các thao tác liên quan thanh toán thật, quyền truy cập production hoặc tài khoản của tôi, phải xin xác nhận trước khi thực hiện.

Bắt đầu bằng cách kiểm tra workspace hiện tại, tạo implementation plan ngắn, sau đó thực hiện lần lượt các phase. Không tự ý bỏ qua tests hoặc thay thế nghiệp vụ cốt lõi bằng placeholder.

Mục tiêu nghiệm thu cuối cùng: Một người dùng có thể mở Telegram bot, xem sản phẩm, thanh toán trong môi trường test, nhận đúng tài nguyên đã mua, và admin có thể quản lý toàn bộ giao dịch một cách an toàn.v