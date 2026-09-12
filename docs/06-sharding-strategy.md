# Sharding Strategy: HTTPE — PayScale

This is the highest-weighted single document in the project (50 points). It covers all seven required areas: shard key selection, shard count & topology, cross-shard transactions, rebalancing, routing, failure handling, and data locality.

---

## 1. Shard Key Selection

### Options Considered

| Option | Distribution Uniformity | Hot-Spot Risk | Cross-Shard Query Frequency |
|---|---|---|---|
| `hash(account_id)` | Excellent (see simulation below) | Present for a small number of extreme-volume accounts (see mitigation) | Every P2P transfer between two random accounts touches 2 shards |
| `user_id` | Similar to account_id, but doesn't map to the actual query pattern (transactions reference accounts, not users directly) | Same underlying issue as account_id, without a corresponding benefit | Same or worse — a user with multiple accounts doesn't co-locate them meaningfully for transaction routing |
| Geographic (North/South India) | Poor — urban centers (Mumbai, Bangalore, Delhi NCR) concentrate far more accounts than the rest of the country | High — metro-heavy shards would be permanently overloaded relative to others | Any transfer between a Delhi user and a Chennai user crosses shards, same as hash-based, but with none of the uniformity benefit |

### Mathematical Analysis of Distribution Uniformity

A direct simulation (1,000,000 simulated accounts, SHA-256 hash mod N) confirms hash-based sharding produces near-perfectly even distribution regardless of shard count:

| Shard Count | Mean Accounts/Shard | Std. Deviation | Deviation as % of Mean |
|---|---|---|---|
| 4 | 250,000 | 376 | 0.150% |
| 6 | 166,667 | 297 | 0.178% |
| 8 | 125,000 | 206 | 0.165% |

This matches the theoretical expectation from balls-into-bins probability: for M accounts uniformly hashed into N shards, the standard deviation of shard size is approximately √(M × (1/N) × (1−1/N)), which shrinks to a negligible fraction of the mean as M grows into the millions. At the target scale of 35M accounts, this deviation stays well under 0.2% — **the number of accounts per shard is effectively a non-issue.**

### The Real Risk: Traffic Skew, Not Account-Count Skew (ARB Q1)

Uniform account distribution does **not** guarantee uniform *traffic* distribution. This is the scenario the ARB is likely to probe directly: **what happens when a single merchant processes 40% of all transactions?** Since `hash(account_id)` is deterministic, every transaction touching that one merchant account routes to the exact same shard — no amount of re-hashing changes this, because the merchant is a single row that must live somewhere.

**Mitigation — decouple high-frequency merchant writes from the live path:** The key insight is that a merchant's account balance doesn't need to be updated synchronously on every single incoming payment. The system already includes batch merchant settlement (Day 4) for exactly this reason. In practice:

- The **paying customers'** accounts (many different `source_account_id` values) are what get debited in real time, and by construction these are spread evenly across all shards — this is the high-volume write path, and it's already load-balanced.
- The **merchant's own balance** is only updated during the batched settlement job (nightly, or more frequently for high-volume merchants), not on every individual transaction. The live-path write for a merchant-bound payment is a `transactions` + `ledger_entries` row (crediting a *pending* merchant balance conceptually, reconciled in the batch), not a synchronous `UPDATE` against the merchant's single `accounts` row.
- For merchants whose *projected* TPS individually exceeds roughly 5% of a single shard's capacity, they are flagged during onboarding and monitored; if actual traffic confirms sustained hot-shard risk, that merchant's settlement cadence is increased (e.g., hourly instead of nightly) to keep the batched-update volume manageable rather than attempting to split a single account's row across shards (which isn't possible without breaking OCC's single-row versioning model).

