# ADR-005: Cache-Aside with Short TTL and Explicit Invalidation on Write

**Status:** Accepted

## Context

The Redis Cluster serves two distinct purposes that need different consistency treatment: (1) caching account balances for fast display reads, and (2) storing idempotency keys, which Redis is the *primary* store for, not a cache of Postgres data. This ADR covers strategy #1 specifically — how the balance cache stays consistent with PostgreSQL, the source of truth — since getting this wrong risks showing users stale or, worse, incorrect balance information, while getting it *too* conservative risks adding latency to the payment hot path for no real benefit.

Critically, the actual payment write path (the OCC-protected debit/credit in `pseudocode/occ-balance-update.py`) **always reads directly from PostgreSQL**, never from cache — it needs the true current `version` to perform its compare-and-swap correctly, so a cache can never be the source of truth for that operation regardless of which invalidation strategy is chosen. This ADR is specifically about balance *display* reads (e.g., a user's app home screen), not the financial correctness path.

## Decision

**Cache-aside with a short TTL (2–5 seconds) and explicit invalidation on write**, rather than write-through or write-behind. On a cache miss, the application reads from PostgreSQL and populates Redis with a short expiry. On a write (any successful debit/credit), the application **deletes** the relevant cache key rather than trying to update it in place — forcing the next read to go to PostgreSQL and repopulate with genuinely fresh data. Deleting rather than updating avoids a subtle race: if the cache were updated directly during a write, a *second* concurrent write's cache update could arrive out of order and leave stale data in place; deletion sidesteps this entirely, since the next reader always goes back to the source of truth.

This is a deliberate application of the CAP theorem trade-off discussed in the training material (Section A1.4): balance **display** is treated as an AP-style read (briefly stale data is an acceptable trade for speed and availability), while balance **mutation** is always CP-style (the OCC write path never trades correctness for availability). Splitting the policy this way — rather than applying one blanket caching rule everywhere — is the core of this decision.

## Alternatives Considered

| Strategy | Problem |
|---|---|
| **Write-through** (every write updates cache and DB synchronously) | Adds latency to the payment hot path for a benefit that doesn't materialize — the write path never reads from cache anyway (it needs the true `version` for OCC), so keeping the cache "warm" synchronously on every write serves only *future* display reads, which a short TTL already handles adequately without the extra synchronous hop |
| **Write-behind** (write to cache first, asynchronously flush to DB) | Unacceptable for financial data — this makes the cache briefly the most current state while the DB lags behind, directly contradicting the requirement that PostgreSQL remain authoritative and immediately consistent (FR-010, ACID) |
| **Cache-aside with TTL + explicit invalidation** (selected) | — |

## Consequences

**Positive:** The payment hot path is completely unaffected by caching strategy, since it never touches the cache for its correctness-critical read. Display reads get the latency benefit of caching for the common case (repeated balance checks) without any risk of showing *incorrect* post-transaction state for more than a few seconds. Explicit invalidation-on-write means a user who just completed a transaction sees their updated balance almost immediately (next read after the delete), rather than waiting out a full TTL window.

**Trade-offs / accepted risk:** For the few seconds between a balance changing and the next read triggering a repopulate, a display read could show slightly stale data if it raced in before the invalidating write's delete landed — this is accepted because it's a display-only concern with no financial consequence (an actual transaction attempt always re-validates against the true, current database balance via OCC, so a stale *display* can never lead to an actual overdraft). If product requirements later demand stronger display freshness (e.g., a live-updating balance ticker), that would be better solved with a WebSocket push on write rather than tightening the cache TTL further, since tightening TTL alone doesn't eliminate the fundamental race, just narrows its window.

## Compliance

Not directly regulatory. The idempotency-key store (Redis, 24-hour TTL, per FR-002) is a separate concern from this ADR — it is the primary store for that data, not a cache of anything in PostgreSQL, so cache-invalidation strategy doesn't apply to it.
