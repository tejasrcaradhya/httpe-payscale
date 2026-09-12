-- =====================================================================
-- 001-accounts.sql
-- Accounts table: one user may hold multiple accounts (savings, wallet,
-- merchant, etc.). Balance fields split into available vs ledger balance
-- per FR-010. Uses Optimistic Concurrency Control via `version`.
-- =====================================================================

-- Shared trigger function for auto-updating `updated_at`.
-- Other DDL files in this project reuse this same function.
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TYPE account_type_enum AS ENUM ('SAVINGS', 'CURRENT', 'WALLET', 'MERCHANT', 'SETTLEMENT_POOL');
CREATE TYPE account_status_enum AS ENUM ('ACTIVE', 'FROZEN', 'SUSPENDED', 'CLOSED');
CREATE TYPE account_tier_enum AS ENUM ('BASIC', 'PREMIUM', 'MERCHANT');

CREATE TABLE accounts (
    account_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),  -- UUIDv7 generated at application layer for time-ordered distribution
    user_id             UUID NOT NULL,
    account_type        account_type_enum NOT NULL,
    currency            CHAR(3) NOT NULL DEFAULT 'INR',
    available_balance   NUMERIC(18,4) NOT NULL DEFAULT 0 CHECK (available_balance >= 0),
    ledger_balance      NUMERIC(18,4) NOT NULL DEFAULT 0,
    version             BIGINT NOT NULL DEFAULT 1,                   -- OCC version counter
    status              account_status_enum NOT NULL DEFAULT 'ACTIVE',
    tier                account_tier_enum NOT NULL DEFAULT 'BASIC',  -- BASIC=50 TPS, PREMIUM=200, MERCHANT=1000 (enforced at API Gateway)
    shard_key           INTEGER NOT NULL,                            -- hash(account_id) mod N; finalized in Day 6 sharding strategy
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
    -- Note: the FK to users(user_id) is added in 004-users.sql via ALTER TABLE,
    -- since these scripts run in numeric order and the users table doesn't exist yet.
);

-- Indexes
CREATE INDEX idx_accounts_user_id ON accounts (user_id);
    -- Supports "one user may have multiple accounts" lookups (e.g., account list screen).
CREATE INDEX idx_accounts_shard_key ON accounts (shard_key);
    -- Supports shard-routing diagnostics and rebalancing queries.
CREATE INDEX idx_accounts_status_active ON accounts (account_id) WHERE status = 'ACTIVE';
    -- Partial index: the hot path (payment processing) only ever touches ACTIVE accounts,
    -- so this keeps the index small and fast rather than indexing FROZEN/CLOSED accounts too.

-- Trigger: keep updated_at current on every write
CREATE TRIGGER trg_accounts_updated_at
    BEFORE UPDATE ON accounts
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

-- Citus distribution (tentative — finalized in docs/06-sharding-strategy.md):
-- SELECT create_distributed_table('accounts', 'account_id');
