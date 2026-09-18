# Architecture Review Board Presentation Outline

**Executive Summary:** A slide-by-slide outline for the 15-minute ARB presentation, followed by a direct mapping of all 10 sample ARB questions to the exact document and section that answers each one — so the answer is a pointer to prepared work, not something improvised live.

Per Section B6: 15 minutes presentation, 20 minutes technical deep-dive Q&A, 10 minutes deliberation. **Use the diagrams as visual aids — don't read from slides.**

## Slide-by-Slide Outline (~15 minutes)

| # | Slide | Time | Content | Visual Aid |
|---|---|---|---|---|
| 1 | Title / Problem Statement | 1 min | PayScale must scale from 1,200 to 12,000+ TPS in 15 days for the Diwali campaign; current architecture fails at 1,800 TPS (BN-001) | — |
| 2 | Current Bottlenecks | 1.5 min | The 3 CRITICAL bottlenecks: connection pool exhaustion, RabbitMQ consumer lag, DB lock contention — each maps directly to a redesign decision | `docs/01-scenario-analysis.md` bottleneck table |
| 3 | Target Architecture Overview | 2 min | The 13-component system, edge-to-data-layer, sync path vs. async fan-out | `diagrams/system-architecture.png` |
| 4 | Key Decision 1 — Kafka | 1.5 min | Why Kafka over RabbitMQ/SQS; acknowledge the team's learning-curve risk head-on rather than waiting for the ARB to raise it | `adrs/001-message-queue-selection.md` |
| 5 | Key Decision 2 — Sharding | 2.5 min | `hash(account_id)`, 6 shards derived from throughput math, and the hot-merchant mitigation — **lead with this**, since it's the ARB's most likely probe (Q1) | `docs/06-sharding-strategy.md`, the distribution simulation numbers |
| 6 | Key Decision 3 — Concurrency | 1.5 min | OCC over pessimistic locking; briefly walk the write-skew example | `pseudocode/occ-balance-update.py` |
| 7 | Fault Tolerance | 2 min | Circuit breakers + bulkheads fixing BN-003; the failover flow (shard primary dies → replica promotes within 30s) | `diagrams/failover-flow.puml`, the CB instance table |
| 8 | Capacity & Cost | 1.5 min | $20,330/month at target — 45% of the $45K ceiling — with the non-linear scaling explanation ready if asked why | `docs/12-capacity-planning.md` cost table |
| 9 | Close | 0.5 min | One sentence: what's genuinely still a risk (Kafka learning curve) and what isn't (the correctness of the OCC/saga model, which is proven, not just asserted) | — |

**Total: ~14.5 minutes**, leaving a small buffer.

## Presentation Delivery Notes

- Open with the sharding hot-merchant answer *before* being asked — it signals the hard question was already anticipated, which tends to shift the tone of the Q&A that follows.
- When walking `diagrams/system-architecture.png`, trace one concrete request (a P2P payment) through the diagram live rather than listing all 13 boxes in the abstract — it's more memorable and shows the architecture is a system, not just a checklist of components.
- Have `docs/06-sharding-strategy.md` and `pseudocode/occ-balance-update.py` open and ready to share-screen — these are the two most likely deep-dive destinations.

## Q&A Preparation: Sample ARB Questions Mapped to Prepared Answers

| # | Question (paraphrased) | Answer Lives In |
|---|---|---|
| Q1 | Hot-partition risk from a single 40%-volume merchant | `docs/06-sharding-strategy.md` §1 ("The Real Risk: Traffic Skew") |
| Q2 | Sender's shard down mid-P2P-transaction | `diagrams/p2p-payment-flow.puml` Failure Scenario 3; narrated in `docs/04-data-flow-design.md` |
| Q3 | Would you still choose Kafka with zero team experience and a fixed deadline? | `adrs/001-message-queue-selection.md` Consequences section — answer honestly: yes, with the managed-MSK fallback named as the risk mitigation, not glossed over |
| Q4 | Fraud detection model takes 25ms against a 10-15ms budget | `load-tests/performance-budget.md` — note the budget has ~50-70ms of headroom even in the worst case; frame this as a real but absorbable pressure, not a broken budget |
| Q5 | Prove the OCC implementation prevents write-skew | `docs/08-concurrency-control.md` §2, the full worked proof |
| Q6 | Budget cut from $40,850 to $25,000 — what gets sacrificed first? | Not pre-written — but `docs/12-capacity-planning.md`'s cost breakdown shows Payment Processing Service and PostgreSQL+Citus nodes are the two largest line items; the honest answer is to reduce shard-replica count from 3 to 2 first (accepting reduced failover headroom) before touching the customer-facing tier's auto-scaling range |
| Q7 | Exact SQL + isolation level for a balance check with optimistic locking | `pseudocode/occ-balance-update.py` + `docs/08-concurrency-control.md` §3 (isolation level table) |
| Q8 | CB-FRAUD's 15s reset timeout — exploitable? | Worth thinking through live: an attacker who can trigger 3 fraud-service failures in 10s could get a 15s window of "approved with manual review" transactions — mitigation is that manual review still catches it after the fact, and the flag itself is logged; this is a genuine accepted trade-off (availability over strict fraud-blocking), not a gap that was missed |
| Q9 | What changes for a stock exchange instead of a payment processor? | Reference `docs/A2.2`-style reasoning from the training material: latency target would drop from milliseconds to microseconds, OCC likely replaced by in-memory lock-free structures, and RBI compliance concerns would shift to SEBI's — worth a brief, honest "this would need a different architecture, not a tuned version of this one" |
| Q10 | FMEA shows Redis failure at RPN 48 — too low? | `docs/13-fmea.md` FM-003 — be ready to defend the O and D scores specifically (Redis Sentinel failover is fast and well-tested) rather than just the final number |
