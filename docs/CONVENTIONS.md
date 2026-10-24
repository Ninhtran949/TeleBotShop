# Coding và engineering conventions

Áp dụng cho code ứng dụng được triển khai sau baseline này. Rule cốt lõi về scope, an toàn và báo cáo nằm ở [AGENTS.md](../AGENTS.md); không nhân bản một bộ rule riêng cho từng agent.

## Python và tổ chức code

- Python 3.12, package `src/telebotshop`, identifier/module/test names tiếng Anh; nội dung bot tiếng Việt đúng dấu. Tài liệu dự án có thể dùng tiếng Việt; giữ API/SQL identifiers nhất quán bằng tiếng Anh.
- `snake_case` cho function/variable/module; `PascalCase` cho class; `UPPER_SNAKE_CASE` cho constants và enum members. Đặt use case theo hành động/nghiệp vụ như `create_order`, `confirm_payment`, `import_inventory`, không gom vào `utils.py`, `manager.py`, `common_service.py` mơ hồ.
- Type hints cho public functions, domain models, ports và return values. Không dùng `Any`, casts, `type: ignore` hoặc optional fields để né một contract chưa rõ. Không thêm type checker/framework mới nếu task chưa cần; type hints chưa đồng nghĩa đã type-check.
- Domain thuần Python; Pydantic dành cho config/transport validation. Dùng dataclass/value đơn giản khi cần DTO; không nhân đôi cùng model qua nhiều lớp nếu không có trách nhiệm/validation khác nhau.
- Pure business rules là hàm đồng bộ; I/O đi qua async port. Không dùng blocking HTTP/DB/file I/O trên event loop. Không tạo một AsyncSession global hay share session trong `gather()`.
- Inject trực tiếp dependency cần thiết ở bootstrap. Không import-time network/config load, singleton mutable business state, global order/inventory cache, auto-register plugin, factory framework hoặc generic base repository.
- Comments giải thích lý do khó thấy từ code; docstring dành cho contract không rõ. Known limit phải có `shortcut: <giới hạn>, <điều kiện nâng cấp>`; không tạo TODO cho tính năng ngoài scope.

## Dữ liệu, lỗi và external effects

- Stars dùng `int` và invariant dương; không dùng float cho giá/discount/refund. UUID nội bộ; Telegram IDs giữ integer; UTC-aware datetime. Clock chỉ inject nơi có logic thời gian cần kiểm thử, không tạo abstraction Clock cho toàn app.
- Validation tại transport không thay cho use-case checks. Re-check actor permission, owner, product status, price snapshot, stock và payment state từ server; callbacks chỉ mang references không đáng tin.
- Business errors đặt tên rõ và map thành thông báo Việt ở adapter. Bắt lỗi cụ thể tại nơi hiểu cách xử lý; không nuốt `Exception`, không trả success khi DB/external API lỗi, không gửi traceback nội bộ cho khách.
- Repositories map ORM ↔ domain/read DTO, không commit và không expose session. Use case sở hữu UoW. Transaction race/unique violation chỉ retry khi chứng minh transaction chưa tạo external effect.
- Side-effect result phải phân biệt success, known failure và uncertain. Timeout sau khi gửi request không chứng minh request chưa thực hiện. Không retry refund/send để chữa lỗi lưu DB sau network call.
- Logs dùng correlation/order/attempt IDs và error codes; audit lưu actor/action/target với quyền truy cập phù hợp. Không log nguyên Update, message body, invoice/token/DSN, CSV inventory, plaintext key hoặc payload giao hàng.
- Ticket bodies chỉ lưu encrypted envelope, không body/hash trong audit hoặc queue. Recheck current grant/persisted assignment trước đọc ciphertext; reply ACK chỉ nói đã lưu khi chưa có notification transport. Terms là public Owner content được cấu hình rõ, không fallback sang draft hoặc policy bịa.
- Refund chỉ full verified charge; encrypted decision + actor/current grant/state-bound confirmation, owned hold trước API, result/audit sau API. Positive evidence mới SUCCEEDED; absence trong một transaction page không là negative proof. Financial refund receipts bypass ordinary guards nhưng phải durable trước offset/ack.

## Database và migrations

Payment V2 mở rộng money contracts theo ADR-0006: credits là integer bounded, USDT/rate dùng
`Decimal` và ROUND_DOWN tường minh; không float. Customer balances là projection của immutable
double-entry postings, không cập nhật trực tiếp. Bank/USDT event không là Stars receipt.
Checkout Credit không tạo invoice/pre-checkout; refund Credit/reversal/cash-refund evidence là
ba nghiệp vụ riêng. Giữ mọi rule Stars bên trên trong phạm vi phương thức Stars.
Local/provider mocks, PostgreSQL thật, hosted SePay và actual USDT phải có evidence riêng.

- SQLAlchemy models chỉ ở infrastructure; migrations ở `migrations/`. Table/column `snake_case`, FK/index/unique/check constraints có tên rõ.
- Dùng FK, check và unique constraint cho money/ownership/allocation invariants; transaction lock cho state changes. Unit tests với fake repository không chứng minh concurrency.
- Mỗi schema change đi cùng migration và test từ database sạch, cùng upgrade path từ schema đang hỗ trợ. Inspect Alembic autogenerate; không tin output tự sinh là đúng.
- Không chạy migration khi import module hoặc mỗi request. Migration là bước operator rõ ràng trước rollout. Thay đổi phá dữ liệu cần plan backup/restore và authorization; không tự coi downgrade là an toàn.
- Không sửa migration đã được sử dụng; tạo revision tiếp theo. Tạo tables theo vertical slice; chưa triển khai coupons thì chưa tạo schema coupons.

