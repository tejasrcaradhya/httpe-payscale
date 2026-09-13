# ADR-004: Hybrid Synchronous/Asynchronous Communication Pattern

**Status:** Accepted

## Context

The architecture mixes direct service-to-service calls (API Gateway → Orchestrator → Payment Processing/Account/Fraud Detection) with Kafka-based event fan-out (→ Notification, Reconciliation, Audit & Compliance). Without an explicit, consistent rule for which category a given interaction belongs to, this risks becoming an ad-hoc, inconsistent set of choices made independently by whoever builds each service — which could quietly blow the p99 ≤100ms latency budget (NFR-003) if a non-essential call is made synchronous, or produce a confusing user experience if an essential one is made asynchronous. This ADR formalizes the rule already implicit in the Day 3 HLD and Day 4 data flow diagrams into an explicit, defensible decision.

## Decision

A call is made **synchronous** if and only if both of the following hold: (1) the client cannot be given a meaningful, correct response without its result, and (2) it can be bounded to a small, well-defined slice of the overall latency budget. This covers exactly: API Gateway → Transaction Orchestrator, Orchestrator → Fraud Detection Service (bounded to 15ms), Orchestrator → Payment Processing Service, and the balance-affecting database writes themselves (debit, credit, ledger entry, all within the same DB transaction).

Everything else is **asynchronous**, published via Kafka and consumed independently: notification delivery, audit logging, reconciliation, and settlement notifications. None of these need to complete before the customer receives their transaction result, and forcing them synchronous would either blow the latency budget (e.g., waiting on an SMS provider) or create unnecessary coupling (a notification-provider outage would otherwise be capable of blocking payments entirely, which is unacceptable for a financial system). The full per-stage latency budget accounting for the synchronous path is formalized in Day 11's performance budget document.

## Alternatives Considered

| Approach | Problem |
|---|---|
| **Synchronous everywhere** | Fails the latency budget outright once fraud detection, balance writes, and notification dispatch are all serialized into one request; also tightly couples payment success to the availability of non-essential downstream services (a notification provider outage would block payments) |
| **Asynchronous everywhere, including fraud/balance checks** | The customer needs a definitive answer within the request/response cycle — a fully async payment flow would mean returning `PENDING` for every transaction and requiring the client to poll for a final result, which fails the basic UX expectation that a payment app tells you immediately whether a payment succeeded, and adds unnecessary complexity for what should be the common, fast case |
| **Hybrid (selected)** | Keeps the critical path lean and bounded; decouples genuinely non-essential work |

## Consequences

**Positive:** The synchronous critical path stays short and each hop within it has a clear reason to exist there, making the p99 latency budget achievable and auditable. Non-essential services (notification providers, the audit pipeline) can degrade or fail without taking payment processing down with them — this is the same principle already reflected in the circuit-breaker fallback behaviors documented in the Day 3 HLD (e.g., `CB-NOTIFY` queues and proceeds rather than blocking).

**Trade-offs / accepted risk:** This rule depends on correct classification at build time — a developer mistakenly making an async-appropriate call synchronous (e.g., calling a notification provider directly instead of publishing to Kafka) could silently reintroduce a latency or coupling risk that this ADR is meant to prevent. This is mitigated by making the synchronous path's latency budget explicit and testable (Day 11), so a misclassified call shows up immediately as a budget violation in load testing rather than being discovered in production.

## Compliance

This ADR has no direct regulatory requirement attached to it, but it does affect how compliance-critical data reaches the Audit & Compliance Service — since that path is asynchronous, its completeness depends on Kafka's durability (RF=3, `min.insync.replicas=2`) and the dedicated P1-alert DLQ policy for that consumer group specifically (`docs/07-message-queue-topology.md`), ensuring "asynchronous" does not become "best-effort" for a compliance-critical data path.
