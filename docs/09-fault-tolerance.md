# Fault Tolerance: HTTPE — PayScale

State machine: `diagrams/circuit-breaker-fsm.puml`. Pseudocode: `pseudocode/circuit-breaker.py`, `pseudocode/idempotency-handler.py`.

## 1. Circuit Breaker Instances

All seven breakers share the identical state machine (`diagrams/circuit-breaker-fsm.puml`) — only the thresholds and fallback behavior differ per dependency, based on how critical the dependency is and how forgiving its fallback can be.

| Circuit Breaker | Protected Dependency | Failure Threshold | Reset Timeout | Fallback Behavior | Why this threshold |
|---|---|---|---|---|---|
| CB-FRAUD | Fraud Detection Service | 3 failures / 10s | 15s | Allow transaction with a manual-review flag | Blocking 100% of payments during a fraud-service blip would be worse than flagging some for later review — availability wins this specific trade-off |
| CB-NOTIFY | Notification Service | 5 failures / 30s | 60s | Queue notification for retry; transaction proceeds regardless | Notifications are entirely non-critical to payment correctness, so this breaker is the most tolerant of the seven |
| CB-DB-PRIMARY | Shard primary (writes) | 2 failures / 5s | 10s | Route reads to replica; queue writes | Tightest threshold — a struggling primary on the correctness-critical write path needs to be detected fast |
| CB-DB-REPLICA | Shard replica (reads) | 5 failures / 10s | 30s | Route reads to primary (rate-limited, to avoid overloading it) | Reads are less urgent than writes; more tolerance before tripping |
| CB-CACHE | Redis Cluster | 3 failures / 5s | 20s | Bypass cache, query DB directly | A cache failure degrades latency, not correctness — safe to fail open quickly |
| CB-EXCHANGE | Currency exchange-rate service | 3 failures / 10s | 30s | Use last known rate with a staleness warning | Rare dependency (only multi-currency transactions); a brief stale rate is an acceptable trade-off |
| CB-SETTLEMENT | Settlement Service | 2 failures / 5s | 60s | Queue settlement for the next batch cycle | Settlement already runs on a batch cadence, so deferring to the next cycle costs little |

**Note on interaction with bulkheads (Section 2):** a circuit breaker decides *whether* to call a dependency; a bulkhead decides *how many resources* a dependency's calls are allowed to consume even when the breaker is closed. They solve different problems — a breaker alone wouldn't have prevented BN-003 (thread pool saturation), because the pool can exhaust well before the failure *count* crosses a breaker's threshold if calls are merely slow rather than outright failing. This is why both are needed together.

## 2. Bulkhead Isolation

Bottleneck BN-003 (`40% request timeout rate` under load) was caused by every outbound call sharing one thread pool — a slow Fraud Detection Service call could starve threads that DB calls needed. The fix is **fully separate, dedicated pools per dependency**, sized independently, so exhaustion in one can never spill into another.

| Dependency | Pool Type | Size (per app instance) | Max Queue Depth | Call Timeout | Rationale |
|---|---|---|---|---|---|
| PostgreSQL (via PgBouncer) | DB connection pool | 40 | 0 — fail fast, no queueing | 200ms | Correctness-critical path; sized against the per-shard PgBouncer connection budget (2,000+ total ÷ ~50 app instances ≈ 40 each) |
| Redis (idempotency + balance cache) | Connection pool | 20 | 10 | 50ms | Sub-millisecond typical latency; modest queueing tolerated since Redis rarely saturates |
| Fraud Detection Service | Dedicated thread pool | 30 | 20 | 15ms (matches FR-007) | The specific dependency that caused BN-003 — isolated deliberately so a slow ML-inference call can never starve DB-bound threads |
| Outbox Relay → Kafka producer | Separate OS process, own I/O threads | 8 | Async 10MB buffer | 100ms produce timeout | Runs entirely outside the request-handling process, so it structurally cannot contend with request threads regardless of pool sizing |

