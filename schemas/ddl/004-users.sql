-- =====================================================================
-- 004-users.sql
-- User profile and KYC status. Sensitive identity document numbers are
-- stored as a salted hash, never plaintext, per PCI-DSS/DPDPA guidance
-- (Section A2.3) — the service that verifies KYC documents at onboarding
-- time is a separate, more tightly-scoped system not covered by this DDL.
-- =====================================================================

CREATE TYPE kyc_status_enum AS ENUM ('PENDING', 'VERIFIED', 'REJECTED');
CREATE TYPE kyc_document_type_enum AS ENUM ('AADHAAR', 'PAN', 'PASSPORT', 'VOTER_ID');
CREATE TYPE risk_rating_enum AS ENUM ('LOW', 'MEDIUM', 'HIGH');

CREATE TABLE users (
    user_id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),  -- UUIDv7
    full_name                 VARCHAR(255) NOT NULL,
    phone_number               VARCHAR(15) NOT NULL,
    email                      VARCHAR(255),
    kyc_status                 kyc_status_enum NOT NULL DEFAULT 'PENDING',
    kyc_document_type          kyc_document_type_enum,
    kyc_document_number_hash   VARCHAR(256),   -- SHA-256 hash of the document number; never store plaintext
    date_of_birth              DATE,
    risk_rating                risk_rating_enum NOT NULL DEFAULT 'LOW',
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_users_phone UNIQUE (phone_number),
    CONSTRAINT uq_users_email UNIQUE (email)
);

-- Indexes
CREATE INDEX idx_users_kyc_status ON users (kyc_status) WHERE kyc_status <> 'VERIFIED';
    -- Partial index: supports the KYC-review queue (only PENDING/REJECTED users need review);
    -- the (large, steady-state) VERIFIED population doesn't need to be indexed for this purpose.

CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

-- Now that users exists, add the FK from accounts that was deferred in 001-accounts.sql
ALTER TABLE accounts
    ADD CONSTRAINT fk_accounts_user FOREIGN KEY (user_id) REFERENCES users(user_id);

-- Citus distribution: at target scale (35M users) this table is too large for a
-- reference table (those are meant for small, mostly-static dimension tables).
-- Distribute by user_id instead:
-- SELECT create_distributed_table('users', 'user_id');
