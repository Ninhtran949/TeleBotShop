# Implementation plan và task ledger

Status: **design baseline hoàn tất; application execution chưa bắt đầu**. Tất cả checkbox ứng dụng dưới đây đang pending, không phải bằng chứng đã triển khai. Agent chỉ làm task/phase được giao, không tự coi roadmap là authorization. Owner đã giao thiết kế các tài liệu này, chưa giao chạy các phase ứng dụng trong lượt thiết kế.

## Cách nhận và bàn giao task

1. Đọc AGENTS → PROJECT → đúng spec/ADR/convention section; xác nhận dependency thật sự hoàn tất bằng code/evidence.
2. Ghi task ID đang làm, phạm vi file dự kiến và acceptance sẽ chứng minh ngay trong mục task. Nếu một task cần nhiều behavioral slices, tách child IDs như `T10.a` trước khi code, giữ acceptance/verify riêng; không đóng task cha khi mới làm một child. Không sửa/xóa work đang dở của task khác.
3. Implement một vertical slice qua domain/use case/persistence/transport cần thiết; kiểm thử và review rồi mới chuyển slice. Không dựng toàn bộ schema/layer trước feature.
4. Chỉ tick done khi AC tương ứng đạt; ghi exact commands, exit/result, environment, review findings còn mở và external checks chưa chạy tại chính mục đó. Không cần status/ledger file song song.

Check scope: **H** = hai harness commands trong PROJECT. **C** = applicable lock/lint/format/unit/contract/architecture/build gates trong CONVENTIONS sau bootstrap. **D** = PostgreSQL integration + migrations với isolated test DB. **E** = actual Telegram test-environment evidence, không suy ra từ mock. **O** = operational/restore/release checks. Không gọi gate chưa được cấu hình là pass.

## Phases và dependency order

| Phase | Tasks | Exit gate |
| --- | --- | --- |
| 0 — Design | Tài liệu hiện tại | Spec/ADR/routing nhất quán; harness/doc checks, không có runtime claim. |
| 1 — Foundation/catalog | T01-T05 | C + D; identity/permission/catalog/inventory thật, không lộ secrets. |
| 2 — Commerce/delivery | T06-T10 | C + D; stock/payment/recovery races đạt và đủ năm product types. |
| 3 — Orders/admin/support | T11-T14 | C + D + E; core customer/admin flow, support/refund và audit đạt. |
| 4 — Growth | T15-T17 | C + D; coupon/referral/opt-in delivery, external messaging riêng khi được phép. |
| 5 — Release readiness | T18 | C + D + O và explicit production authorization; readiness không tự là deploy. |

## Phase 1

- [ ] **T01 — Toolchain và package baseline.** Depends: design. Scope: `pyproject.toml`, `uv.lock`, Python/uv version pin, minimal `src/telebotshop` package, test/Ruff configuration và quality-check entrypoints. Acceptance: clean locked environment; wheel/import smoke; meaningful architecture-check self-cases; harness chạy riêng được. Verify: H + applicable C, record real package/runtime versions. Không tạo placeholder business flows hoặc dependencies cho feature chưa làm.

- [ ] **T02 — Runtime wiring, settings và database readiness.** Depends: T01. Scope: bootstrap/entrypoints, FastAPI health, PostgreSQL/async session UoW, Alembic baseline, dev/test Compose settings. Acceptance: startup fail với invalid config; ready phản ánh DB/schema; isolated test DB/migration clean/upgrade; clients/session đóng khi shutdown. Verify: C + D; integration tests chứng minh UoW rollback/no hidden commit và import matrix. Cập nhật PROJECT bằng exact commands đã tồn tại.

- [ ] **T03 — Identity, permissions và audit foundation.** Depends: T02. Spec: IA-01..05, AO-01. Scope: user/role/permission/audit schema + use cases, `/start`/Owner bootstrap, Owner grant/revoke UI và safe settings/admin menu foundation. Acceptance: idempotent profile; current permission/default deny kể cả direct use-case call; Owner mismatch không tự cấp quyền; transactional audit cho protected mutations. Verify: C + D, concurrent identity/permission and audit rollback tests.

