# Payment V2 — specification, plan và evidence

Owner giao 24/10/2026; [ADR-0006](../../adr/0006-payment-v2-wallet.md). Stars PAUSED/BLOCKED;
T14 external E3/E4 chưa đạt, không đổi tiêu chí. Phạm vi: code thật + PostgreSQL thật ở
local/sandbox. Không financial production effects, Stars purchase, trade/withdraw, deploy/Git writes.

**Nghiệm thu mới24/10:** Owner yêu cầu cho PASS T14 nếu logic đã thay Stars được. T14 chức
năng hiện PASS theo [R1–R8](t14-replacement-acceptance.md): fresh C609/D510/H6 và actual
Telegram Test6shapes/Credit refund/resend/support. Stars E3/E4 vẫn là historical PAUSED gates;
hosted-provider/actual USDT/production không được tự đổi thành PASS.

## Capability map và interfaces

| Module | Trách nhiệm | Depends on |
| --- | --- | --- |
| wallet-ledger | integer accounts, balanced immutable journals, balance/history/audit | identity, UoW |
| wallet-checkout | explicit credit price, debit/order/allocation/work, compensating refund | wallet-ledger, catalog, orders, fulfillment |
| topups | intents, quote snapshot, verified evidence, auto/manual idempotent credit/reversal | wallet-ledger, access |
| bank-settlement | QR instructions, signed SePay sandbox webhook + read-only reconciliation | topups |
| usdt-settlement | configured sandbox quote, signed deposit-read adapter, identity/finality checks | topups |
| payment-v2-transports | customer/admin Telegram, independent authenticated operator CLI, flags | previous modules |

Domain DTOs thuần stdlib; ports ở application; SQL/provider adapters infrastructure;
bootstrap wire concrete. Không dùng generic PaymentService cho cả top-up và order.
Existing Stars PaymentService giữ receipt ingestion. Mỗi use case sở hữu UoW; network luôn ngoài UoW.

## Schema và invariants

- wallet_accounts: UUID, customer UUID hoặc system code, integer balance; unique customer/system.
- wallet_journals: UUID, unique operation_key, kind, subject, actor, reason, evidence digest, UTC.
- wallet_postings: journal/account FK, signed integer delta; journal hai postings tổng0, cấm edit/delete.
  Balance là projection DB-trigger; trực tiếp sửa balance/ledger history bị từ chối.
- product_credit_prices: product FK, positive integer credit; action-keyed operator audit.
- orders.payment_method: TELEGRAM_STARS default cho history; WALLET_CREDIT cho checkout mới,
  immutable. order_items: disjoint XTR/price_stars hoặc CRD/price_credits, snapshot immutable.
- payments: preserved Stars receipts, hoặc WALLET_CREDIT + unique wallet_journal_id; receipt
  fields NULL ở wallet. Một MATCHED payment/order và same method; deferred proof binds exact debit.
- topup_requests: UUID, buyer, rail, unique reference, requested credits, expected settlement amount,
  asset/network/token/recipient identity, Decimal rate/source, quoted_at/expires_at và state.
- settlement_events: canonical provider transaction identity + immutable facts/digest, trạng thái
  kiểm chứng; observation append-only cho progress/conflict. Không overwrite evidence đã dùng cấp credit.
- topup credit/review/reversal references: unique journal/top-up/event, actor/reason/source audit.
  Một external settlement không được cấp credit cho hai buyer hoặc hai intents.

Pending top-ups không phải held available balance. Chưa cần wallet holds: wallet checkout debit
và allocation atomic, không giữ tiền qua network. Không P2P, withdrawals, crypto custody/trading.

## State và transaction boundaries

Top-up: DETECTED → PENDING_CONFIRMATION → CONFIRMED → CREDITED; mismatch/expired/ambiguous
→ REVIEW_REQUIRED; authorized reject → REJECTED. CONFIRMED proof cùng atomic CREDITED journal.
Bank exact reference/recipient/amount/settled direction mới auto; USDT phải có exact chain/asset/
contract/destination/finality + independently bound buyer intent. Shared address + amount không đủ.
Manual review không biến screenshot/user TXID thành provider proof; approve requires verified
settlement, rechecks current state/credit, actor-bound confirmation; unsafe identity facts không override.

Checkout: buyer/action dedup → current credit-price/availability → order snapshot → exact debit →
permanent entitlement + PAID + READY + audit → commit. Không invoice, pre-checkout hay Stars API.
Worker shared xử lý sau commit; thất bại không hoàn debit hoặc release key ngầm.
Refund: current financial permission → order/work/resend locks → reject sending/uncertain →
unique compensating journal + hold + REFUNDED + audit. External money refund không được suy từ credit refund.
DB commit failure rollback toàn bộ; lost ACK gọi lại cùng action/operation key trả existing result.

