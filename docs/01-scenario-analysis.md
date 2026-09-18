# Scenario Analysis: PayScale Financial Technologies — HTTPE Redesign

**Executive Summary:** PayScale must scale from 1,200 to 12,000+ TPS in 15 days. This document identifies the five most urgent bottlenecks from the load test report and proposes preliminary fixes for each, setting the direction for every design decision that follows in this repository.

## Context

PayScale Financial Technologies is a Series-B neo-banking startup in Mumbai serving 12 million monthly active users across P2P payments, merchant processing, micro-lending, and investment management. The current transaction processing system runs on a single PostgreSQL 15 primary, a single-node RabbitMQ instance, 8 fixed EC2 m5.2xlarge application servers, and a single-node 16GB Redis cache. It sustains roughly 1,200 TPS with 85ms median and 450ms p99 latency, at 99.9% availability, on approximately $12,000/month of infrastructure spend.

The Diwali festive-season campaign launches in 15 days. Marketing has committed to cashback promotions, flash sales, and partner merchant discounts intended to drive 10x user engagement, pushing projected peak load to 12,000+ TPS with bursts to 18,000 TPS. Simulated load testing confirms the current architecture cannot absorb this: connection pools exhaust and lock contention spikes well below even today's peak, let alone the target. A ground-up redesign — not incremental tuning — is required, to be presented to the Architecture Review Board on Day 15.

## Key Challenges

**Throughput and latency, simultaneously.** The system must scale 10x in raw throughput while tightening latency targets (p50 to ≤30–50ms, p99 to ≤100ms, p99.9 to ≤250ms) rather than merely holding latency steady. Scaling the application tier alone will not achieve this; the database and messaging layers are the binding constraints.

**Data layer as the primary bottleneck.** The single PostgreSQL primary exhausts its connection pool at 1,800 TPS — below even today's peak — and shows a 15% deadlock rate under row-level locking on the accounts table. This points to two related fixes: horizontal sharding to remove the single-primary ceiling, and a move from pessimistic locking to optimistic concurrency control to remove lock contention as a throughput limiter.

**Messaging layer replacement.** RabbitMQ's single-node design cannot sustain consumer throughput past 2,000 TPS. Apache Kafka's partitioned, multi-broker model fits the target load and also provides the durable event log needed for saga orchestration and audit-grade event sourcing.

**Availability jump.** Moving from 99.9% (8.76 hours/year downtime) to 99.99% (52.6 minutes/year) is a step change, not a tuning exercise. It requires automated failover, circuit breakers around every downstream dependency, and a 30-second RTO for single-component failures.

**Regulatory constraints.** RBI's data localization directive requires all data to remain within India, shaping cloud region and sharding choices. Two-year hot data retention, immutable audit trails, and real-time fraud checks within a 15ms budget must be designed in from the start, not bolted on later.

**Budget ceiling.** Infrastructure spend must stay within $45,000/month — a 3.75x increase over current spend to support a 10x throughput increase — meaning the architecture must be genuinely more efficient per transaction, not just bigger.

**Timeline pressure.** Fifteen days for a complete redesign, documentation, and defense favors pragmatic, well-justified technology choices (Kafka, sharded PostgreSQL/CockroachDB, OCC, saga pattern) over novel or unproven approaches, with every decision captured in an ADR the ARB can interrogate.

---

## Top 5 Bottlenecks (from Load Test Bottleneck Report, Section A3.2) & Preliminary Solutions

| # | ID | Component | Observation | Severity | Preliminary Solution |
|---|-----|-----------|-------------|----------|----------------------|
| 1 | BN-001 | PostgreSQL Primary | Connection pool exhaustion at 1,800 TPS; query queue depth >500 | CRITICAL | Introduce PgBouncer with transaction-level pooling to multiplex client connections down to a manageable DB connection count; move off a single primary onto a horizontally sharded PostgreSQL (Citus) or distributed SQL engine (CockroachDB/TiDB) so writes are distributed across shards instead of bottlenecked on one node. Raising `max_connections` is a stopgap, not a fix. |
| 2 | BN-002 | RabbitMQ | Message ack backlog causing consumer lag >30s at 2,000 TPS | CRITICAL | Replace single-node RabbitMQ with a multi-broker Apache Kafka cluster. Kafka's partitioned log model allows many parallel consumers per topic instead of RabbitMQ's single-node queue ceiling. Size partition counts to match target consumer-group parallelism for the transaction-events topic. |
| 3 | BN-006 | Database Locks | Row-level contention on accounts table; 15% deadlock rate | CRITICAL | Replace pessimistic row locking (`SELECT ... FOR UPDATE`) with Optimistic Concurrency Control using a version column on `accounts`. Conflicts become rare retries with backoff instead of blocking waits, removing lock contention as a throughput ceiling — well suited since most P2P transfers don't actually contend on the same account. |
| 4 | BN-003 | Application Layer | Thread pool saturation; 40% request timeout rate | HIGH | Move from 8 fixed instances to an auto-scaling group (8–64 instances). Decouple long-running work (fraud checks, notifications) onto async Kafka consumers so request-handling threads aren't blocked on downstream calls. Add circuit breakers on downstream dependencies so one slow service can't exhaust the thread pool. |
| 5 | BN-004 | Redis Cache | Hit ratio drops from 92% to 61%; memory evictions under load | HIGH | Move from single-node Redis (16GB) to a 6-node Redis Cluster (96GB total) to remove single-node eviction pressure. Review TTLs and cache-key granularity (e.g., cache balance reads separately from full account objects) to shrink per-entry footprint and improve effective hit ratio under load. |

**Note:** BN-005 (API Gateway rate limiter misconfiguration, MEDIUM) and BN-007/BN-008 (cross-AZ latency, log ingestion lag, MEDIUM/LOW) are deferred from this initial top-5 but will be addressed in the Fault Tolerance (Day 9) and Observability design work, since they don't block the core throughput target the way BN-001/002/003/004/006 do.