- [ ] **T04 — Catalog và admin product maintenance.** Depends: T03. Spec: CI-01,04; AO-01,06. Scope: categories/products + validated five-type metadata, browse/search/pagination, staff product/price/sale-state mutations. Acceptance: dữ liệu DB thật; safe Việt navigation; owner-authorized writes có audit, read models không có secret. Verify: C + D, permission/tampered callback/pagination/price-change tests. Seed chỉ là demo/test inventory hợp pháp giả, không thay nguồn dữ liệu runtime.

- [ ] **T05 — Encrypted inventory và atomic import.** Depends: T04. Spec: CI-03, CI-04, CI-06; ADR-0003. Scope: unit storage/crypto/HMAC, CSV/TXT upload import, retire permissions và low-stock query/signal. Acceptance: duplicate/invalid/oversize batch rollback; encryption fail-closed; two-connection import không trùng; secret không lọt logs/errors. Verify: C + D + focused security review. Reservation/order FKs và last-key checkout race thêm ở T06/T08, không tạo fake orders/schema sớm để test T05.

## Phase 2

- [ ] **T06 — Create order, reserve và expiry.** Depends: T05. Spec: OP-01, OP-02, OP-07; CI-02, CI-05. Scope: order/item snapshots và reservation FK migration, action idempotency, finite-stock reservation, invoice intent/Stars adapter, guarded expiry/cancel. Acceptance: quantity-one checkout/price snapshot; no network under DB lock; double-click/last-key/expiry races giữ stock đúng. Verify: C + D, mocked invoice outcomes gồm uncertain và price change, hai independent DB connections.

- [ ] **T07 — Pre-checkout boundary.** Depends: T06. Spec: OP-02,07. Scope: Telegram pre-checkout parsing/response + application validation/serialized approval. Acceptance: owner/currency/amount/payload/status/stock checks; duplicate query idempotent, query khác không double-approve; bounded DB/API deadline đáp ứng giới hạn Telegram. Verify: C + D và controlled timeout/contention tests; approval không được mark PAID.

- [ ] **T08 — Durable payment receipt và fulfillment intent.** Depends: T07. Spec: OP-03..05,07. Scope: payments/fulfillments contract, successful-payment handler và atomic UoW. Acceptance: duplicate/conflicting/extra/late/unknown receipts được giữ đúng; matched payment + allocation + work atomic; webhook không ack trước durable outcome. Verify: C + D, concurrent duplicates, injected commit failure và payment-vs-expiry race; no delivery call trong handler.

- [ ] **T09 — Key/license worker và recovery.** Depends: T08. Spec: FD-01..03,06; CI-05. Scope: work claim/lease/CAS, delivery attempts, unique-key/license sending, retry/uncertain/alerts. Acceptance: một active sender, stale token bị chặn, crash sau send không cấp key mới và không auto-send uncertain. Verify: C + D, two-worker races và fault injection trước/sau từng durable/send boundary; focused security review.

- [ ] **T10 — File, reusable và manual fulfillment.** Depends: T09. Spec: CI-04, FD-04,06. Scope: từng typed dispatch adapter và manual work completion có permission/audit. Acceptance: mỗi loại giao đúng payload cho đúng buyer; unsupported/corrupt payload fail-closed; MANUAL không giả delivered khi payment tới. Verify: C + D + mocked message/document contracts; tách T10.a/b/c nếu cần, child nào xong không thay thế coverage cả ba loại.

## Phase 3

- [ ] **T11 — Order history và resend an toàn.** Depends: T10. Spec: FD-05, IA-02,05; AO-02,06. Scope: customer/staff order views và authorized entitlement reader/resend actions. Acceptance: buyer/staff scopes đúng, customer không mở order của người khác, resend cùng entitlement và không mở lại refunded order; staff dashboard/order counts dùng DB thật. Verify: C + D, IDOR/callback replay/resend state tests.

