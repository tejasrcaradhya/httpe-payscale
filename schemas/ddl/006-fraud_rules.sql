-- =====================================================================
-- 006-fraud_rules.sql
-- Configurable fraud detection rules read by the Fraud Detection Service
-- on every transaction (15ms SLA — FR-007). Small, mostly-static table,
-- so it's a good candidate for a Citus reference table (copied to every
-- worker node, avoiding a network hop to look it up on the hot path).
-- =====================================================================

CREATE TYPE fraud_rule_type_enum AS ENUM ('VELOCITY', 'THRESHOLD', 'PATTERN', 'ML_MODEL');
CREATE TYPE fraud_rule_severity_enum AS ENUM ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL');

CREATE TABLE fraud_rules (
    rule_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),  -- UUIDv7
    rule_name        VARCHAR(255) NOT NULL,
    rule_type        fraud_rule_type_enum NOT NULL,
    rule_definition  JSONB NOT NULL,     -- e.g. {"max_txns_per_minute": 10} for a VELOCITY rule
    severity         fraud_rule_severity_enum NOT NULL DEFAULT 'MEDIUM',
    is_active        BOOLEAN NOT NULL DEFAULT true,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_fraud_rules_name UNIQUE (rule_name)
);

-- Indexes
CREATE INDEX idx_fraud_rules_active ON fraud_rules (rule_id) WHERE is_active = true;
    -- Partial index: the Fraud Detection Service only ever reads active rules on the
    -- hot path — this keeps that lookup fast and excludes disabled/retired rules.

CREATE TRIGGER trg_fraud_rules_updated_at
    BEFORE UPDATE ON fraud_rules
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

-- Citus: small, mostly-static, read on every transaction — reference table
-- SELECT create_reference_table('fraud_rules');