Bank webhook: cap raw bytes/headers/time → HMAC(timestamp.raw_body) → parse validated facts →
durable evidence+credit transaction → HTTP success. DB fail non-2xx; signed replay event dedup.
Reconciler đọc bounded pages/windows ngoài UoW, dùng cùng canonical identities/intake với webhook.
USDT: source rate sandbox có cấu hình, ROUND_DOWN credits; immutable quote TTL; under/over/late
review, không lấy spot rate mới thay snapshot. Không network thật mặc định.

## Threat model và recovery

| Boundary / abuse | Control và required evidence |
| --- | --- |
| Forged webhook/replay/body mutation | raw HMAC + timestamp ±300s, unique transaction, digest conflict audit |
| Buyer identity/reference/recipient spoofing | authenticated account + exact server snapshot; no amount-only assignment |
| Double auto/manual / TXID reused | DB unique keys, row/advisory locks, one immutable credit posting |
| Overspending / last-key / double Buy | buyer/account/product/unit lock order + DB constraints + two-connection tests |
| Ledger tampering / bogus balanced journal | immutable rows, projection trigger, deferred balance/source binding |
| Refund vs send/resend/manual | existing work locks, no refund for in-flight/uncertain, permanent allocation |
| Stolen financial privileges | separate explicit wallet permissions, default deny, recheck in transaction + audit; grant/revoke giữ exclusive access lock, thao tác tài chính giữ shared lock tới hết transaction |
| Provider outage / missed callback | bounded read-only reconciliation, no retry ambiguous outbound financial effects |
| Leaked keys/PII | no raw provider payloads in logs, bounded parsed facts/digests, secrets env, no private keys |

## Slices và acceptance

Thứ tự: PV2-00 → PV2-01 → PV2-02 → PV2-03 → PV2-04 → PV2-05 → PV2-06 → PV2-07 → PV2-08.
Runtime pause/flag scaffolding được kéo sớm khi cần an toàn, không mở payment production.

- [x] **PV2-00 Audit/design:** audit reuse/coupling/races; ADR/schema/ports/states/threats/rollback và
  official provider research. Verify document/source review; external unknowns ghi riêng.
- [x] **PV2-01 Ledger:** migrate accounts/journals/postings; balance/history/operator adjustment;
  integer double-entry + immutable/nonnegative/idempotent enforced PostgreSQL. Verify unit +
  PostgreSQL concurrent debits, duplicate journals, tamper/unbalanced/rollback tests.
- [x] **PV2-02 Checkout:** explicit credit prices; buyer confirmation, atomic debit/payment/order/
  allocation/resource/work cho năm types; shared delivery không Stars objects/calls/receipts.
  Verify exact balance/ledger/work, insufficient funds, idempotency, last-key/two purchases/faults.
- [x] **PV2-03 Top-ups/review:** quote/reference, trusted-event ingestion, review queue, current
  financial permission, independent operator authentication, duplicate manual/auto approval.
  Verify one event/credit, mismatch/expiry/replay/source/authorization/commit failures.
- [x] **PV2-04 Bank local:** documented SePay HMAC raw-body/time, QR, sandbox API/read reconciliation;
  local full-path tests. Provider-hosted sandbox E2E chỉ PASS khi account/secret/endpoint actual sẵn sàng.
- [x] **PV2-05 USDT local/simulated:** separate Pay/exchange/on-chain; Decimal quote + deposit verification,
  shared-address ambiguity review, bounded signed read-only adapter. Simulated fixtures
  chứng minh flow; không claim exchange deposit testnet hoặc real USDT settlement.
- [x] **PV2-06 UX/flags:** wallet/balance/history/topup Bank+USDT/credit Buy/admin review/ledger;
  Stars runtime hidden/new invoice disabled, historical intake retained. Production external
  payments disabled; test-only configuration cannot be mistaken for production.
- [x] **PV2-07 Refund/recovery:** compensating credit refund/reversal, strict source proof,
  reconciliation and financial races; no outgoing money functions.
- [x] **PV2-08 Local gates/runbook:** full A/B/C/F/G, lock/lint/format/architecture/harness/build/audit;
  private evidence summary, safe migration rollback, provider E2E/production blockers reported.

- [ ] **D Hosted SePay E2E:** callback từ Dashboard Test Mode thật → ledger → Credit checkout/
  Telegram delivery; authenticated API reconciliation và replay. Chưa có hosted configuration.
- [ ] **E USDT external proof:** network/contract/recipient/buyer binding/chain finality và nguồn
  tỷ giá được xác minh. Exchange history adapter đã có; independent chain proof chưa tích hợp.
- [x] **Payment V2 Telegram Test delivery:** actual Test Bot + existing Owner MTProto session,
  đủ5types/6shapes,6Credit refunds, safe key resend và support thread. Top-up inputs vẫn
  locally signed/simulated; không gọi đó là real SePay/USDT payment. [Bằng chứng](t14-replacement-acceptance.md).