- [ ] **T12 — Support và payment support.** Depends: T11. Spec: AO-02,03. Scope: tickets, staff assignment/scoped replies, `/paysupport`, terms/support navigation. Acceptance: đúng owner/ticket permission; lỗi paid/uncertain có support path; real terms/support identity là Owner input, tests dùng nội dung giả rõ ràng. Verify: C + D, cross-user/ticket isolation và sanitized output contracts.

- [ ] **T13 — Refund và privileged operations.** Depends: T12. Spec: OP-06, AO-01,06; FD-06. Scope: persisted refund intents, action confirmation/dedup, grant-aware admin refund UI và uncertain reconciliation path. Acceptance: only authorized full refund; no refund-vs-send race, no automatic uncertain retry/restock; audit/business state atomic. Verify: C + D và focused security review với refund timeout/replay/worker concurrency. Actual real-account refund không được thực hiện từ việc task code được giao.

- [ ] **T14 — Core integration và Telegram test acceptance.** Depends: T13. Scope: complete customer/admin navigation/contracts, operational alerts, BotFather/test-webhook setup instructions và end-to-end evidence. Acceptance: đủ năm types và customer/admin menus thuộc core hoạt động trên DB thật; test Stars checkout/delivery/refund được quan sát; không Critical/High còn mở trong core. Verify: C + D + E theo matrix dưới; thiếu external credentials/setup thì ghi gate chưa verify và không tick task done. Không cần credentials để hoàn tất các automated tests độc lập.

## Phase 4

- [ ] **T15 — Coupons.** Depends: T14. Spec: GR-01,02. Scope: coupons/redemptions + admin/customer flows và checkout integration. Acceptance: đúng scope/expiry/caps, integer price snapshot và transactional redemption; last-use race/duplicate/late payment không over-redeem. Verify: C + D, concurrency tests. Không đổi snapshot của invoice đã phát.

- [ ] **T16 — Referral.** Depends: T15. Spec: GR-03. Scope: referral deep links/attribution/stats. Acceptance: new-user attribution immutable, không self-referral, paid/refunded reporting đúng; không commission payout. Verify: C + D, concurrent/replayed attribution và refund-adjusted stats.

- [ ] **T17 — Opt-in marketing, channel và reports.** Depends: T16. Spec: GR-04,05; AO-06. Scope: subscription/notification intents, restock alerts/channel posts, bounded reports và admin controls. Acceptance: consent/opt-out/dedup/rate limits, valid product links, không auto paid broadcast; reports từ DB thật. Verify: C + D + mocked external contracts; actual channel messages chỉ khi explicit authorization và ghi external evidence riêng.

## Phase 5

- [ ] **T18 — Operational/release readiness.** Depends: T17. Spec: AO-04,05 và mọi core AC. Scope: Docker production image/config, migration/rollout runbook, monitoring, graceful shutdown, backup/key restore rehearsal, dependency/security review và retained evidence. Acceptance: clean build/start/readiness; test restore dùng DB + key versions; all required AC đạt hoặc rõ blocker, Owner policies và production config được chuẩn bị. Verify: H + C + D + O và E evidence phù hợp phiên bản release. Production deployment, live payments, credential/account changes cần authorization riêng; không tick deploy chỉ vì local tests xanh.

## Nghiệm thu bắt buộc từ task.md §11

