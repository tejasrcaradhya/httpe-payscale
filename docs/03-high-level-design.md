# High-Level Design: HTTPE — PayScale Financial Technologies

See the accompanying diagram: `diagrams/system-architecture.drawio` (editable source) and `diagrams/system-architecture.png` (exported image).

## Deployment Topology Overview

All components are deployed within **AWS ap-south-1 (Mumbai)** across multiple Availability Zones, satisfying the RBI Data Localization Directive (all payment data stays within India). The system is split into five logical layers:

1. **Edge Layer** — Load Balancer, API Gateway
2. **Orchestration Layer** — Transaction Orchestrator, Payment Processing Service, Account Service, Fraud Detection Service
3. **Event Layer** — Kafka Event Bus, and its async consumers (Notification, Reconciliation, Audit & Compliance)
4. **Data Layer** — PostgreSQL + Citus (sharded), Redis Cluster
5. **Cross-Cutting Layer** — Observability Stack, reachable from every other component

A P2P payment request flows synchronously through Edge → Orchestration → Data (for the balance write), then fans out asynchronously through Kafka to the Event Layer consumers — keeping the customer-facing latency budget (p99 ≤100ms) isolated from slower, non-blocking work like notifications and reconciliation.

---

## 1. API Gateway Layer

- **Responsibility:** Single entry point for all client traffic — request routing to backend services, rate limiting per account tier, authentication (JWT validation), SSL/TLS termination, and canary deployment routing for safe rollouts.
- **Interfaces:** Receives HTTPS traffic from the Load Balancer; routes to Transaction Orchestrator, Account Service, and read-only endpoints directly to cached data where appropriate.
- **Technology & Justification:** **Envoy Proxy**, chosen over Kong/AWS API Gateway for its low per-request latency overhead (sub-millisecond) and native support for the canary/traffic-splitting patterns needed for safe daily deployments during a 15-day build.
- **Scaling Strategy:** Stateless — scales horizontally behind the load balancer; sized at 4 instances for 12,000 TPS per the capacity plan, auto-scaling to handle the 18,000 TPS burst.
- **Failure Handling:** Multiple gateway instances across AZs; unhealthy instances removed from rotation by the load balancer's health checks within seconds.

## 2. Load Balancer

- **Responsibility:** Layer 7 load balancing across API Gateway instances, health checking, session affinity where needed, and connection draining during deploys.
- **Interfaces:** Sits directly in front of the API Gateway fleet; the only component with a public-facing endpoint.
- **Technology & Justification:** **AWS ALB**, chosen for native multi-AZ failover and tight integration with auto-scaling groups, avoiding the operational overhead of self-managing HAProxy/Traefik during a tight timeline.
- **Scaling Strategy:** Fully managed — AWS scales ALB capacity automatically with traffic.
- **Failure Handling:** Multi-AZ by default; automatically stops routing to unhealthy targets.

## 3. Transaction Orchestrator

- **Responsibility:** Owns the saga workflow for each transaction — debit sender → credit receiver → create ledger entries → trigger notifications — including compensation logic if any step fails, and idempotency-key enforcement at the entry point (FR-002).
- **Interfaces:** Called synchronously by the API Gateway; calls Payment Processing Service and Account Service synchronously; publishes transaction-state events to Kafka for downstream async consumers.
- **Technology & Justification:** A **custom state machine** (Java/Spring Boot) rather than adopting Temporal.io/Cadence — those tools add real value but also a new operational dependency the team would need to learn from scratch; a well-tested custom saga state machine keeps the critical path inside technology the team already knows, while still fully implementing the saga pattern described in Section A1.4.
- **Scaling Strategy:** Stateless request handling (all saga state persisted in the transactions/transaction_events tables), so horizontal scaling is a matter of adding instances.
- **Failure Handling:** If a step fails mid-saga, the compensating transaction for prior completed steps executes automatically (e.g., re-crediting the sender); in-flight sagas are recoverable by replaying from `transaction_events` on orchestrator restart.

## 4. Payment Processing Service

