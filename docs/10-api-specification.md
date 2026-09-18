# API Specification: HTTPE — PayScale

**Executive Summary:** The complete OpenAPI 3.0 specification for 8 endpoints, using cursor-based pagination to avoid OFFSET performance cliffs at scale, with every error code traceable back to a specific failure scenario designed in an earlier document rather than invented ad hoc.

Full spec: `api/openapi.yaml` (OpenAPI 3.0.3, validated — see note at the end of this document).

## 1. Endpoint Overview

| Endpoint | Method | Purpose |
|---|---|---|
| `/transactions` | POST | Initiate a P2P payment (requires `Idempotency-Key` header, FR-002) |
| `/transactions` | GET | List transactions for an account, cursor-paginated |
| `/transactions/{id}` | GET | Get a single transaction's status |
| `/transactions/{id}/events` | GET | Full saga event history (support/audit view) |
| `/accounts/{id}` | GET | Account details |
| `/accounts/{id}/balance` | GET | Lightweight, cache-backed balance read (ADR-005) |
| `/merchants/{id}/settlements` | GET | Merchant settlement history, cursor-paginated |
| `/webhooks` | POST | Register a webhook subscription (FR-008) |

## 2. Cursor-Based Pagination

Offset-based pagination (`?page=3&size=50`) was deliberately avoided. On a table partitioned by month with tens of millions of rows, `OFFSET` requires the database to scan and discard every skipped row — the deeper the page, the slower the query. It's also unsafe under concurrent writes: if new transactions are inserted while a client pages through results, offset-based pagination can skip or duplicate rows across pages.

Instead, both paginated endpoints (`GET /transactions`, `GET /merchants/{id}/settlements`) use **keyset (cursor) pagination**:

- Results are ordered by `(created_at DESC, id DESC)`.
- The response's `next_cursor` is an opaque, base64-encoded token containing the last row's `(created_at, id)`.
- The next request's query becomes `WHERE (created_at, id) < (cursor.created_at, cursor.id) ORDER BY created_at DESC, id DESC LIMIT :limit` — an indexed range scan, not a skip-and-discard operation, so performance stays flat regardless of how deep into the result set a client pages.
- `has_more: false` and `next_cursor: null` signal the end of the results.

The cursor is intentionally opaque (not just a raw timestamp) so the encoding can change in the future — e.g., adding a tie-breaker field — without breaking clients that just pass the token back verbatim.

## 3. Error Response Schema & Status Code Mapping

Every error follows the same shape: `{ "error": { "code": "...", "message": "...", "details": {...} } }`. `code` is a stable, machine-readable string clients can branch on; `message` is human-readable and may change wording over time.

| HTTP Status | Error Code | When | Traceable To |
|---|---|---|---|
| 400 | `VALIDATION_ERROR` | Malformed request body or query params | — |
| 402 | `INSUFFICIENT_BALANCE` | Debit exceeds available balance | Day 4 Failure Scenario 2 |
| 404 | `RESOURCE_NOT_FOUND` | Transaction/account/merchant doesn't exist | — |
| 409 | `DUPLICATE_REQUEST_IN_PROGRESS` | Same idempotency key currently mid-processing | Day 9 idempotency handler (concurrent-duplicate case) |
| 422 | `FRAUD_DETECTED` / `ACCOUNT_FROZEN` | Transaction rejected by business rules | Day 3 Fraud Detection Service |
| 429 | `RATE_LIMIT_EXCEEDED` | Account's tier limit exceeded | Section 4 below |
| 503 | `RECEIVER_SHARD_UNAVAILABLE` | A required shard was unreachable mid-transaction; saga compensated | Day 4 Failure Scenario 3 |
| 500 | `INTERNAL_ERROR` | Unexpected server-side failure | — |

Every error code in this table maps back to a scenario already designed in an earlier day's work — the API surface isn't inventing new failure modes, it's exposing the ones the system actually produces.

## 4. Rate Limiting Strategy

Enforced at the API Gateway (Envoy) using a token-bucket algorithm, with counters held in the Redis Cluster (per the Day 3 HLD's cache-layer responsibilities). Limits are keyed by account, matching each account's `tier` column:

| Tier | Write Endpoints (`POST /transactions`) | Read Endpoints (`GET ...`) |
|---|---|---|
| BASIC | 50 TPS | 250 requests/sec (5x write limit — reads are cheap and mostly cache-served) |
| PREMIUM | 200 TPS | 1,000 requests/sec |
| MERCHANT | 1,000 TPS | 5,000 requests/sec |

These figures come directly from the `tier` enum already defined on the `accounts` table (Day 5) — the API layer enforces the same limits the schema was designed around, rather than introducing a second, disconnected set of numbers. A `429` response includes a `Retry-After` header so well-behaved clients back off automatically rather than immediately retrying into the same limit.

## 5. Validation

The spec was checked programmatically rather than just by eye: parsed as YAML (valid), every one of its 41 `$ref` pointers resolved successfully against the document, and every operation's response object carries a `description` (a common Swagger Editor validation failure when relying on `$ref`-only responses without checking the referenced object itself has one). The same spec can be pasted into `https://editor.swagger.io` for interactive/visual validation and try-it-out testing.
