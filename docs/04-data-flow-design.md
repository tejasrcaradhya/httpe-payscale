# Data Flow Design: HTTPE — PayScale

**Executive Summary:** Walks through the P2P payment (happy path plus three failure scenarios), batch merchant settlement, and system failover flows in detail, distinguishing which steps are synchronous versus asynchronous and how each failure mode is detected, retried, or compensated.

This document narrates the three critical flows diagrammed in `diagrams/p2p-payment-flow.puml`, `diagrams/batch-settlement-flow.puml`, and `diagrams/failover-flow.puml`, covering error handling, timeout/retry behavior, compensation logic, and which steps are synchronous versus asynchronous.

---

## 1. P2P Payment Transaction Flow

### Narrative (Happy Path)

A client submits a transfer with a client-generated idempotency key. The API Gateway forwards it to the Transaction Orchestrator, which first checks Redis to confirm this request hasn't already been processed. Fraud detection runs synchronously and must return within 15ms. If approved, the Payment Processing Service debits the sender and credits the receiver using Optimistic Concurrency Control (a version-checked `UPDATE`), and writes the ledger entry plus an outbox event in the same database transaction. Only after all of this succeeds does the client receive a response. Notification delivery happens afterward, asynchronously, so a slow push-notification provider never adds latency to the payment itself.

### Synchronous vs. Asynchronous Points

| Step | Type | Why |
|---|---|---|
| Client → API Gateway → Orchestrator | Sync | Client is waiting for a definitive result |
| Idempotency check (Redis) | Sync | Must happen before any processing begins |
| Fraud evaluation | Sync | Must gate the transaction before funds move (15ms SLA) |
| Debit / credit / ledger write | Sync | Financial correctness requires the client to know the outcome |
| Outbox → Kafka publish | Async | Downstream consumers don't need to complete before the client gets a response |
| Kafka → Notification Service | Async | Notification delivery is not part of transaction correctness |

### Error Handling, Retries & Compensation (per failure scenario)

**Scenario 1 — Duplicate submission.** If a client retries after a network timeout using the same idempotency key, the Orchestrator finds the cached result in Redis and returns it directly without touching the Payment Processing Service again. No retry logic is even needed here — this is the intended fast path for handling client-side retries safely (FR-002, FR-003).

**Scenario 2 — Insufficient balance.** The balance check happens *before* any write occurs, so a failure here requires no compensation — the transaction is simply marked `FAILED` with a reason code, and the client receives a clear error. This is the cheapest failure to handle because nothing needs to be undone.

**Scenario 3 — Receiver's shard fails mid-transaction.** This is the scenario the ARB is most likely to probe (it mirrors sample question Q2). Because the sender's debit already committed before the receiver's shard became unreachable, the saga cannot simply return an error — it must run a **compensating transaction** that re-credits the sender for the exact amount debited. Only once compensation is confirmed does the Orchestrator mark the transaction `REVERSED` and inform the client. The key property: at every point in time, the sum of account balances remains conserved (Section A1.2's consistency guarantee) — money is never left "in limbo." Retries here are bounded: if the compensating credit itself fails (e.g., the sender's shard is also degraded), the failure is escalated to a P1 alert rather than retried indefinitely, since an un-compensated debit is a compliance-critical state.

---

## 2. Batch Merchant Settlement Flow

### Narrative

A nightly scheduled job pulls all unsettled merchant transactions (up to 100,000 per batch, per FR-006), groups them by merchant, and computes each merchant's net settlement amount. Each merchant's settlement is written as its own database transaction — not one giant transaction for the whole batch — specifically so that one merchant's failure doesn't block or roll back the other 99,999. A settlement-completed event is published per merchant so the Notification Service can inform them independently, decoupled from the settlement write itself.

### Synchronous vs. Asynchronous Points

| Step | Type | Why |
|---|---|---|
| Scheduler → Settlement Service trigger | Sync | Simple invocation, no downstream dependency |
| Per-merchant DB write (settlement record + balance update) | Sync | Needs a definitive commit/rollback outcome per merchant |
| Settlement Service → Kafka | Async | Merchant notification doesn't need to block the batch job |
| Kafka → Notification Service | Async | Same reasoning as the P2P flow |

### Error Handling, Retries & Compensation

Because each merchant's settlement is its own transaction, a failure (e.g., a transient DB error) only affects that merchant — it's flagged and automatically picked up in the next scheduled run, while the rest of the batch proceeds uninterrupted (partial-batch isolation). The entire job is designed to be **idempotent**: if the scheduler itself retries the whole job (e.g., after a crash), merchants already marked `SETTLED` are skipped, so no merchant is ever settled twice. This avoids needing any compensation logic at the batch level — the idempotency check *is* the safeguard.

---

## 3. System Failover Flow

### Narrative

When a database shard's primary node stops responding, two things happen in parallel: the application's circuit breaker (`CB-DB-PRIMARY`) trips after 2 failures within 5 seconds and immediately starts routing reads to the replica while queuing writes, rather than letting requests pile up waiting on a dead node; meanwhile, the health-monitoring system (e.g., Patroni) independently detects the missed heartbeats and promotes the replica to be the new primary. Once the connection pool's routing config is updated to point at the new primary, the circuit breaker's next probe request succeeds, it closes, and queued writes resume — all targeted to complete within the 30-second RTO (NFR-006).

### Synchronous vs. Asynchronous Points

| Step | Type | Why |
|---|---|---|
| Application write → circuit breaker | Sync | The calling service needs an immediate decision (fail fast vs. wait) |
| Health monitor heartbeat checks | Async (background) | Runs independently of any live request |
| Replica promotion | Async (background) | Takes measurable time (seconds); not tied to a specific user request |
| On-call alert | Async | Informational — doesn't block recovery |

### Error Handling, Retries & Compensation

The circuit breaker's queued writes are retried automatically once it moves back to `CLOSED`, and because every write carries an idempotency key, a write that partially executed before the failure (if any) is never double-applied on retry. The old primary is deliberately **not** allowed to automatically rejoin as primary once it recovers — it comes back only as a replica. This is a direct mitigation for FMEA item FM-004 (network partition / potential split-brain): automatically re-promoting a recovered node without verifying it isn't out of sync could cause two nodes to both believe they're primary, which is a far worse failure mode than a few extra seconds of degraded availability.
