# HTTPE — High-Throughput Transaction Processing Engine

**Project:** Software Engineer — High-Throughput Transaction Processing Engine (HTTPE)
**Simulation Scenario:** PayScale Financial Technologies — scaling from 1,200 TPS to 12,000+ TPS .

---

### Strictly Private and Confidential — Not for Circulation

This repository and its contents were prepared for the Zetheta Algorithms Private Limited HTTPE assessment project. It is intended solely for the purpose of candidate/participant evaluation and must not be shared, copied, or circulated outside the engaging parties involved in this assessment.

---

## Repository Structure

```
httpe-payscale/
├── README.md
├── SELF-ASSESSMENT.md
├── REFLECTION.md
├── docs/
│   ├── 01-scenario-analysis.md
│   ├── 02-technology-evaluation.md
│   ├── 03-high-level-design.md
│   ├── 04-data-flow-design.md
│   ├── 05-database-schema.md
│   ├── 06-sharding-strategy.md
│   ├── 07-message-queue-topology.md
│   ├── 08-concurrency-control.md
│   ├── 09-fault-tolerance.md
│   ├── 10-api-specification.md
│   ├── 11-load-testing-strategy.md
│   ├── 12-capacity-planning.md
│   ├── 13-fmea.md
│   └── 14-architecture-review-deck.md
├── adrs/
│   ├── 001-message-queue-selection.md
│   ├── 002-database-selection.md
│   ├── 003-sharding-strategy.md
│   ├── 004-communication-pattern.md
│   └── 005-cache-invalidation.md
├── diagrams/
│   ├── system-architecture.drawio (+ .png)
│   ├── p2p-payment-flow.puml
│   ├── batch-settlement-flow.puml
│   ├── failover-flow.puml
│   └── circuit-breaker-fsm.puml
├── schemas/
│   ├── ddl/
│   │   ├── 001-accounts.sql
│   │   ├── 002-transactions.sql
│   │   ├── 003-ledger_entries.sql
│   │   ├── 004-users.sql
│   │   ├── 005-transaction_events.sql
│   │   ├── 006-fraud_rules.sql
│   │   ├── 007-merchant_settlements.sql
│   │   └── 008-notification_log.sql
│   └── erd/
│       └── erd-diagram.dbml (+ .png)
├── pseudocode/
│   ├── occ-balance-update.py
│   ├── saga-orchestrator.py
│   ├── circuit-breaker.py
│   └── idempotency-handler.py
├── api/
│   └── openapi.yaml
└── load-tests/
    ├── performance-budget.md
    └── scenarios/
```

## Project Summary

PayScale's current infrastructure (single PostgreSQL primary, single-node RabbitMQ, 8 fixed app servers, single-node Redis) handles ~1,200 TPS. The Diwali campaign is projected to drive peak load to 12,000+ TPS with bursts to 18,000 TPS within 15 days. This repository documents a complete architecture redesign — database sharding, Kafka-based event processing, optimistic concurrency control, circuit breakers, and a capacity plan within a $45,000/month ceiling — culminating in an Architecture Review Board (ARB) presentation on Day 15.

See `docs/01-scenario-analysis.md` for the full scenario analysis and initial bottleneck assessment.
