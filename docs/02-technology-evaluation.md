# Technology Evaluation: HTTPE — PayScale

**Executive Summary:** Compares three technology options each for the database, message queue, and cache layers against performance, cost, operational complexity, team fit, and community support. Selects PostgreSQL+Citus, Apache Kafka, and Redis Cluster — decisions formalized in `adrs/001-message-queue-selection.md` and `adrs/002-database-selection.md`.

## Methodology

Each candidate technology is scored 1–5 (5 = best fit) across five criteria: **Performance** (ability to hit 12,000+ TPS / sub-100ms p99), **Operational Complexity** (lower score = harder to run), **Cost** (fit within the $45,000/month ceiling), **Team Expertise Compatibility** (existing team is Java/Kotlin + PostgreSQL), and **Community Support** (documentation, maturity, troubleshooting resources). Raw totals are a starting signal, not the final word — where a hard non-functional requirement (like NFR-001's 12,000 TPS floor) is at stake, that criterion is weighted more heavily in the final decision, explained under each table.

---

## 1. Database Layer: PostgreSQL+Citus vs. CockroachDB vs. TiDB

| Criterion | PostgreSQL + Citus | CockroachDB | TiDB |
|---|---|---|---|
| Performance | 4 | 4 | 4 |
| Operational Complexity | 3 | 3 | 2 |
| Cost | 4 | 3 | 3 |
| Team Expertise Compatibility | 5 | 2 | 1 |
| Community Support | 4 | 4 | 3 |
| **Total (/25)** | **20** | **16** | **13** |

**Why the gap is bigger than it looks:** CockroachDB and TiDB are both capable distributed databases, but the team's only production database expertise is PostgreSQL. Citus is an extension *on top of* standard PostgreSQL 15 — the SQL dialect, tooling, and operational knowledge the team already has carries over almost unchanged. CockroachDB and TiDB both require learning a new engine's query planner, indexing behavior, and failure modes inside a 15-day build window, which is a real execution risk on a hard deadline.

**Selected: PostgreSQL + Citus.** Full reasoning in `adrs/002-database-selection.md`.

---

## 2. Message Queue: Apache Kafka vs. RabbitMQ (clustered) vs. Amazon SQS

| Criterion | Kafka | RabbitMQ (clustered) | Amazon SQS |
|---|---|---|---|
| Performance | 5 | 2 | 3 |
| Operational Complexity | 2 | 4 | 5 |
| Cost | 3 | 4 | 2 |
| Team Expertise Compatibility | 2 | 3 | 3 |
| Community Support | 5 | 4 | 3 |
| **Total (/25)** | **17** | **17** | **16** |

**Why Kafka wins despite a tied raw score:** The raw scores are close, but Performance is not just "one of five criteria" here — it's a hard, non-negotiable requirement (NFR-001: 12,000 TPS sustained, bursting to 18,000). RabbitMQ has *already* been load-tested and shown to exhaust at 2,000 TPS with 30-second consumer lag (bottleneck BN-002) — it's not a hypothetical weakness, it's the system currently in front of us. SQS's FIFO throughput ceiling (roughly 3,000 msg/s with batching) would require splitting traffic across many queues just to reach target load, adding complexity that erases its "fully managed" advantage, and per-request pricing at sustained 12,000 TPS gets expensive fast. Kafka's partitioned log model is also a much more natural fit for the exactly-once semantics (FR-003) and immutable audit trail (FR-004) requirements, since events can be replayed rather than just consumed once and discarded.

**Selected: Apache Kafka.** Full reasoning in `adrs/001-message-queue-selection.md`.

---

## 3. Cache Layer: Redis Cluster vs. Memcached vs. Hazelcast

| Criterion | Redis Cluster | Memcached | Hazelcast |
|---|---|---|---|
| Performance | 5 | 4 | 4 |
| Operational Complexity | 3 | 4 | 3 |
| Cost | 4 | 4 | 3 |
| Team Expertise Compatibility | 4 | 2 | 3 |
| Community Support | 5 | 3 | 3 |
| **Total (/25)** | **21** | **17** | **16** |

**Why:** The team already runs single-node Redis today, so a Redis Cluster is an incremental step rather than a new tool. It's also the only option with native atomic compare-and-swap (WATCH/MULTI/EXEC) needed for idempotency-key checks (FR-002) and built-in TTL support, pub/sub for notification fan-out, and per-tier rate-limit counters (BASIC/PREMIUM/MERCHANT tiers from the accounts schema). Memcached has no persistence or native CAS; Hazelcast is JVM-native (a plus for the Java/Kotlin team) but has a smaller community and less precedent in this exact fintech caching pattern.

**Selected: Redis Cluster (6 nodes, 96GB total)** — matches the target architecture already specified in the scenario data (Section A3.1).

---

## Summary of Picks Feeding Into Day 2 ADRs

| Component | Selected Technology | ADR |
|---|---|---|
| Message Queue / Event Bus | Apache Kafka (3-broker cluster) | ADR-001 |
| Database Layer | PostgreSQL 15 + Citus | ADR-002 |
| Cache Layer | Redis Cluster (6 nodes) | *(ADR to follow on Day 8 — Cache Invalidation Strategy)* |
