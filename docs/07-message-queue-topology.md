# Message Queue Topology: HTTPE — PayScale

**Executive Summary:** Designs the full Kafka topic topology — partition counts derived from real throughput math rather than picked arbitrarily — plus the Outbox Pattern implementation for exactly-once semantics and a per-consumer-group dead-letter and retry policy.

## 1. Kafka Topic Structure

| Topic | Partitions | Key | Retention | Replication | Purpose |
|---|---|---|---|---|---|
| `transaction-events` | 64 | `transaction_id` | 7 days | RF=3, min.insync.replicas=2 | Every saga state transition (INITIATED → PROCESSING → COMPLETED/FAILED/REVERSED), including compensation events |
| `settlement-events` | 12 | `merchant_account_id` | 14 days | RF=3, min.insync.replicas=2 | Per-merchant settlement completion, from the Day 4 batch settlement flow |
| `fraud-alerts` | 6 | `account_id` | 30 days | RF=3, min.insync.replicas=2 | Transactions flagged for manual review (e.g., when CB-FRAUD trips and allows-with-flag) |
| `transaction-events.notification.dlq` | 4 | (inherited) | 30 days | RF=3 | Poison messages from the notification consumer group |
| `transaction-events.reconciliation.dlq` | 4 | (inherited) | 30 days | RF=3 | Poison messages from the reconciliation consumer group |
| `transaction-events.audit.dlq` | 4 | (inherited) | 30 days | RF=3 | Poison messages from the audit consumer group |

### Key Strategy Justification

- **`transaction-events` keyed by `transaction_id`:** guarantees every event in one transaction's lifecycle — including any compensation event — lands in the same partition, so consumers see them in the order they happened. This matters most for the Audit & Compliance Service, which must reconstruct an accurate state-transition history, and Reconciliation, which needs to see a transaction's full lifecycle before judging it complete.
- **`settlement-events` keyed by `merchant_account_id`:** keeps one merchant's settlement events (including any retry) in order, while naturally spreading load across the many different merchants.
- **`fraud-alerts` keyed by `account_id`:** keeps a given account's flagged-transaction history ordered, useful for building a per-account risk timeline.

### Why 64 Partitions for `transaction-events` (Partition Calculation with Throughput Proof)

Each logical transaction generates roughly 3 events on average (e.g., `INITIATED` → `PROCESSING` → `COMPLETED`, with occasional extra events for `FAILED`/`REVERSED`/compensation).

- **Sustained load:** 12,000 TPS × 3 events/txn = **36,000 events/second**
- **Burst load:** 18,000 TPS × 3 events/txn = **54,000 events/second**
- **Assumed per-consumer-instance throughput:** 1,500 events/second (a conservative figure for a lightweight consumer doing I/O — a DB write or an HTTP call to a notification provider — not raw in-memory processing)
- **Instances needed at burst:** 54,000 ÷ 1,500 = **36 instances** for the most demanding consumer group