| Case | AC / task sở hữu |
| --- | --- |
| Mua còn hàng / hết hàng | CI-01,02; OP-01,02; T05-T08 |
| Hai khách mua key cuối | CI-02; OP-03; T06,T08 |
| Payment callback lặp | OP-03,04; T08 |
| Paid rồi gửi hàng lỗi | FD-02,03; OP-07; T09 |
| Restart giữa fulfillment | FD-01,02; T09 |
| Không đủ điều kiện pre-checkout | OP-02; T07 |
| Giá đổi khi order chờ | OP-02; T06,T07 |
| Xem đơn người khác | IA-05, FD-05; T11 |
| User thường gọi admin | IA-02,03, AO-01; T03,T13 |
| Admin refund | OP-06, AO-01; T13,T14 |
| Coupon invalid/expired/over-limit | GR-01,02; T15 |
| Import lỗi/trùng key | CI-03,06; T05 |
| Bổ sung failure cases cho thiết kế | Late/extra/conflicting payment, expiry-vs-pay, stale lease, ambiguous send/refund, restore keys: OP-03..06, FD-01..06, AO-05 |

## Deliverables và canonical paths

`task.md` dùng một số tên tài liệu ở root; baseline này đặt tài liệu chi tiết dưới `docs/` để không có hai bản chuẩn. Không tạo redirect/copy cùng nội dung chỉ để có cả hai paths.

| Deliverable | Canonical path / task |
| --- | --- |
| App source, tests, fake seeds | `src/telebotshop/`, `tests/`, seed entrypoint thuộc infrastructure khi T04/T05 cần |
| Hướng dẫn chạy | `README.md`, thêm khi bootstrap; PROJECT ghi trạng thái và actual commands |
| Architecture | `docs/ARCHITECTURE.md` đã thiết kế; cập nhật theo implementation evidence |
| Database/schema/migrations | `docs/DATABASE.md`, `migrations/`; cập nhật T02 và từng schema slice |
| Security | `docs/SECURITY.md`; controls thực tế từ T03/T05, review hoàn thiện T18 |
| BotFather/test environment | `docs/BOTFATHER-SETUP.md`; T14, không chứa token thật |
| Deployment/backup/restore | `docs/DEPLOYMENT.md`; T18, explicit production gate |
| Environment/build/runtime | `.env.example`, `Dockerfile`, `docker-compose.yml`, lock; T01/T02 và hoàn thiện T18 |
| Specs/plan/ADRs | Các tài liệu hiện tại; không tạo root PLAN.md/SPEC.md trùng lặp |

## Bằng chứng của bước thiết kế

Ngày 2026-10-21, chỉ verification cho tài liệu/harness:

- `python scripts/verify_harness.py`: PASS, six skills/integrity/reference paths/layout.
- `python -B -m unittest discover -s tests -p test_harness.py -v`: 6 tests, OK. Không thay test hoặc verifier để đạt kết quả này.
- Standard-library document audit: 18 authored Markdown files, 58 local links hợp lệ, code fences đóng đủ; 6 capabilities không có dependency cycle; 35 unique AC IDs, không có reference AC không tồn tại; 18 application tasks vẫn pending. Task-dependency/preservation/format checks được rà soát lại sau self-review.
- Baseline hash comparison: chỉ `AGENTS.md` và docs được thêm/sửa; `task.md`, toàn bộ `.agents/`, verifier/tests và ignore/attributes giữ nguyên. Không tạo source, manifest, Git metadata, credential hoặc app configuration.
- Instruction self-review: trivial docs trực tiếp; code dùng đúng capability spec; payment/stock/security thêm tests/review; upstream auto-commit/router/templates không override project authorization/path rules. Extra/late charge, refund primary-vs-extra, uncertain delivery, staged schema và Owner-only operational settings được rà lại trong thiết kế.
- Official sources được đối chiếu: Telegram Stars/Bot API, PostgreSQL locks, SQLAlchemy 2.0 async, uv locking/options, cryptography AEAD; link đặt tại spec/ADR tương ứng. Đây là design research, không phải external smoke.
- `git status --short --branch` vẫn báo workspace không phải Git repository. Không commit/push/deploy, không runtime/DB/live check. App gates và compatibility evidence vẫn là việc của T01 trở đi; những điều Owner cần chốt trước mở bán nằm trong PROJECT.
