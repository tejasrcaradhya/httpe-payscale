-- =====================================================================
-- 002-transactions.sql
-- Transactions table: the record of every payment attempt. Partitioned
-- by month on created_at (Section: Day 5 partitioning requirement) since
-- this is the highest-volume table (85M/day at target scale) and old
-- partitions can be archived/dropped independently per RBI's 2-year
-- hot-retention rule without touching recent data.
-- =====================================================================

CREATE TYPE transaction_type_enum AS ENUM ('P2P', 'MERCHANT_PAYMENT', 'SETTLEMENT', 'REVERSAL', 'FEE');
CREATE TYPE transaction_status_enum AS ENUM ('INITIATED', 'PROCESSING', 'COMPLETED', 'FAILED', 'REVERSED');

CREATE TABLE transactions (
    transaction_id          UUID NOT NULL DEFAULT gen_random_uuid(),  -- UUIDv7 for chronological ordering
    idempotency_key         VARCHAR(128) NOT NULL,                    -- client dedup key; 24hr TTL enforced at Redis layer
    transaction_type        transaction_type_enum NOT NULL,
    source_account_id       UUID,                                     -- NULL for system credits
    destination_account_id  UUID,                                     -- NULL for fee deductions
    amount                  NUMERIC(18,4) NOT NULL CHECK (amount > 0),
    status                  transaction_status_enum NOT NULL DEFAULT 'INITIATED',
    saga_id                 UUID NOT NULL,
    failure_reason          VARCHAR(500),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at             TIMESTAMPTZ,
    metadata                JSONB NOT NULL DEFAULT '{}',

    PRIMARY KEY (transaction_id, created_at),   -- created_at must be part of the PK on a partitioned table
    CONSTRAINT fk_txn_source FOREIGN KEY (source_account_id) REFERENCES accounts(account_id),
    CONSTRAINT fk_txn_dest FOREIGN KEY (destination_account_id) REFERENCES accounts(account_id)
) PARTITION BY RANGE (created_at);

-- Monthly partitions. In production these are created automatically ahead of
-- time by a scheduled job (e.g., pg_partman) rather than by hand each month.
CREATE TABLE transactions_2026_09 PARTITION OF transactions
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE transactions_2026_10 PARTITION OF transactions
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE transactions_2026_11 PARTITION OF transactions
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
-- ... additional monthly partitions created on a rolling basis.

-- Indexes (created on the parent; Postgres propagates them to each partition automatically)
CREATE UNIQUE INDEX idx_txn_idempotency_key ON transactions (idempotency_key, created_at);
    -- Enforces FR-002 (idempotent submission). created_at included because it's required
    -- alongside any unique index on a partitioned table.
CREATE INDEX idx_txn_source_account ON transactions (source_account_id, created_at DESC);
    -- Supports "transaction history for account X" queries, most-recent-first.
CREATE INDEX idx_txn_dest_account ON transactions (destination_account_id, created_at DESC);
CREATE INDEX idx_txn_saga_id ON transactions (saga_id);
    -- Supports saga-recovery queries (replaying transaction_events for an in-flight saga).
CREATE INDEX idx_txn_status_pending ON transactions (transaction_id) WHERE status IN ('INITIATED', 'PROCESSING');
    -- Partial index: only a small fraction of rows are ever in a non-terminal state;
    -- this keeps the "find stuck transactions" monitoring query fast without indexing
    -- the (much larger) set of COMPLETED/FAILED/REVERSED rows.

-- Citus distribution (tentative — finalized in docs/06-sharding-strategy.md):
-- SELECT create_distributed_table('transactions', 'source_account_id');