Per the Kafka fundamentals in the training material (Section A5.2): a consumer group can never usefully run more instances than the topic has partitions — extra consumers beyond the partition count sit idle. Provisioning **64 partitions** leaves comfortable headroom above the 36-instance burst requirement (roughly 78% extra headroom), covering both burst traffic and future growth toward the 35M-user projection without a partition-count migration (which, unlike adding Postgres shards, requires more care in Kafka since existing keyed messages don't automatically redistribute when partitions are added).

---

## 2. Consumer Group Topology & Processing Rate Calculations

| Consumer Group | Latency Tolerance | Instances (sustained) | Instances (burst) | Derivation |
|---|---|---|---|---|
| `notification-service-group` | Low — FR-008 requires sub-second propagation | 24 | 36 | 36,000 ÷ 1,500 = 24; 54,000 ÷ 1,500 = 36 |
| `audit-compliance-service-group` | Low — a stalled audit consumer is a P1 (compliance-critical) | 24 | 36 | Same math — this group cannot be allowed to lag |
| `reconciliation-service-group` | High — reconciliation is inherently a lagging, batch-tolerant process | 8 | 12 | Sized smaller deliberately; lag is monitored, not eliminated |

All three groups attach independently to the same 64-partition topic (each consumer group gets its own full copy of the partition assignment — this is how Kafka consumer groups work per Section A5.2). Even at burst, the largest groups (36 instances) stay well under the 64-partition ceiling, so no group is ever partition-starved. `kafka_consumer_lag` is monitored per consumer group with an alert threshold of 10,000 messages (per the observability design, Section A5.6) — this is what catches the Reconciliation group falling meaningfully behind, rather than assuming its smaller instance count is always sufficient.

---

## 3. Exactly-Once Semantics: The Outbox Pattern

Financial correctness requires that a transaction event is never lost (money moved but nobody notified/audited) and never duplicated (double-counted in reconciliation). Kafka alone can't guarantee this across a database write *and* a message publish, since those are two separate systems that can't share a single atomic commit — this is the "dual-write problem" referenced in the training material (Section A5.2).

**Implementation:**

1. An `outbox_events` table lives in the same database (same shard) as the `accounts`/`transactions` tables being written to. When the Payment Processing Service commits a balance update and ledger entries, it inserts a row into `outbox_events` **in that same database transaction** — so either both the business data and the outbox event are committed together, or neither is (standard ACID atomicity, no cross-system coordination needed).
2. A lightweight **Outbox Relay** process polls `outbox_events` for unpublished rows (ordered by a monotonic sequence) and publishes each to `transaction-events`, using an idempotent Kafka producer (`enable.idempotence=true`, `acks=all`).
3. Once Kafka confirms the write (acknowledged by all in-sync replicas), the relay marks the row as published.
4. If the relay crashes between publishing and marking the row published, it will re-publish the same row on restart. This makes the *relay's* behavior "at-least-once," not exactly-once — but because each outbox row has a stable, deterministic `event_id`, downstream consumers (Notification, Reconciliation, Audit) dedupe on `event_id` the same way the payment path dedupes on `idempotency_key`. The end-to-end effect is exactly-once processing, built from at-least-once delivery plus idempotent consumption — this is the standard, well-understood way exactly-once semantics are achieved in event-driven systems, rather than relying on any single component to guarantee it alone.

**Alternative considered — CDC via Debezium:** reading the Postgres write-ahead log directly (instead of a custom outbox table + relay) is a legitimate alternative that avoids the polling relay entirely. It's not used for the initial build because it requires standing up and operating a Kafka Connect cluster — another new operational component on top of Kafka itself, which the team is already climbing a learning curve on (per ADR-001's acknowledged risk). The polling-relay outbox pattern is simpler to build, test, and reason about with the team's existing skills, and is flagged as a good candidate to revisit once the team has more Kafka operational experience.

---

## 4. Dead Letter Queue Handling & Retry Policies

| Consumer Group | Retry Attempts | Backoff | DLQ Alert Severity | Rationale |
|---|---|---|---|---|
| Notification | 3 | 1s → 5s → 25s | None (reviewed in a low-priority ops queue) | A failed push notification doesn't affect financial correctness — consistent with `CB-NOTIFY`'s existing fallback behavior (queue and proceed) |
| Reconciliation | 5 | 2s → 10s → 30s → 2min → 10min | P2 (business hours, 30-min SLA) | Reconciliation gaps need human attention but aren't urgent enough to page someone at 2am |
| Audit & Compliance | 2 | 1s → 5s | **P1 (immediate page, 5-min SLA)** | A compliance-trail gap is treated as a serious risk regardless of how small the customer-facing impact is — matches the policy already stated in the Day 3 HLD |

All DLQ messages retain for 30 days (enough time for investigation and manual reprocessing tooling to pick them back up) before archival. Note that **DLQ retention is not the system's actual compliance safety net** — the underlying business data (the balance update, the ledger entry) is already durably committed to PostgreSQL before the outbox event is even published, regardless of whether the downstream Kafka delivery ultimately succeeds. A message landing in a DLQ means a *consumer* (notification, reconciliation, or audit) fell behind or errored — it does not mean the transaction itself is at risk.
