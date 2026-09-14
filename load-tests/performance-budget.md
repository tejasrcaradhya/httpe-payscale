# Performance Budget: HTTPE — PayScale

Allocates the p99 ≤100ms target (NFR-003) across every stage of the synchronous P2P payment critical path (see `diagrams/p2p-payment-flow.puml`, happy path).

## Budget Table

| Processing Stage | Budget (ms) | Justification |
|---|---|---|
| API Gateway (Envoy: SSL termination + routing + rate-limit check) | 3–5 | Hardware-offloaded SSL, in-memory routing table, Redis-backed rate counter lookup |
| Auth / JWT validation | 2–3 | Local validation against cached public keys — no network call |
| Idempotency check + reserve (Redis `SET NX`) | 1–2 | Single Redis round-trip, sub-millisecond typical latency |
| Fraud detection (sync path, `CB-FRAUD` protected) | 10–15 | Matches FR-007's 15ms SLA exactly — this is the single largest budget line, and deliberately so |
| Database read (account balance + version, OCC) | 3–5 | Indexed point lookup on the local shard |
| Business logic validation (balance check, etc.) | 1–2 | In-memory computation, no I/O |
| Database write (debit `UPDATE` + ledger `INSERT` + outbox `INSERT`, same transaction) | 8–15 | Transactional write with fsync; the outbox insert here is what later feeds the async Kafka publish — but that publish itself is NOT in this budget (see note below) |
| Response serialization + return | 2–3 | JSON serialization and transmission |
| **TOTAL (typical, no OCC retry)** | **30–50ms** | Comfortably within the 100ms p99 target, leaving ~50–70ms of headroom |

## Why Kafka Publishing Is NOT in This Budget

The reference performance-budget template includes a "Kafka produce" line on the critical path. **This architecture deliberately does not** — per ADR-004 (sync/async communication pattern) and the Outbox Pattern design (Day 7), the outbox row is written in the same database transaction as the balance update, but the actual publish to Kafka happens afterward, asynchronously, via the Outbox Relay — **after the client has already received their response.** Including it in the customer-facing latency budget would be modeling a dependency that doesn't actually sit in the request's path. This is a direct, intentional consequence of the sync/async split established in ADR-004, not an oversight.

## Accounting for OCC Retries

The budget above assumes the common case: no version conflict on the balance update. Per Day 8's contention analysis, conflicts are rare for typical P2P transfers (a given account is rarely the target of many simultaneous transactions). When a conflict *does* occur, steps "Database read" through "Database write" repeat (roughly +11–20ms per retry, plus exponential backoff starting at 50ms). This is why the budget targets the **p99** (100ms) rather than assuming every request hits the fast path — a small number of single-retry requests landing around 50–90ms is expected and accounted for within the p99 target, while the rarer multi-retry case is covered by the looser p99.9 target (≤250ms, NFR-004).

## Headroom Summary

| Percentile | Target | Typical Budget Usage | Margin |
|---|---|---|---|
| p50 | ≤50ms (NFR-002 target is ≤30ms; treated as a stretch goal) | ~30–40ms | Comfortable |
| p99 | ≤100ms | ~30–50ms (0 retries) to ~60–90ms (1 retry) | Comfortable, even with a retry |
| p99.9 | ≤250ms | Covers rare multi-retry cases or a brief downstream slowdown absorbed by a circuit breaker's fallback path | Comfortable |
