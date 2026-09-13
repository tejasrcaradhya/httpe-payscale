# Concurrency Control: HTTPE — PayScale

Pseudocode: `pseudocode/occ-balance-update.py`, `pseudocode/saga-orchestrator.py`

## 1. The OCC Mechanism

Every balance mutation follows the same pattern: read `available_balance` and `version`, compute the new balance in application code, then write it back with `UPDATE ... WHERE account_id = X AND version = <version we read>`. If the row's version has changed since the read, the `UPDATE` affects zero rows — someone else committed a change first — and the operation retries with a fresh read. No lock is ever held while the application is "thinking"; the only thing that can block is the instant of the `UPDATE` statement itself.

## 2. Formal Correctness Argument (Double-Spend & Write-Skew Prevention)

**Claim:** No sequence of concurrent transactions using this protocol can drive an account's `available_balance` negative, and no concurrent update can silently overwrite another without being detected.

**Argument:**

1. Every account row carries a `version` counter, incremented by exactly 1 on every successful update.
2. A transaction reads `(balance, version) = (B, V)` at some point in time.
3. It computes a new balance and attempts `UPDATE accounts SET available_balance = B', version = V+1 WHERE account_id = X AND version = V`.
4. PostgreSQL evaluates that `WHERE` clause against the **current committed state of the row at the moment the `UPDATE` executes** — not the snapshot the transaction read in step 2. This is a property of the database engine, not of our application code: an `UPDATE`'s `WHERE` clause always sees the latest committed row version under READ COMMITTED isolation (each *statement* gets its own fresh read).
5. Therefore, if any other transaction committed a change to this row between steps 2 and 4, that transaction's `version` bump means `version = V` is now false, and the `UPDATE` in step 4 matches zero rows — atomically, with no race window, because the check-and-write happens as one indivisible database operation.

**Worked example — the exact write-skew scenario from Section A5.4:** Account A has `balance = 1000, version = 5`. Two concurrent transactions, T1 (debit 800) and T2 (debit 600), both read `(1000, 5)`.

- T1 commits first: `UPDATE ... SET available_balance=200, version=6 WHERE account_id=A AND version=5` → matches 1 row, succeeds.
- T2 then attempts: `UPDATE ... SET available_balance=400, version=6 WHERE account_id=A AND version=5` → the row's version is now 6, not 5, so this matches **zero rows**.
- T2's code sees `rows_affected == 0`, detects the conflict, and retries: it re-reads the row, now seeing `(200, 6)`. It recomputes `200 - 600 = -400`, which fails the `new_available < 0` check **before any write is even attempted**, and T2 is correctly rejected with `INSUFFICIENT_BALANCE`.
- **Result: the account never goes below zero, and no update is silently lost** — T2's attempt is either retried against fresh data or explicitly rejected, never blindly applied on top of stale data. This is the direct answer to ARB sample question Q5.

**Termination:** retries are bounded (`MAX_RETRIES = 5`, exponential backoff with jitter). Under the low-contention conditions typical of P2P transfers — a given account is rarely the target of many simultaneous transactions — this converges almost immediately in practice. In the rare high-contention case (many simultaneous transfers to one hot account), retries are exhausted and a `ConcurrencyConflictError` is surfaced to the saga rather than the system hanging indefinitely; the saga then either fails the transaction cleanly or triggers compensation if this was the second leg of a cross-shard transfer.

## 3. Isolation Level Selection Per Query Type

| Query Type | Isolation Level | Rationale |
|---|---|---|
| Balance debit/credit (OCC `UPDATE`) | READ COMMITTED | The version-column check in the `WHERE` clause is what actually prevents write-skew here (see proof above) — this is a deliberate choice of application-level OCC *instead of* relying on the database's SERIALIZABLE isolation, since READ COMMITTED is lower-overhead and doesn't produce the serialization-failure retry storms SERIALIZABLE can under high contention |
| Balance reads (pre-transaction check) | REPEATABLE READ | Ensures the "read balance, then decide" window sees one consistent snapshot without holding a lock |
| Settlement batch (multi-row aggregation per merchant) | SERIALIZABLE | This is genuinely a multi-row aggregation, not a single-row CAS — it benefits from the database's full anomaly protection, and runs as a bounded nightly batch job where SERIALIZABLE's retry overhead is an acceptable trade-off for correctness |
| Dashboard / notification checks | READ COMMITTED | Low-stakes reads where slightly stale data is fine, and lowest overhead is preferred |
| Fraud rule evaluation | REPEATABLE READ | Needs a consistent view of velocity-check data across its (short) evaluation window without holding locks |
| Reconciliation / audit queries | REPEATABLE READ (Postgres's MVCC snapshot) | Long-running analytical queries get a stable snapshot so they never see partially-committed in-flight transactions, without blocking live writes |

## 4. Distributed Locking Strategy (Fencing Tokens)

OCC handles single-row balance contention, but a few operations need **mutual exclusion across an entire process**, not just a single row — specifically, ensuring only one instance of the nightly settlement batch job runs at a time, and ensuring only one shard-rebalance operation runs against the cluster at once. For these, we use a Redis-based lock modeled on the Redlock pattern, with **fencing tokens** to close the gap that a naive lock leaves open.

**The problem a plain lock doesn't solve:** a process acquires a lock with a TTL, then pauses unexpectedly (e.g., a garbage-collection pause or a slow disk I/O) for longer than the TTL. The lock expires and is granted to a second process, which does its work and releases it. The first process then resumes, unaware it lost the lock, and proceeds to write — corrupting state that the second process already updated. A lock alone cannot prevent this, because the danger is a *stale* holder acting after its lock has already moved on, not two holders acquiring the lock simultaneously.

**The fix:** every time the lock is granted, Redis also returns a monotonically increasing **fencing token** (a simple `INCR` on a dedicated counter key). The resource being protected — here, the settlement job's write path — is required to include this token with its writes, and rejects any write carrying a token *lower* than the highest one it has already seen. A zombie process resuming after losing its lock will present an old, smaller token, and its write is rejected even though it still believes it holds the lock.

```
token = redis.incr("fencing:settlement-job")
acquired = redis.set("lock:settlement-job:2026-09-13", token, nx=True, px=300000)  # 5 min TTL
if acquired:
    run_settlement_batch(fencing_token=token)
    # inside run_settlement_batch, every write checks:
    #   if token < last_seen_token: reject and abort
    #   else: proceed, and record token as last_seen_token
```

**Defense in depth:** even if the fencing mechanism were somehow bypassed, the `UNIQUE (merchant_account_id, settlement_date)` constraint on `merchant_settlements` (Day 5) is a second, independent backstop — a genuine double-settlement attempt fails at the database level regardless of what the locking layer did or didn't catch. The distributed lock exists primarily to avoid *wasted duplicate work*, not as the sole correctness guarantee.

## 5. Saga Orchestrator

See `pseudocode/saga-orchestrator.py` for the full implementation sketch. Key properties:

- **Same-shard transfers skip the saga entirely** — a single local ACID transaction handles both the debit and credit, since there's no cross-shard risk to manage (Day 6).
- **Every state transition is persisted to `transaction_events` before the corresponding action executes**, not after — this is what makes `recover_incomplete_sagas()` possible: on orchestrator restart, any saga that never reached a terminal state (`COMPLETED`/`FAILED`/`REVERSED`) is found by querying for exactly that gap, and its full event history is replayed to determine what needs to happen next, rather than trusting any in-memory state that was lost in the restart.
- **Compensations run in strict reverse order** of the steps that actually committed, matching the P2P Failure Scenario 3 diagram in `diagrams/p2p-payment-flow.puml`.