- **Responsibility:** Balance validation, debit/credit execution, currency handling, and fee calculation for each transaction leg.
- **Interfaces:** Called by the Transaction Orchestrator; reads/writes account balances via the Database Layer using OCC; checks the idempotency key against Redis before processing.
- **Technology & Justification:** Custom Java/Spring Boot microservice — matches team's existing production skill set (per the CTO's stated constraint) and integrates directly with the chosen Postgres+Citus driver ecosystem.
- **Scaling Strategy:** Stateless; scales horizontally (12 instances at 12K TPS per the capacity reference), the largest instance count of any service since it sits on the hot path of every transaction.
- **Failure Handling:** OCC version-conflict retries with exponential backoff (bounded to a small number of attempts before surfacing a failure to the orchestrator, which triggers the saga's compensation path).

## 5. Account Service

- **Responsibility:** Account CRUD operations, balance and tier management, freezing/suspension, and per-tier rate-limit enforcement (BASIC 50 TPS / PREMIUM 200 TPS / MERCHANT 1000 TPS, per the accounts schema).
- **Interfaces:** Called by the Transaction Orchestrator and Payment Processing Service; owns its partition of the sharded database.
- **Technology & Justification:** Custom microservice with a dedicated database partition per shard, keeping account lookups local to a shard rather than requiring cross-shard hops for the common case.
- **Scaling Strategy:** Horizontally scaled independent of the payment service; scales with shard count as new shards are added.
- **Failure Handling:** Read replicas absorb read traffic if the primary for a shard is degraded; writes queue briefly and retry during shard failover (target: 30-second RTO per NFR-006).

## 6. Notification Service

- **Responsibility:** Real-time transaction status delivery to users via WebSockets, plus push notifications, SMS, and email for completed/failed transactions (FR-008, sub-second propagation).
- **Interfaces:** Consumes transaction-state events from Kafka asynchronously — never sits on the synchronous payment path, so a slow notification provider can't add latency to a transaction.
- **Technology & Justification:** Custom service integrated with Firebase Cloud Messaging (push) and AWS SNS (SMS/email), chosen for pay-as-you-go pricing that fits the budget model without upfront commitments.
- **Scaling Strategy:** Scales with Kafka consumer-group parallelism; independent of transaction-processing capacity.
- **Failure Handling:** Circuit breaker (CB-NOTIFY) queues failed notification attempts for retry rather than blocking; the underlying transaction is already committed by this point, so notification failures never affect financial correctness.

## 7. Fraud Detection Service

- **Responsibility:** Real-time rule-engine and ML-based fraud scoring, velocity checks, evaluated within a strict 15ms SLA (FR-007) as part of the synchronous authorization path.
- **Interfaces:** Called synchronously by the Transaction Orchestrator before a transaction is committed; reads a feature store from Redis for fast velocity/pattern lookups.
- **Technology & Justification:** Custom service backed by a **Redis feature store** and **ONNX** for model inference — ONNX allows the fraud model to be trained separately (e.g., in Python) but served with low-latency native inference, which is necessary to fit inside the 15ms budget.
- **Scaling Strategy:** Horizontally scaled; sized independently since its per-request compute cost (ML inference) is higher than the other services on the hot path.
- **Failure Handling:** Circuit breaker (CB-FRAUD, 3 failures/10s threshold, 15s reset) falls back to allowing the transaction with a manual-review flag rather than blocking all payments if the fraud service degrades — a deliberate availability-over-strictness trade-off, since blocking 100% of transactions on a fraud-service outage would be worse than flagging some for later review.

## 8. Reconciliation Service

- **Responsibility:** Double-entry verification and cross-service balance reconciliation — confirming that every debit has a matching credit and that ledger and available balances stay consistent (FR-010, within 500ms).
- **Interfaces:** Consumes transaction events from Kafka asynchronously; reads from the Database Layer's analytics-oriented read replicas so reconciliation queries don't compete with live transaction traffic.
- **Technology & Justification:** Custom service paired with a dedicated analytics database (read replica), keeping heavy reconciliation queries off the primary write path entirely.
- **Scaling Strategy:** Scales with data volume rather than TPS directly — typically run as scheduled batch jobs plus a continuous consumer for near-real-time checks.
- **Failure Handling:** Reconciliation lag is monitored (not customer-facing); a backlog triggers an alert rather than any live-traffic impact, since this service is fully decoupled from the transaction-processing path.

## 9. Audit & Compliance Service

- **Responsibility:** Immutable logging of every transaction state transition, regulatory report generation, and PII masking — the system's RBI-compliance backbone (FR-004).
- **Interfaces:** Consumes every transaction event from Kafka; writes to append-only storage that is never updated or deleted, only appended to.
- **Technology & Justification:** Custom service over **append-only storage** (e.g., S3 with object lock, or a WORM-configured table), chosen specifically because RBI's audit-trail requirement demands immutability that a normally-mutable table can't guarantee without extra safeguards.
- **Scaling Strategy:** Scales with Kafka consumer parallelism; write-heavy but append-only, so it scales linearly without contention.
- **Failure Handling:** If this service falls behind, transaction processing is unaffected (Kafka retains the events for later consumption) — but a stalled audit consumer is treated as a P1 alert, since a compliance gap is a serious risk even without customer impact.

## 10. Message Queue / Event Bus (Apache Kafka)

- **Responsibility:** Asynchronous inter-service communication, the backbone of event sourcing and saga event propagation, and dead-letter-queue management for poison messages.
- **Interfaces:** Written to by the Payment Processing Service (via the Outbox Pattern) and Transaction Orchestrator; read by Notification, Reconciliation, and Audit & Compliance services.
- **Technology & Justification:** See `adrs/001-message-queue-selection.md` for the full comparison — Apache Kafka selected over RabbitMQ and SQS.
- **Scaling Strategy:** 3-broker cluster with partition counts sized to consumer-group parallelism (e.g., 32 partitions on the primary transaction-events topic).
- **Failure Handling:** Replication factor 3, `min.insync.replicas=2`, so the cluster tolerates a single broker failure without data loss (addresses FMEA item FM-002).

## 11. Database Layer

- **Responsibility:** ACID-compliant storage for accounts, transactions, and ledger entries; read replicas for reporting/reconciliation; connection pooling; backups.
- **Interfaces:** Written to by Account Service and Payment Processing Service; read by nearly every other service, directly or via replicas.
- **Technology & Justification:** See `adrs/002-database-selection.md` — PostgreSQL 15 + Citus selected over CockroachDB and TiDB.
- **Scaling Strategy:** Horizontal sharding via Citus, with shard count calculated in the Day 6 sharding strategy document; PgBouncer in front for connection pooling (resolving BN-001).
- **Failure Handling:** Each shard runs a primary + 2 read replicas; automated failover (e.g., Patroni) promotes a replica within the 30-second RTO target if a shard primary fails (FMEA item FM-001).

## 12. Cache Layer

- **Responsibility:** Hot-data caching (account balances, idempotency keys), per-tier rate-limit counters, and feature flags.
- **Interfaces:** Read/written by Payment Processing Service (idempotency checks) and Fraud Detection Service (feature store).
- **Technology & Justification:** **Redis Cluster** — see `docs/02-technology-evaluation.md` for the comparison against Memcached and Hazelcast.
- **Scaling Strategy:** 6-node cluster, 96GB total, matching the target state in Section A3.1; scales by adding nodes and resharding cache keys.
- **Failure Handling:** Circuit breaker (CB-CACHE) bypasses the cache and queries the database directly if Redis degrades, at the cost of higher latency rather than a hard failure.

## 13. Observability Stack

- **Responsibility:** Metrics collection, distributed tracing, centralized logging, alerting, and operational dashboards across every other component.
- **Interfaces:** Every service emits metrics (Prometheus format), traces (OpenTelemetry), and structured logs; this stack doesn't sit on any request path, so its own failure never affects transaction processing.
- **Technology & Justification:** **Prometheus + Grafana** for metrics/dashboards, **Jaeger** for distributed tracing, **ELK/Loki** for log aggregation — all free/open-source, matching the resource guidance in Section A6, and giving the team full visibility during the ARB's technical deep-dive.
- **Scaling Strategy:** Runs as its own small cluster (3 instances per the capacity reference), scaled independently of transaction load since its resource needs track log/metric *volume*, not TPS directly.
- **Failure Handling:** Not on the critical path — if Prometheus or Grafana is briefly unavailable, transactions continue processing normally; this is a deliberate design choice so observability tooling is never a source of production incidents itself.
