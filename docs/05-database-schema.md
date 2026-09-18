# Database Schema: HTTPE — PayScale

**Executive Summary:** Specifies production-grade DDL for all 8 core tables, with partitioning for the two highest-volume tables (`transactions`, `ledger_entries`) and immutability triggers enforcing RBI's audit-trail requirement at the database level, not just by convention.

Full DDL: `schemas/ddl/001-accounts.sql` through `008-notification_log.sql` (run in that numeric order — later files add foreign keys back onto earlier tables, e.g. `004-users.sql` adds the FK from `accounts` to `users`). ER diagram: `schemas/erd/erd-diagram.dbml` (source) and `schemas/erd/erd-diagram.png` (rendered).

## Schema Overview

Eight tables cover three categories of data:

- **Core financial data** — `accounts`, `transactions`, `ledger_entries`. These carry the strict correctness requirements (ACID, OCC, immutability) and the highest write volume.
- **Supporting operational data** — `users`, `transaction_events`, `merchant_settlements`, `notification_log`. Still important, but with looser latency requirements than the payment hot path.
- **Reference/configuration data** — `fraud_rules`. Small, mostly static, read on every transaction.

## Indexing Strategy

Every index in the DDL files is commented with its specific justification at the point it's declared, but the underlying principles are:

1. **Partial indexes wherever the hot-path query only touches a small, well-defined subset of rows.** `idx_accounts_status_active`, `idx_txn_status_pending`, `idx_fraud_rules_active`, and `idx_notification_retry_queue` all follow this pattern — indexing only `ACTIVE` accounts, only in-flight transactions, only active fraud rules, or only failed notifications keeps those indexes small and fast indefinitely, rather than growing in lockstep with the (much larger) table of terminal/historical rows.
2. **Composite indexes ordered to match the actual query pattern**, not just "index every foreign key." For example, `idx_txn_source_account` is `(source_account_id, created_at DESC)` rather than just `(source_account_id)`, because the real query is always "this account's transactions, most recent first" — a single-column index would still need a separate sort step.
3. **Uniqueness constraints doing double duty as correctness guarantees, not just performance aids.** `idx_txn_idempotency_key` enforces FR-002 at the database level (not just in application code), and `uq_settlement_merchant_day` is what actually makes the Day 4 batch settlement job idempotent — a duplicate settlement attempt fails on a constraint violation rather than needing extra application logic to detect it.
4. **No indexing of write-heavy columns that aren't actually queried.** `metadata` and `payload` (JSONB columns) are intentionally left unindexed for now — they're written far more often than they're queried, and a GIN index would add write overhead without a corresponding read benefit at this stage. If ops tooling later needs to query inside these fields regularly, a targeted GIN index can be added then.

## Partitioning Strategy: `transactions` and `ledger_entries`

Both tables are partitioned by month on `created_at` using native PostgreSQL declarative partitioning (`PARTITION BY RANGE`). Three reasons this table pair specifically needs it, ahead of the others:

- **Volume.** At target scale (85M transactions/day), these two tables dwarf every other table in the schema — a full year is on the order of 30 billion rows combined. Partitioning keeps each individual partition (and its indexes) a manageable size, which keeps query planning and index maintenance fast.
- **RBI's 2-year hot retention rule has a natural expiry point.** Once a monthly partition ages past the 2-year mark, it can be archived and dropped as a single fast metadata operation (`DROP TABLE transactions_2024_08`), instead of running a slow, lock-heavy `DELETE` against billions of individual rows.
- **Query patterns are naturally date-scoped.** Almost every real query against these tables — "this account's transactions from the last 30 days," "this month's settlement batch," "yesterday's reconciliation run" — already filters by a date range, so Postgres's partition pruning means most queries only ever touch one or two partitions instead of scanning the whole table.

New partitions are created ahead of time by a scheduled maintenance job (e.g., `pg_partman`) rather than manually — the DDL files show three months created explicitly as a starting point, with the expectation that a rolling job keeps at least 2-3 months of future partitions pre-created at all times so an unexpected surge in traffic never hits a missing partition.

`ledger_entries` and `transaction_events` are **append-only by design** — both have triggers that reject any `UPDATE` or `DELETE` outright. This is a direct, enforced implementation of RBI's immutable audit trail requirement (FR-004): it's not just a convention the application layer is expected to follow, the database itself refuses the operation.

## Notes on Cross-Table Dependencies

- `accounts.user_id` has its foreign key added in `004-users.sql` rather than `001-accounts.sql`, since the scripts run in numeric order and `users` doesn't exist yet at that point.
- `reject_ledger_mutation()` (the immutability trigger function) is defined once in `003-ledger_entries.sql` and reused by `005-transaction_events.sql`.
- `set_updated_at()` (the auto-timestamp trigger function) is defined once in `001-accounts.sql` and reused by `004-users.sql` and `006-fraud_rules.sql`.
- Citus distribution keys shown in each file (`create_distributed_table(...)`) are marked tentative — they're finalized with full justification in `docs/06-sharding-strategy.md` (Day 6), since the shard key choice depends on the cross-shard transaction analysis done there.