## Test và review

| Loại | Phải chứng minh |
| --- | --- |
| Unit | Domain rules/use cases, state transitions, actor permission, failure handling; mock external effects qua port. |
| Integration | PostgreSQL thật: constraints, two-connection races, transactions/rollback, reservations, claim/lease, migrations. |
| Contract | Shape/authentication của Telegram/FastAPI adapters, callback tampering, SDK result/error mapping; Telegram API được mock. |
| Architecture | AST kiểm tra import matrix, kể cả relative imports; domain không kéo ORM/SDK/config vào. |
| External smoke | Test bot + Stars test environment, tách khỏi CI thông thường; chỉ ghi pass khi đã thực hiện và có evidence. |

Tên test mô tả behavior: `test_duplicate_charge_does_not_allocate_twice`. Test lỗi/race có giá trị hơn test chỉ lặp lại implementation. Regression test nên fail trước fix; không đo coverage để thay cho case tiền/kho/quyền quan trọng. Dùng fixtures giả, fake clock khi cần và hai transaction độc lập để test cạnh tranh.

Integration fixtures chỉ được reset database test được allowlist rõ, có `APP_ENV=test`, DSN test tách biệt và ownership marker của test run. Không infer an toàn chỉ từ substring tên DB; tuyệt đối không fallback sang DATABASE_URL production hay truncate schema không chứng minh thuộc test run.

Review theo correctness, simplicity, architecture, security, performance. Feature tiền/kho/quyền phải xem call chain và rollback/uncertain paths, không chỉ diff/happy-path test. Nêu Critical/High còn mở và chặn nghiệm thu affected scope; không đổi business rules để làm test pass.

## Toolchain và quality gates

**T01–T13 đã thiết lập toolchain/runtime và commerce/support/refund local. T14 nối public checkout/terms, customer lookup, authenticated webhook/test endpoint và durable metadata notifications qua các gate dưới đây. Telegram test E chưa chạy; CI chưa có.** Bằng chứng nằm trong [implementation plan](plans/shop/implementation-plan.md); lý do chọn ở [ADR-0004](adr/0004-toolchain-and-quality-gates.md).

`pyproject.toml` cấu hình Python/dependency groups, Pytest và Ruff; `uv.lock` là lockfile duy nhất. `.python-version` pin CPython 3.12.15; `tool.uv.required-version` pin uv 0.13.4, build backend pin cùng phiên bản; dev group pin Pytest 9.1.1 và Ruff 0.17.1. T02 pin runtime packages và PostgreSQL image/digest; xem PROJECT/DATABASE. Ruff: target `py312`, line length 100, rules `E`, `F`, `I`, `B`, `UP`; dùng formatter của Ruff. `.agents/` được loại khỏi lint/format để giữ nguyên bytes upstream; Python do repo sở hữu, gồm harness, vẫn được kiểm tra. Thêm rule sau khi có nhu cầu; không disable rule hàng loạt hoặc auto-fix ngoài scope.

```text
uv lock --check
uv sync --locked --no-build
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pytest tests/unit tests/contract tests/architecture
uv run --no-sync python scripts/check_architecture.py
uv build --no-sources
```

PostgreSQL gate riêng, với test service/APP_ENV/TEST_DATABASE_URL đã chuẩn bị theo README:

```text
uv run --no-sync pytest tests/integration
```

`tests/architecture` gồm positive/negative self-cases của checker và kiểm tra source thực. Checker dùng AST, resolve relative imports kể cả package `__init__.py`, không import code app; không thay review runtime/dynamic loading. `pythonpath` của Pytest chỉ thêm repository root cho tooling, không thêm `src`, nên không che lỗi package installation. Wheel/import smoke dùng môi trường tách biệt và Python `-I` theo README.

Pytest mặc định chạy unit/contract/architecture; PostgreSQL integration được gọi riêng, không skip để giả pass khi thiếu cấu hình. Async fixtures/test loop scope là function; mỗi integration test có engine/schema riêng theo DATABASE. `tests/test_harness.py` vẫn được kiểm tra riêng bằng lệnh standard-library trong PROJECT. Chỉ chạy test directories đã tồn tại; với docs-only change không chạy DB/app suite không liên quan.

CI dùng cùng commands, database test cô lập và lockfile không được tự sửa. `--locked` kiểm tra stale metadata/lock; không thay bằng `--frozen` để che drift. `--no-build` hạn chế build third-party sdist, không miễn việc review build backend của chính project. Không chạy curl-to-shell hoặc install script chưa review. Dependency audit trước release dùng tool tương thích lockfile đã chọn; ghi tool/version, findings/reachability và mọi deferral. Không tự forced-upgrade dependencies.

## Bàn giao một slice

Gắn task ID và capability AC vào thay đổi; ghi file/contract bị ảnh hưởng, commands/exit codes thực tế, expected failing test nếu dùng TDD, kết quả review và external gates chưa xác minh. Chỉ đánh dấu task done khi acceptance đạt; không đánh dấu toàn phase hoàn tất từ một case mock. Khi cần pause/resume, cập nhật chính task đang làm trong plan, không lập ledger/status file trùng lặp.
