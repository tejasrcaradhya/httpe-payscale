# ADR-002: Use PostgreSQL 15 + Citus over CockroachDB and TiDB for the Database Layer

**Status:** Accepted

## Context

The current database layer is a single PostgreSQL 15 primary with a 200-connection ceiling. Load testing (bottleneck BN-001) shows connection pool exhaustion and a query queue depth above 500 at just 1,800 TPS — below today's actual peak load, let alone the 12,000+ TPS target (NFR-001). The same load test also surfaced row-level lock contention on the accounts table producing a 15% deadlock rate (BN-006), pointing to a second, related problem: the database's concurrency model, not just its connection capacity, needs to change.

Any database choice must satisfy strict latency targets (p50 ≤30–50ms, p99 ≤100ms), ACID guarantees for every P2P transfer (FR-001), and the RBI Data Localization Directive, which requires all data to remain within India. It must also fit inside the $45,000/month infrastructure ceiling and, critically, be buildable and defensible within a 15-day timeline by a team whose only production database experience is PostgreSQL — a constraint stated directly in the CTO's mission brief (Section A3.3).

## Decision

Adopt PostgreSQL 15 with the **Citus extension** for horizontal sharding, rather than migrating to a different distributed database engine. The cluster is deployed as a coordinator plus N worker shards (the exact shard count is calculated in the Day 6 sharding strategy, based on per-shard TPS headroom), each shard running with a primary and two read replicas inside AWS ap-south-1 (Mumbai) to satisfy data residency requirements. **PgBouncer** is placed in front of the cluster in transaction-pooling mode, multiplexing thousands of application-tier connections down to a bounded number of real database connections per shard — directly resolving BN-001, since the current 200-connection hard ceiling was the proximate cause of that bottleneck, not the underlying hardware.

Table design follows the schema reference in Section A4.4: `account_id` uses UUIDv7 for time-ordered distribution, with `hash(account_id) mod N` as the shard-routing key. To resolve the lock-contention bottleneck (BN-006), balance updates use **Optimistic Concurrency Control** via a `version` column rather than `SELECT ... FOR UPDATE` row locks — conflicting writes become rare, cheap retries instead of blocking waits, which removes lock contention as a throughput ceiling entirely for the low-conflict-rate P2P workload this system mostly handles.

## Alternatives Considered

| Dimension | PostgreSQL + Citus | CockroachDB | TiDB |
|---|---|---|---|
| SQL dialect | Native PostgreSQL — zero translation needed | Postgres wire-compatible, but query planning/indexing behavior differs meaningfully underneath | MySQL-compatible — a different dialect entirely from the team's PostgreSQL background |
| Team ramp-up | Minimal — same tools, same tuning knowledge | Moderate–high — distributed transaction model and planner are new concepts | High — new dialect and a multi-component architecture (PD, TiKV, TiDB server) |
| Licensing | Fully open source | Core is open source; several availability/multi-region features are Enterprise-licensed | Open source |
| Operational footprint | One extension on a database the team already runs | New system, but single binary per node | Three distinct services to deploy and monitor |

CockroachDB was rejected mainly on team-expertise grounds: despite Postgres wire compatibility, its distributed transaction model and query planner behave differently enough that the team's existing performance-tuning experience doesn't transfer cleanly, and some of the high-availability features relevant at this scale sit behind an Enterprise license, complicating budget planning under time pressure. TiDB was rejected for a similar but larger reason — it is MySQL-compatible, the single biggest mismatch against the team's stated PostgreSQL expertise, and it introduces three new operational components (PD, TiKV, TiDB server) to learn and run inside a 15-day build window. Citus, by contrast, is additive: the database the team already knows continues to work almost unchanged, with sharding as a layer on top rather than a wholesale replacement.

## Consequences

**Positive:** This is the fastest realistic path to a working sharded system inside 15 days, since it reuses the team's existing PostgreSQL tooling (monitoring, backup/replication patterns, query tuning knowledge) rather than requiring a new database engine to be learned from scratch. It fully satisfies RBI data residency, since the entire cluster is self-managed within the Mumbai region.

**Trade-offs / accepted technical debt:** Citus is an extension, not a database engineered from the ground up for distribution — certain cross-shard operations (particularly joins and multi-account transfers spanning two shards) require deliberate application-level handling rather than being transparently distributed. This is addressed directly in the Day 6 cross-shard transaction protocol, which evaluates 2PC, Saga, and TCC patterns rather than assuming cross-shard queries "just work." Shard rebalancing, while supported online by Citus, still introduces transient load during the operation and must be tested before go-live rather than assumed safe.

## Compliance

All Citus coordinator and worker nodes are deployed exclusively within AWS ap-south-1 (Mumbai), satisfying the RBI Data Localization Directive (2018) and the Payment Aggregator Guidelines (2020) requirement that payment data remain within India. AES-256 encryption at rest and TLS 1.3 in transit satisfy PCI-DSS v4.0. Standard PostgreSQL write-ahead logging combined with the append-only `ledger_entries` table satisfies the RBI requirement for a complete, immutable audit trail of every transaction state transition (FR-004).
