# Load Testing Strategy: HTTPE — PayScale

**Executive Summary:** Defines all 8 required load test scenarios with concrete pass/fail criteria grounded in the system's actual NFRs, a performance budget that accounts for every stage of the critical path (deliberately excluding Kafka publishing, since that's asynchronous in this architecture), and the metrics used to diagnose failures during a run.

Performance budget: `load-tests/performance-budget.md`. Locust scripts for the 3 most important scenarios: `load-tests/scenarios/lt_002_target_load.py`, `lt_006_failover_under_load.py`, `lt_007_mixed_workload.py`.

## 1. Load Test Scenarios

| ID | Scenario | Duration | Target TPS | Warm-up | Cool-down | Pass/Fail Criteria |
|---|---|---|---|---|---|---|
| LT-001 | Baseline Performance | 30 min | 1,200 | 5 min ramp | 5 min ramp-down | p50 ≤50ms, p99 ≤100ms, error rate <0.1% — establishes the "current system" reference point |
| LT-002 | Target Load | 60 min | 12,000 | 10 min ramp | 10 min ramp-down | p50 ≤50ms, p99 ≤100ms, p99.9 ≤250ms, error rate <0.1%, **zero DB connection pool exhaustion** (validates the BN-001 fix), **zero deadlocks** (validates the BN-006 fix via OCC) |
| LT-003 | Burst Capacity | 15 min | 18,000 | 2 min ramp (simulating a real flash-sale spike, not instantaneous) | 5 min ramp-down | Same latency targets held for the full 15-minute window (matches NFR-001's stated burst duration exactly); error rate <0.5% (slightly relaxed — this is the genuine stress edge); no shard exceeds 100% of its assumed write-capacity ceiling (Day 6 capacity math) |
| LT-004 | Soak Test | 4 hours | 9,600 (80% of target) | 10 min | 10 min | No memory growth over the 4-hour window (validates the FM-010 memory-leak mitigation — heap-alerting threshold never trips); no gradual latency drift; connection pools stay healthy throughout |
| LT-005 | Ramp-Up | 30 min | 0 → 15,000 | N/A (the ramp IS the test) | 5 min | Exploratory, not pass/fail in the usual sense — the deliverable is the *documented inflection point* where p99 first exceeds 100ms; a "pass" requires that point to be at or above 12,000 TPS |
| LT-006 | Failover Under Load | 30 min | 12,000 | 10 min ramp | 5 min | Zero committed-transaction loss; p99 during the ~30s failover window may spike to ≤5s; full recovery to p99 ≤100ms within 30s of the kill (NFR-006 RTO); error rate during the window <5%, returns to <0.1% after |
| LT-007 | Mixed Workload | 60 min | 12,000 | 10 min ramp | 10 min | Same targets as LT-002, but validated under the realistic 60/20/10/5/5 traffic mix — catches issues specific to the balance-read cache path or merchant/reversal code paths that a 100%-P2P test (LT-002) wouldn't exercise |
| LT-008 | Hot Partition | 10 min | 5,000 | 1 min | 2 min | **Zero balance corruption** under worst-case single-row OCC contention (empirically validates the Day 8 correctness proof); average OCC retries per request stays bounded (<3) — a runaway retry count would indicate livelock risk, not just a performance concern |

### Notes on Specific Scenarios

**LT-006** is run as a scripted chaos event *during* live load (see the script's `schedule_chaos_injection` hook) rather than only as an isolated chaos experiment (`CE-001` in `docs/09-fault-tolerance.md`) — failover behavior under real concurrent traffic is a meaningfully different (and more realistic) test than failover against an otherwise-idle system.

**LT-008** exists specifically because Day 8's correctness argument, while mathematically sound, deserves empirical validation under the worst case the architecture is designed to handle — this is the test that would actually catch a bug in the OCC implementation before production does.

## 2. Metrics Collection Strategy

Every load test run correlates two sources of truth, tagged with a shared `run_id` label so they can be overlaid on the same time axis in Grafana:

1. **Load generator metrics** (Locust's own request/response histograms, error counts, and RPS) — the client-side view of what happened.
2. **Backend observability metrics** (Prometheus, per the metrics catalog in Section A5.6) — the server-side view of *why* it happened. The specific metrics watched during every run:

| Metric | What It Catches During a Load Test |
|---|---|
| `txn_processing_duration_seconds` (p50/p95/p99/p99.9) | The core pass/fail signal for every scenario above |
| `account_balance_update_retries` | Directly measures OCC contention — the key metric for LT-008 specifically |
| `db_connection_pool_active` | Would catch a BN-001 regression immediately (pool exhaustion) |
| `db_query_duration_seconds` | Distinguishes "the app is slow" from "the database is slow" |
| `kafka_consumer_lag` | Confirms async consumers (notification, reconciliation, audit) keep pace even while the synchronous path is under load |
| `circuit_breaker_state` | For LT-006 specifically — confirms `CB-DB-PRIMARY` actually trips and recovers on schedule |
| `redis_memory_utilization` | For LT-004 (soak) — catches a slow cache-memory leak that a short test wouldn't reveal |

A test run is only considered complete once both views are reconciled — a run that looks clean from the load generator's side but shows a metric like `db_connection_pool_active` creeping toward its ceiling is a warning sign even if latency targets were technically met, since it indicates the system was closer to its limit than the pass/fail numbers alone would suggest.

## 3. Dashboards Used Per Scenario

Per the five operational dashboards named in Section A5.6 (System Health Overview, Transaction Deep-Dive, Database Performance, Capacity Planning, Business Metrics): LT-001/LT-002/LT-003 primarily watch **System Health Overview** and **Transaction Deep-Dive**; LT-004 (soak) watches **Capacity Planning** for long-run trend lines; LT-006 watches **Database Performance** specifically for the failover window; LT-008 watches **Transaction Deep-Dive** filtered to the single hot account.
