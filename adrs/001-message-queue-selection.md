# ADR-001: Use Apache Kafka over RabbitMQ and Amazon SQS for the Event Bus

**Status:** Accepted

## Context

PayScale's current messaging layer is a single-node RabbitMQ instance. Simulated load testing (Section A3.2, bottleneck BN-002) already shows this instance developing a message-acknowledgment backlog and consumer lag exceeding 30 seconds once traffic reaches just 2,000 TPS — far below the 12,000+ TPS sustained target (NFR-001), and less than a sixth of the 18,000 TPS burst ceiling the architecture must absorb during Diwali flash sales.

Beyond raw throughput, the redesign has functional requirements the current queue cannot satisfy well: exactly-once processing under any failure (FR-003), a complete and immutable audit trail for every transaction state transition (FR-004), and asynchronous fan-out of transaction events to multiple downstream consumers — notification, fraud detection, reconciliation, and audit/compliance services — without those services blocking the critical payment path. The saga-based orchestration pattern selected for cross-service transaction flows also depends on a durable, replayable event log rather than a queue whose messages disappear once consumed.

The team's production messaging experience today is limited to RabbitMQ; no one has run Kafka in production. This is a real constraint on a 15-day timeline and is addressed directly in the Consequences section below.

## Decision

Adopt Apache Kafka as the system's event bus, deployed as a 3-broker cluster within the RBI-mandated India region (AWS ap-south-1), each broker sized at roughly 8 vCPU / 32GB per the capacity reference in Section A3 (Capacity Planning Template). Topics are configured with a replication factor of 3 and `min.insync.replicas=2` so the cluster tolerates a single broker failure without data loss (mapping directly to failure mode FM-002 in the FMEA). Partition counts are sized to match consumer-group parallelism — for example, the primary `transaction-events` topic is provisioned with 32 partitions so an 8-instance consumer group can each process 4 partitions with headroom to scale the group up as load grows.

Exactly-once delivery is achieved through two complementary mechanisms: Kafka's built-in idempotent producers (`enable.idempotence=true`), and the **Outbox Pattern** — writing the transaction event to an outbox table in the *same database transaction* as the balance update, with a separate relay process reading the outbox and publishing to Kafka. This avoids the dual-write problem (database commit succeeding but the Kafka publish failing, or vice versa) without requiring a distributed transaction across the database and the queue. Dead-letter topics are configured per consumer group so a single malformed or "poison" message cannot block an entire partition; the `kafka_consumer_lag` metric is monitored with an alert threshold of 10,000 messages, per the observability design in Section A5.6.

## Alternatives Considered

| Option | Peak Throughput Fit | Replay / Audit Trail | Ops Complexity | Verdict |
|---|---|---|---|---|
| **RabbitMQ (clustered)** | Already measured failing at 2,000 TPS (BN-002); clustering adds headroom but the queue-based (not log-based) model is a structurally weaker fit for sustained 12K+ TPS | Poor — messages are removed once consumed, awkward for replay/audit needs | Lower — team has existing familiarity | **Rejected** |
| **Amazon SQS** | FIFO queues cap at roughly 3,000 msg/s even with batching; hitting 12,000 TPS would require splitting traffic across many queues, adding its own complexity | Weak — not designed for long-retention event replay | Lowest — fully managed | **Rejected** — throughput ceiling and per-request cost at sustained 12K TPS both work against it |
| **Apache Kafka** | Purpose-built for this throughput class; explicitly the recommended technology in the project's own reference architecture (Section A4.3) | Strong — partitioned log natively supports replay and event sourcing | Highest — new to the team | **Selected** |

RabbitMQ was rejected primarily because its failure mode isn't hypothetical — it's the exact bottleneck already observed in the load test report. SQS was rejected because its FIFO throughput ceiling and per-request pricing model both work against the 12,000 TPS sustained / 18,000 TPS burst target within the $45,000/month budget.

## Consequences

**Positive:** Kafka provides headroom well beyond 12,000 TPS simply by adding partitions and brokers, without an architectural rewrite. It is a natural fit for the saga orchestration pattern and for the immutable audit trail required by RBI, since events can be replayed rather than consumed once and lost. It's also the path with the strongest available learning material (Part A curates free Kafka-specific resources, including official documentation and the MIT distributed systems lectures).

**Trade-offs / accepted technical debt:** Zero production Kafka experience on the team today is the single biggest execution risk on this ADR, and is exactly the kind of question the ARB is likely to probe (see sample question Q3). This is mitigated by two choices: first, allocating early project days (per the Day 7 schedule) specifically to Kafka topology design and hands-on learning before it's load-bearing; second, keeping the option open to use a managed offering (e.g., Amazon MSK) to reduce day-to-day operational burden if self-managing three brokers proves too time-consuming inside the 15-day window. Consumer-lag monitoring and dead-letter-queue handling must be built in from day one rather than retrofitted later.

## Compliance

The Kafka cluster is deployed exclusively within AWS ap-south-1 (Mumbai), satisfying the RBI Data Localization Directive (2018). TLS 1.3 in transit and AES-256 encryption at rest satisfy PCI-DSS v4.0. Kafka's own retention is kept short (operational replay window only, e.g., 7 days) rather than used to satisfy RBI's 2-year hot data retention requirement directly — that obligation is met by the durable transactions table and the append-only audit/compliance service, avoiding unbounded broker storage growth.
