# ADR-003: Shard by hash(account_id) into 6 Citus Shards with Saga-Based Cross-Shard Transactions

**Status:** Accepted

## Context

ADR-002 established PostgreSQL + Citus as the database layer but deferred the shard key, shard count, and cross-shard transaction protocol to this ADR, since those decisions depend on quantitative capacity analysis rather than the qualitative technology comparison done there. The system must sustain 12,000 TPS with bursts to 18,000 TPS (NFR-001), keep p99 latency under 100ms (NFR-003), and — per the CTO's brief and the ARB's own sample questions — must have a specific, defensible answer for what happens when a single high-volume merchant concentrates a large share of transaction volume, since a naive hash-based shard key routes all of that merchant's activity to one shard regardless of how many shards exist in total.

## Decision

Shard by **`hash(account_id)`** across **6 shards**, each with a replication factor of 3 (1 primary + 2 read replicas), for 18 total PostgreSQL instances plus a replicated coordinator. This shard count is derived from a bottom-up capacity calculation: 12,000 TPS generates roughly 60,000 row writes/second system-wide (5 writes per transaction: 2 balance updates, 2 ledger entries, 1 outbox insert), and at a conservative 15,000 writes/second-per-shard ceiling, 6 shards keeps sustained utilization at 67% with the 18,000 TPS burst target reaching but not exceeding 100% of that ceiling for its bounded 15-minute window. A direct simulation of 1,000,000 hashed account IDs confirmed distribution uniformity within 0.2% of the mean regardless of shard count, so account-count skew is not a concern — the real risk is *traffic* skew from a small number of extreme-volume accounts (see Consequences).

Cross-shard P2P transfers use the **Saga pattern**: same-shard transfers execute as a single local ACID transaction (no saga overhead needed); cross-shard transfers execute the debit and credit as independent local transactions per shard, with a compensating re-credit if the second step fails. Shard routing is handled transparently by the Citus coordinator's hash-range catalog, requiring no custom application-level routing logic. Rebalancing (when adding shards as the platform grows toward the 35M-user projection) uses Citus's online, logical-replication-based rebalancer, which moves data in the background and only requires a brief per-shard cutover window rather than full-system downtime.

## Alternatives Considered

| Shard Key Option | Verdict |
|---|---|
| `user_id` | Rejected — doesn't map to the transaction routing pattern (transactions reference accounts, not users directly) and carries the identical hot-account risk as `account_id` without a corresponding benefit |
| Geographic (North/South India) | Rejected — urban account concentration (Mumbai, Bangalore, Delhi NCR) creates permanent, structural hot shards; also adds complexity for zero RBI compliance benefit, since the residency requirement is India-wide, not region-specific within India |

| Cross-Shard Protocol Option | Verdict |
|---|---|
| Two-Phase Commit (2PC) | Rejected — holds cross-shard locks for the duration of the coordination round-trip, directly conflicting with the p99 ≤100ms target, and introduces a blocking-coordinator failure mode |
| Try-Confirm-Cancel (TCC) | Rejected — its reservation semantics add value for scenarios needing resource holds (e.g., inventory); a straightforward debit/credit doesn't need a third phase, and the extra round-trip only costs latency here |

## Consequences

**Positive:** The capacity math is grounded and cross-checked against the project's own reference capacity table (which independently shows 4 shards as the no-headroom baseline for 12K TPS — our 6-shard figure adds the burst/operational headroom that reference number doesn't account for). Failure blast radius is contained to roughly one-sixth of accounts per shard outage rather than the whole system. Citus's coordinator-managed routing means no custom shard-routing code needs to be written or maintained.

**Trade-offs / accepted risk:** A single extreme-volume merchant account (the ARB's likely probe question) still hashes to exactly one shard — this is mitigated by decoupling the merchant's *own* balance updates from the live transaction path via the existing batch settlement mechanism (Day 4), rather than by any sharding trick, since a single account's row fundamentally cannot be split across shards without breaking the OCC version-counter model. This is a deliberate, documented trade-off, not an oversight: if monitoring shows a specific merchant approaching hot-shard territory, their settlement cadence is tightened (e.g., hourly instead of nightly) rather than attempting to re-architect the shard key. The Citus coordinator is a structural single point of failure for query routing and is given its own HA standby to mitigate this, at the cost of one more component to operate.

## Compliance

All 6 shards, their replicas, and the coordinator are deployed exclusively within AWS ap-south-1 (Mumbai) across multiple Availability Zones, satisfying the RBI Data Localization Directive. Because sharding here is a load-distribution mechanism rather than a geographic one, no shard can end up outside the compliance boundary regardless of which accounts hash into it.
