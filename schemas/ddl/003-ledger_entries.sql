-- =====================================================================
-- 003-ledger_entries.sql
-- The authoritative, append-only double-entry ledger. Every balance
-- movement produces exactly two rows: one DEBIT and one CREDIT, which
-- must sum to zero for a given transaction (enforced by the Reconciliation
-- Service, not a DB constraint, since the two rows are written in the same
-- transaction but from application logic). This table is the RBI-mandated
-- immutable audit trail (FR-004) — rows are never updated or deleted.
-- Partitioned by month on created_at for the same reasons as transactions.
-- =====================================================================

CREATE TYPE ledger_entry_type_enum AS ENUM ('DEBIT', 'CREDIT');

CREATE TABLE ledger_entries (
    ledger_entry_id   UUID NOT NULL DEFAULT gen_random_uuid(),  -- UUIDv7
    transaction_id    UUID NOT NULL,
    account_id        UUID NOT NULL,
    entry_type        ledger_entry_type_enum NOT NULL,
    amount            NUMERIC(18,4) NOT NULL CHECK (amount > 0),
    balance_after     NUMERIC(18,4) NOT NULL,        -- account's ledger_balance immediately after this entry
    currency          CHAR(3) NOT NULL DEFAULT 'INR',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (ledger_entry_id, created_at),
    CONSTRAINT fk_ledger_transaction FOREIGN KEY (transaction_id, created_at)
        REFERENCES transactions (transaction_id, created_at),
    CONSTRAINT fk_ledger_account FOREIGN KEY (account_id) REFERENCES accounts(account_id)
) PARTITION BY RANGE (created_at);

CREATE TABLE ledger_entries_2026_09 PARTITION OF ledger_entries
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE ledger_entries_2026_10 PARTITION OF ledger_entries
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE ledger_entries_2026_11 PARTITION OF ledger_entries
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
-- ... additional monthly partitions created on a rolling basis.

-- Indexes
CREATE INDEX idx_ledger_transaction_id ON ledger_entries (transaction_id);
    -- Supports "show me both entries for transaction X" — the core reconciliation query.
CREATE INDEX idx_ledger_account_created ON ledger_entries (account_id, created_at DESC);
    -- Supports account statement generation (most-recent-first, bounded by date range = partition pruning).

-- Immutability guard: RBI's audit-trail requirement means these rows must never
-- change after being written. This trigger rejects any UPDATE or DELETE outright.
CREATE OR REPLACE FUNCTION reject_ledger_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'ledger_entries is append-only: % is not permitted (RBI immutable audit trail requirement)', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ledger_no_update
    BEFORE UPDATE ON ledger_entries
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

CREATE TRIGGER trg_ledger_no_delete
    BEFORE DELETE ON ledger_entries
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

-- Citus distribution (tentative — finalized in docs/06-sharding-strategy.md):
-- SELECT create_distributed_table('ledger_entries', 'account_id');