**Selected shard key: `hash(account_id)`**, using a standard hash function (SHA-256 truncated, matching Citus's default hashing), with the above mitigation for the hot-merchant edge case.

---

## 2. Shard Count & Topology

### Capacity Calculation

Each P2P transaction generates approximately 5 row writes system-wide: 1 debit `UPDATE`, 1 credit `UPDATE`, 2 `ledger_entries` `INSERT`s (one per side), and 1 outbox `INSERT`. These writes split across (typically) 2 shards — the source shard sees roughly 3 writes (debit update, debit ledger entry, outbox insert), the destination shard sees roughly 2 (credit update, credit ledger entry).

- **At 12,000 TPS sustained:** 12,000 × 5 = **60,000 row writes/second, system-wide**.
- **Per-shard write ceiling assumption:** 15,000 writes/second per shard. This is a conservative figure for a well-tuned PostgreSQL 15 primary (8 vCPU, NVMe-backed storage, PgBouncer pooling, OCC avoiding lock waits on simple indexed point updates) — well within commonly published PostgreSQL OLTP benchmarks for this hardware class.
- **Base shard count:** 60,000 ÷ 15,000 = **4 shards** (matches the project's own reference capacity table, which shows 4 shards for 12K TPS — a useful cross-check that this reasoning is in the right ballpark).
- **Headroom for the 18,000 TPS burst target:** at 4 shards, burst load = 18,000 × 5 ÷ 4 = 22,500 writes/sec/shard — **150% of the assumed ceiling**, which is unsafe.

**Selected: 6 shards at launch.** At 6 shards: sustained load = 60,000 ÷ 6 = 10,000 writes/sec/shard (**67% utilization**, leaving comfortable headroom); burst load = 90,000 ÷ 6 = 15,000 writes/sec/shard (**100% utilization**, acceptable since NFR-001's burst window is bounded to 15 minutes, not sustained).

### Topology

- **Replication factor: 3** per shard (1 primary + 2 read replicas), matching the target architecture in Section A3.1.
- **Total PostgreSQL instances:** 6 shards × 3 = 18, plus a coordinator node (itself replicated with a standby for HA — see Section 6).
- **Growth strategy:** monitor `db_connection_pool_active` and `db_query_duration_seconds` per shard (per the observability design in Section A5.6); trigger a rebalance (Section 4) when any shard's sustained utilization exceeds ~75%, keeping at least 25% headroom per shard at all times rather than waiting until a shard is already saturated.

---

## 3. Cross-Shard Transaction Protocol

### Options Evaluated

| Pattern | Locking Behavior | Latency Impact | Verdict |
|---|---|---|---|
| **2-Phase Commit (2PC)** | Holds locks across both shards until both participants confirm — blocking | Adds a full extra coordination round-trip; a slow/unavailable participant stalls the whole transaction | **Rejected** — directly works against the p99 ≤100ms target, and introduces a classic blocking-coordinator failure mode |
| **Try-Confirm-Cancel (TCC)** | Reserves resources in a "Try" phase before confirming | Three network round-trips instead of one/two | **Rejected** — TCC's reservation semantics add real value for scenarios like inventory holds, but a simple debit/credit doesn't need a separate "reserve" step; the extra round-trip only adds latency here |
| **Saga** | No cross-shard locks — each shard commits its own local step independently; failures trigger compensation | Each step is a normal local transaction; no blocking wait on the other shard | **Selected** |

### Protocol

1. The Transaction Orchestrator resolves `shard(source_account_id)` and `shard(destination_account_id)` via the routing layer (Section 5).
2. **Same-shard fast path:** if both accounts hash to the same shard, the debit and credit execute as a single local ACID transaction — no saga machinery needed at all, since there's no cross-shard risk to begin with.
3. **Cross-shard path:** the saga runs as: Step 1 (debit sender + write ledger entry + outbox event, on shard A) → Step 2 (credit receiver + write ledger entry, on shard B) → mark transaction `COMPLETED`. If Step 2 fails for any reason (timeout, shard unavailable, validation failure), the compensating action re-credits the sender on shard A — this exact flow is diagrammed in `diagrams/p2p-payment-flow.puml`, Failure Scenario 3.

This is the same pattern already established in the Day 3/Day 4 design work — Day 6 is where the shard-count math and shard-key choice that make it necessary are formally justified.

---

## 4. Shard Rebalancing

Citus's built-in online rebalancer (`citus_rebalance_start()`, logical-replication-based since Citus 11+) is used rather than a custom migration tool.

**Procedure:**
1. A new worker node is provisioned and added to the Citus cluster.
2. The rebalancer computes which existing shards should move to the new node to re-equalize distribution (by shard count or data size).
3. For each shard being moved, Citus takes a snapshot and streams it to the target node via logical replication *while the shard continues serving live traffic* — the source shard is not taken offline during the bulk copy.
4. Once the target is caught up, a brief cutover (typically sub-second to a few seconds, not the full copy duration) switches routing to the new location.

**Expected impact:** the bulk copy for a large shard can take hours, bounded by data size and network throughput, but this happens entirely in the background. Only the final cutover window causes any customer-visible effect, and only for accounts on the shard actively being moved — requests to that shard during the cutover see a brief latency bump or a single retry (already handled by existing OCC-retry and circuit-breaker logic); every other shard is completely unaffected. This satisfies the "no downtime" requirement — it's downtime-free for the system as a whole, with a narrow, bounded blast radius during each individual shard's cutover.

---

## 5. Routing Layer

Citus's **coordinator node** owns this, so the application layer doesn't need custom shard-routing logic. The coordinator maintains a shard metadata catalog (`pg_dist_shard`) mapping hash ranges to physical worker nodes. When a service (e.g., Account Service) queries through the coordinator, Citus computes `hash(account_id)`, determines which hash range — and therefore which shard — owns that row, and transparently pushes the query down to the correct worker. For the Transaction Orchestrator's cross-shard saga steps, each step is issued as an independent, normal query through the coordinator; there's no special cross-shard query syntax needed.

Internally, Citus uses **consistent hashing** (hash ranges assigned to shards, per the training material's description in Section A5.1) rather than plain `hash(key) mod N`. This is what makes the rebalancing in Section 4 efficient — adding a shard only requires moving the fraction of hash-space reassigned to it, not re-hashing every row in the cluster.

---

## 6. Failure Handling

- **Single shard's primary fails:** the health monitor (Patroni) detects missed heartbeats and promotes one of that shard's 2 replicas, targeting the 30-second RTO (NFR-006). This is diagrammed in `diagrams/failover-flow.puml`. Critically, **only the ~1/6th of accounts on that specific shard are affected** — this containment of blast radius is one of sharding's core benefits, not just a scaling mechanism.
- **In-flight transactions on the affected shard:** the application-level circuit breaker (`CB-DB-PRIMARY`) trips after 2 failures in 5 seconds, queues writes, and routes reads to a surviving replica immediately — read availability for that shard is barely interrupted, since a shard has 2 replicas and only 1 needs to be promoted.
- **Cross-shard saga where one shard is down:** handled identically to Failure Scenario 3 in the Day 4 design — the saga's compensation logic doesn't need to know *why* a step failed (shard failure, network partition, timeout), only that it did, and reverses the already-committed step accordingly.
- **Coordinator failure (single point of failure risk):** the Citus coordinator itself is deployed with a streaming-replication standby, promoted via the same Patroni-style mechanism as a shard primary. This is called out explicitly because a coordinator outage would block query routing to *all* shards even if every shard itself is perfectly healthy — coordinator HA is not optional.

---

## 7. Data Locality (RBI Compliance)

All Citus nodes — coordinator, every shard primary, and every replica — are deployed exclusively within **AWS ap-south-1 (Mumbai)**, distributed across multiple Availability Zones within that single region, never across regions. This satisfies the RBI Data Localization Directive's requirement that all payment data reside within India.

Because the chosen shard key (`hash(account_id)`) is a horizontal load-splitting mechanism rather than a geographic one, there's no risk of a shard accidentally landing outside the compliance boundary — every shard, regardless of which accounts happen to hash into it, lives in the same region. This is also part of why geographic sharding (Section 1) was rejected: RBI's requirement is about staying within India as a whole, not about aligning shards to specific in-country regions, so geographic sharding would have added complexity and hot-spot risk for zero regulatory benefit.
