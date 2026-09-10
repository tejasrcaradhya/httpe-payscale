# Technology Evaluation: HTTPE — PayScale

## Methodology

Each candidate technology is evaluated from **1–5**, where 5 represents the best fit. The evaluation considers:

- **Performance** — ability to support the target of 12,000+ TPS and sub-100 ms p99 latency.
- **Operational Complexity** — ease of deployment, monitoring, scaling, and troubleshooting.
- **Cost** — ability to remain within the $45,000/month infrastructure budget.
- **Team Expertise Compatibility** — alignment with the team's existing Java/Kotlin and PostgreSQL experience.
- **Community Support** — maturity, documentation, ecosystem, and availability of troubleshooting resources.

The raw score provides an initial comparison, but **hard NFRs are given greater weight** in the final decision. In particular, the 12,000 TPS requirement is treated as a gating requirement rather than simply another scoring category.

---

## 1. Database Layer

### PostgreSQL + Citus vs. CockroachDB vs. TiDB

| Criterion | PostgreSQL + Citus | CockroachDB | TiDB |
|---|---:|---:|---:|
| Performance | **4** | 4 | 4 |
| Operational Complexity | 3 | 3 | 2 |
| Cost | **4** | 3 | 3 |
| Team Expertise Compatibility | **5** | 2 | 1 |
| Community Support | **4** | 4 | 3 |
| **Total** | **20/25** | **16/25** | **13/25** |

### Decision: PostgreSQL 15 + Citus

PostgreSQL + Citus provides the strongest balance between scalability and implementation risk. Citus extends PostgreSQL into a distributed database while retaining PostgreSQL as the underlying technology, allowing the team to reuse existing SQL knowledge, tooling, and operational experience. It supports distributed tables, parallel query execution, and sharding across multiple PostgreSQL nodes.

CockroachDB and TiDB are technically capable alternatives, but adopting either would introduce a new database engine, query planner, indexing behavior, and operational model. With a **15-day implementation window**, that additional learning and migration risk is not justified when the team already has PostgreSQL expertise.

**Selected technology: PostgreSQL 15 + Citus**

---

## 2. Message Queue / Event Bus

### Apache Kafka vs. RabbitMQ Cluster vs. Amazon SQS

| Criterion | Apache Kafka | RabbitMQ Cluster | Amazon SQS |
|---|---:|---:|---:|
| Performance | **5** | 2 | 3 |
| Operational Complexity | 2 | 4 | **5** |
| Cost | 3 | **4** | 2 |
| Team Expertise Compatibility | 2 | 3 | 3 |
| Community Support | **5** | 4 | 3 |
| **Total** | **17/25** | **17/25** | **16/25** |

### Decision: Apache Kafka

Kafka is selected even though its raw score is tied with RabbitMQ because **performance is a hard requirement** for this system.

The target is **12,000 TPS sustained with bursts up to 18,000 TPS**. RabbitMQ has already demonstrated a bottleneck at approximately 2,000 TPS in the existing system, making it a poor choice for the redesigned architecture.

Kafka's partitioned log architecture allows workload to be distributed across brokers and provides durable event storage. Kafka also supports different delivery semantics, including exactly-once processing when the producer, consumer, and destination are configured appropriately.

Kafka is also a particularly strong fit for PayScale because events can be retained and replayed. This supports the system's **auditability and recovery requirements** rather than treating messages as disposable work items.

SQS would reduce operational overhead, but its throughput and pricing characteristics at sustained high traffic would need careful architecture and cost validation. Kafka provides greater control over partitioning, retention, replay, and throughput.

**Selected technology: Apache Kafka — 3-broker cluster**

---

## 3. Cache Layer

### Redis Cluster vs. Memcached vs. Hazelcast

| Criterion | Redis Cluster | Memcached | Hazelcast |
|---|---:|---:|---:|
| Performance | **5** | 4 | 4 |
| Operational Complexity | 3 | **4** | 3 |
| Cost | **4** | 4 | 3 |
| Team Expertise Compatibility | **4** | 2 | 3 |
| Community Support | **5** | 3 | 3 |
| **Total** | **21/25** | **17/25** | **16/25** |

### Decision: Redis Cluster

Redis provides the strongest combination of performance, functionality, and existing team familiarity.

The team already operates Redis, making a move to Redis Cluster considerably less risky than introducing a completely new caching technology. Redis transactions provide atomic execution, while `WATCH` supports optimistic check-and-set behavior that can be useful for idempotency workflows.

Redis also provides TTL-based expiration, making it suitable for:

- Idempotency keys
- API response caching
- Rate-limit counters
- Short-lived session/state data
- Notification fan-out

**Selected technology: Redis Cluster — 6 nodes / 96 GB total**

---

## Final Technology Selection

| Component | Selected Technology | Key Reason |
|---|---|---|
| **Database** | PostgreSQL 15 + Citus | PostgreSQL expertise + distributed scalability |
| **Message Queue** | Apache Kafka | Meets high-throughput event-processing requirements |
| **Cache** | Redis Cluster | Performance + existing expertise + atomic operations |

## Recommended Architecture

```text
                    ┌─────────────────┐
                    │   API Gateway   │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │ Java/Kotlin API │
                    │    Services     │
                    └──────┬─────┬─────┘
                           │     │
                ┌──────────┘     └──────────┐
                ▼                           ▼
       ┌────────────────┐          ┌─────────────────┐
       │ Redis Cluster  │          │  Apache Kafka   │
       │  6 nodes/96GB  │          │   3 brokers     │
       └────────────────┘          └────────┬────────┘
                                            │
                                            ▼
                                   ┌─────────────────┐
                                   │ PostgreSQL 15   │
                                   │    + Citus      │
                                   └─────────────────┘
```

## Why These Technologies Were Selected

1. **PostgreSQL + Citus** minimizes migration and learning risk while providing distributed database capabilities.
2. **Kafka** is selected because the 12,000 TPS requirement makes throughput a hard constraint.
3. **Redis Cluster** provides high performance, TTL support, atomic operations, and compatibility with the team's existing Redis experience.
4. The overall stack aligns with the **15-day implementation window**, existing team skills, scalability requirements, and infrastructure budget.
