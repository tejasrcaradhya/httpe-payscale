-- =====================================================================
-- 005-transaction_events.sql
-- Event-sourced log of every saga state transition for a transaction —
-- this is what lets the Transaction Orchestrator recover an in-flight
-- saga after a restart (replay events for a saga_id) and what feeds the
-- Audit & Compliance Service's immutable trail. Append-only, like
-- ledger_entries.
--
-- Depends on: reject_ledger_mutation() defined in 003-ledger_entries.sql
-- =====================================================================

CREATE TYPE txn_event_type_enum AS ENUM ('STATE_TRANSITION', 'COMPENSATION', 'RETRY');

CREATE TABLE transaction_events (
    event_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),  -- UUIDv7
    transaction_id  UUID NOT NULL,
    saga_id         UUID NOT NULL,
    event_type      txn_event_type_enum NOT NULL,
    from_state      VARCHAR(50),                                 -- NULL for the saga's first event
    to_state        VARCHAR(50) NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}',                 -- step-specific detail (e.g., failure_reason, retry_count)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes
CREATE INDEX idx_txn_events_saga_id ON transaction_events (saga_id, created_at);
    -- The primary access pattern: "replay all events for saga X in order" — used both
    -- for saga recovery after an orchestrator restart and for audit investigation.
CREATE INDEX idx_txn_events_transaction_id ON transaction_events (transaction_id);
    -- Supports "show me the full history for transaction Y" from a support/ops tool.

-- Immutability guard, same rationale as ledger_entries
CREATE TRIGGER trg_txn_events_no_update
    BEFORE UPDATE ON transaction_events
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

CREATE TRIGGER trg_txn_events_no_delete
    BEFORE DELETE ON transaction_events
    FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

-- Citus distribution (tentative — finalized in docs/06-sharding-strategy.md):
-- SELECT create_distributed_table('transaction_events', 'transaction_id');