Kết quả local/Telegram Test giữ nguyên phạm vi Owner giao, không đóng các hosted-provider
gates còn unchecked hoặc Stars T14 E3/E4. Không tuyên bố Payment V2 production-ready.

Mốc T14 chức năng đã được Owner giao nghiệm thu theo điều kiện thay Stars; kết luận PASS
được ghi riêng, không phải sửa kết quả của các gate Stars hoặc provider chưa chạy.

## Migration, rollback và operational gates

Additive 0015+ sau0014; không sửa bytes migrations0001–0014. Existing order/payment values
default Stars, amounts untouched; no arbitrary Stars→credit conversion. New wallet-specific
deferred guards/triggers giữ nguyên original Stars function texts; cả hai rail có preservation tests.
Downgrade only empty PV2 history; nếu đã có financial records, stop writes và forward-fix /
restore reviewed backup, không DROP history. Chưa apply vào acceptance/main DB.

Runtime flags: STARS_CHECKOUT_ENABLED=false; wallet/bank/usdt explicit sandbox opt-in;
EXTERNAL_PAYMENTS_LIVE_ENABLED=false. Existing old Stars regression harness có thể bật Stars
explicitly; actual runtime default paused. New flow test never needs Stars invoice mocks.
Operator CLI independent of Telegram polling, local secret authentication + current grants +
preview/confirmation and reason/source. Không truyền secrets trên command line/chat.

Provider-hosted Bank E2E cần SePay Test Mode account, sandbox API token và webhook HMAC secret
local, receiving sandbox account, callback URL nếu provider cần public reachability. USDT real
network/token/recipient và Binance read-only credentials/IP whitelist chỉ hỏi khi external
integration được giao; không cần seed phrase/trade/withdraw. No real FX source yet: sandbox
configured rate explicitly labelled; live rate/legal/platform/terms/retention/refund require Owner review.

## Evidence ledger

Kết quả actual24/10, commands/limitations tại [PAYMENT-V2](../../PAYMENT-V2.md):

| Gate | Kết quả |
| --- | --- |
| A/B + architecture | 609 pass,0fail/error/skip; HMAC/QR/adapter/CLI/flags và Stars regressions. |
| C PostgreSQL + F/G | Fresh510cases pass qua3schema-isolated processes, mỗi process170cases. Thêm2regression closed CREDITED/REJECTED quote trong lần nghiệm thu T14.r; toàn bộ JUnit set khớp collected cases. Mốc508cũ được giữ trong artifacts trước. |
| Harness | 6 pass và six-skill/hash/layout verifier pass. |
| Lock/sync/lint/format/architecture/build | PASS; versions không đổi. `sync --locked --no-build --inexact --offline` giữ optional E2E packages cũ. |
| Package/runtime | 156 modules byte-match source; isolated wheel import155 modules (trừ entrypoint), no pytest/Telethon/network, poisoned settings, native asyncpg/AES-GCM pass; no private artifacts. |
| Dependency audit | 53packages,0known vulnerabilities;1adverse archived rsa trong optional Telethon group được báo, không giấu warning hoặc đổi lock. |
| Native sandbox smoke | Actual localhost health/readiness200, invalid HMAC401, locally signed Bank replay và simulated USDT CLI replay-safe, authenticated operator, balanced ledger;0Telegram/money-provider calls. |
| Telegram Test T14.r |6shapes thật, user-side payload/file download, Credit refunds, key resend, support pass;0Stars API/production effects. Inputs Bank/USDT vẫn locally signed/simulated. |
| D / E external | BLOCKED như các unchecked gates ở trên. E simulated PASS không phải E external PASS. |

Full D đầu490pass/15fail do history query đọc Credit column ở schema cũ; fix projection,
refund/support migration reruns14+5pass và full506 rerun pass. Notification migration oracle
được mở rộng cho additive defaults + SCHEMA_HEAD, vẫn kiểm tra mọi old Stars field/function;
không bỏ assertion hoặc sửa acceptance. Hai quote-expiry regressions bổ sung sau self-review.
HEAD/index, private configs, dependency files, migrations0001–0014, six skills và temp.md
giữ nguyên so với baseline phiên. Chỉ database PV2 mới được migrate; không commit/push/deploy.

T14.r phát hiện quote USDT đã đóng còn bị xét trong cửa sổ thời gian của deposit mới. Matching
giờ chỉ xét active intents, giữ nguyên route/amount/token/finality/time validation. Hai regression
CREDITED/REJECTED fail trước fix, pass sau fix;30affected cases và full D510pass. Chỉ2source/test
files thay đổi vì fix; kiến trúc, flags, schema, dependencies và business conversion policy giữ nguyên.