Total request-path pool footprint per app instance: 40 (DB) + 20 (Redis) + 30 (Fraud) = 90 dedicated connections/threads — sized against a realistic per-instance capacity (per the capacity plan's app-tier sizing) with room left for the instance's own request-handling threads.

## 3. Retry Policies

| Operation | Retries | Backoff | Notes |
|---|---|---|---|
| OCC balance update (debit/credit) | 5 | Exponential + jitter, base 50ms | See `pseudocode/occ-balance-update.py` and Day 8's correctness proof |
| Kafka producer publish (outbox relay) | 5 (via `retries` config) | Kafka client's internal backoff | Idempotent producer (`enable.idempotence=true`) makes retries safe — no duplicate publishes |
| Notification delivery | 3 | 1s → 5s → 25s | Then to `transaction-events.notification.dlq` (Day 7) |
| Reconciliation consumer processing | 5 | 2s → 10s → 30s → 2min → 10min | Then to `transaction-events.reconciliation.dlq` |
| Audit & Compliance consumer processing | 2 | 1s → 5s | Then to `transaction-events.audit.dlq`, with an immediate P1 alert |
| Shard-failover queued writes | Replayed once circuit breaker returns to CLOSED | N/A (queued, not polled) | Idempotency keys make replay safe even if a write partially executed before the failure — see `diagrams/failover-flow.puml` |
| Circuit breaker HALF_OPEN probes | 1 probe at a time (configurable per breaker) | N/A — governed by `reset_timeout_seconds` | Prevents slamming a still-recovering dependency with full traffic immediately |

## 4. Idempotency Handling — the Concurrent-Duplicate Case

The Day 4 sequence diagram covers the *sequential* retry case (a client retries after its first request already finished). `pseudocode/idempotency-handler.py` also protects against a **concurrent** duplicate — two requests with the same idempotency key in flight at the same moment (e.g., a UI double-tap that fires two network requests almost simultaneously, before either has finished).

This works via Redis's atomic `SET ... NX` (set-if-not-exists): the first request to reserve a key writes an `IN_PROGRESS` sentinel; a second, concurrent request sees the key already exists, finds it's `IN_PROGRESS` rather than a finished result, and is rejected with a retryable error — it does **not** proceed to process the transaction a second time, and it does **not** simply wait silently, since we can't yet know if the first attempt will succeed. A bounded safety TTL (30s) on the `IN_PROGRESS` marker ensures that if the first request crashes before completing (and before its own cleanup runs), a legitimate later retry isn't blocked forever.

## 5. Chaos Engineering Experiments

Each experiment specifies a hypothesis, blast radius, injection method, expected behavior, success criteria, and rollback procedure — run first in a staging environment sized like production, before ever being considered for production game-days.

### CE-001: Kill the Primary Database Node of One Shard

- **Hypothesis:** The affected shard fails over to a replica within 30 seconds; in-flight transactions to that shard retry successfully after failover; zero committed transactions are lost.
- **Blast Radius:** One shard (~1/6th of accounts); all other shards unaffected.
- **Injection Method:** Forcefully terminate the primary Postgres process on one shard (e.g., `SIGKILL` via a chaos tooling agent).
- **Expected Behavior:** `CB-DB-PRIMARY` trips within 5s; Patroni promotes a replica within 30s; queued writes replay successfully; `diagrams/failover-flow.puml` describes the full sequence.
- **Success Criteria:** Zero committed-transaction loss; p99 latency during the incident stays under 5 seconds; full recovery (writes flowing normally again) within 30 seconds.
- **Rollback Procedure:** Restart the killed node as a new replica (per ADR/HLD, it never automatically rejoins as primary); confirm cluster health via `db_connection_pool_active` and replication-lag metrics before considering the experiment closed.

### CE-002: Inject 500ms Network Latency Between App Tier and Kafka

- **Hypothesis:** Producer retries increase and consumer lag grows, but no message is lost, and lag recovers fully within 2 minutes of the fault being removed.
- **Blast Radius:** All Kafka-dependent async flows (notifications, reconciliation, audit) — the synchronous payment path is unaffected, since it doesn't call Kafka directly (the outbox relay does).
- **Injection Method:** Network-level latency injection (e.g., `tc netem` on the outbox relay's outbound path to the Kafka brokers) for a fixed 10-minute window.
- **Expected Behavior:** `kafka_consumer_lag` rises but stays under the 10,000-message alert threshold for at most a few minutes; no DLQ messages are produced by this fault alone.
- **Success Criteria:** Zero message loss (verified by comparing outbox row count to consumed-event count afterward); consumer lag returns to baseline within 2 minutes of latency removal.
- **Rollback Procedure:** Remove the `tc netem` rule; verify lag recovery; confirm no `transaction-events.*.dlq` topics received messages attributable to this window.

### CE-003: Saturate the Redis Cache (Force Evictions)

- **Hypothesis:** Cache miss rate spikes and DB read load increases 3-5x, `CB-CACHE` may trip, but p99 latency for balance reads stays under 200ms throughout via the direct-DB fallback.
- **Blast Radius:** Balance display reads and idempotency checks; the OCC write path is unaffected since it never reads from cache.
- **Injection Method:** Flood the Redis Cluster with large synthetic keys until it's forced to evict under memory pressure.
- **Expected Behavior:** `CB-CACHE` trips per its 3-failures/5s threshold; reads fall back to querying PostgreSQL directly; DB connection pool utilization rises but stays under its configured ceiling (Section 2).
- **Success Criteria:** DB connection pool never exceeds its 40-connection-per-instance limit; p99 read latency during degradation stays under 200ms; no cascading failure into the write path.
- **Rollback Procedure:** Stop the synthetic key flood; flush the polluting keys; confirm `CB-CACHE` returns to CLOSED and hit ratio recovers to its normal ~92% baseline.

### CE-004: Kill 50% of Payment Processing Service Pods

- **Hypothesis:** Remaining pods absorb the load; the auto-scaling group provisions replacement pods within 90 seconds (NFR-012's cold-start target); error rate during recovery stays under 5%.
- **Blast Radius:** Payment Processing Service only — other services' pods are untouched.
- **Injection Method:** Force-terminate half of the running Payment Processing Service pods simultaneously (e.g., via a Kubernetes chaos tool).
- **Expected Behavior:** The load balancer routes around terminated pods immediately (health checks fail fast); the auto-scaler detects reduced capacity and provisions replacements; some requests to terminated pods mid-flight fail and are retried by the client using the same idempotency key, which `pseudocode/idempotency-handler.py` handles safely even under concurrent retry.
- **Success Criteria:** Error rate stays under 5% during the recovery window; replacement pods reach full serving capacity within 90 seconds; zero duplicate transactions processed (validated via idempotency-key audit).
- **Rollback Procedure:** Confirm the auto-scaler has restored the full pod count; monitor for 15 minutes post-recovery to confirm no residual elevated error rate.

### CE-005: Introduce 5-Second Clock Skew Between Two Services

- **Hypothesis:** Any timestamp-based validation (e.g., idempotency-key TTL edge cases, JWT expiry checks) may behave inconsistently, but no duplicate transactions are processed and the system self-heals once clocks resync.
- **Blast Radius:** Services performing timestamp comparisons — primarily the API Gateway's JWT validation and any TTL-adjacent logic.
- **Injection Method:** Adjust the system clock on one service's container by +5 seconds relative to the rest of the fleet (e.g., via `faketime` or a container-level clock-skew tool).
- **Expected Behavior:** A small number of requests near a token's expiry boundary may be rejected and require re-authentication; no financial-correctness logic is affected, since balance/version checks depend on database-assigned values, not wall-clock time on any individual service.
- **Success Criteria:** Zero duplicate transactions processed during the skew window; any elevated auth-failure rate returns to baseline within one clock-sync cycle (typically under a minute with NTP) after the skew is removed.
- **Rollback Procedure:** Remove the clock-skew injection; confirm NTP resync; verify auth failure rate returns to baseline.
