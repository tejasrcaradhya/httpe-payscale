-- =====================================================================
-- 007-merchant_settlements.sql
-- One row per merchant per settlement batch (see the Day 4 batch
-- settlement flow). The unique constraint on (merchant_account_id,
-- settlement_date) is what makes the nightly settlement job idempotent —
-- re-running it for a day that's already settled simply hits a constraint
-- violation for merchants already processed, rather than double-settling them.
-- =====================================================================

CREATE TYPE settlement_status_enum AS ENUM ('PENDING', 'SETTLED', 'FAILED');

CREATE TABLE merchant_settlements (
    settlement_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),  -- UUIDv7
    merchant_account_id    UUID NOT NULL,
    settlement_batch_id    UUID NOT NULL,
    gross_amount           NUMERIC(18,4) NOT NULL,
    fee_amount              NUMERIC(18,4) NOT NULL DEFAULT 0,
    net_amount              NUMERIC(18,4) NOT NULL,
    transaction_count       INTEGER NOT NULL CHECK (transaction_count > 0),
    status                  settlement_status_enum NOT NULL DEFAULT 'PENDING',
    settlement_date         DATE NOT NULL,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at             TIMESTAMPTZ,

    CONSTRAINT fk_settlement_merchant FOREIGN KEY (merchant_account_id) REFERENCES accounts(account_id),
    CONSTRAINT uq_settlement_merchant_day UNIQUE (merchant_account_id, settlement_date)
        -- Guarantees idempotency: one settlement row per merchant per day, full stop.
);

-- Indexes
CREATE INDEX idx_settlements_batch_id ON merchant_settlements (settlement_batch_id);
    -- Supports "show me everything in batch X" for ops/debugging a specific run.
CREATE INDEX idx_settlements_merchant_history ON merchant_settlements (merchant_account_id, settlement_date DESC);
    -- Supports the merchant-facing "settlement history" view, most-recent-first.

-- Citus distribution (tentative — finalized in docs/06-sharding-strategy.md):
-- SELECT create_distributed_table('merchant_settlements', 'merchant_account_id');
