-- =====================================================================
-- 008-notification_log.sql
-- Delivery tracking for every notification sent (push/SMS/email/websocket).
-- Written by the Notification Service as it consumes events from Kafka
-- (see diagrams/p2p-payment-flow.puml). Used both for customer support
-- ("did the user get notified?") and for the retry worker that re-attempts
-- FAILED deliveries.
-- =====================================================================

CREATE TYPE notification_channel_enum AS ENUM ('PUSH', 'SMS', 'EMAIL', 'WEBSOCKET');
CREATE TYPE notification_status_enum AS ENUM ('QUEUED', 'SENT', 'DELIVERED', 'FAILED');

CREATE TABLE notification_log (
    notification_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),  -- UUIDv7
    transaction_id    UUID,                                        -- NULL for non-transactional notifications (e.g., KYC status updates)
    user_id            UUID NOT NULL,
    channel            notification_channel_enum NOT NULL,
    status             notification_status_enum NOT NULL DEFAULT 'QUEUED',
    payload            JSONB NOT NULL DEFAULT '{}',
    attempt_count      INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at        TIMESTAMPTZ,

    CONSTRAINT fk_notification_user FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Indexes
CREATE INDEX idx_notification_transaction_id ON notification_log (transaction_id);
    -- Support-tool lookup: "what notifications went out for transaction X".
CREATE INDEX idx_notification_user_id ON notification_log (user_id, created_at DESC);
    -- Supports a user's own notification history view.
CREATE INDEX idx_notification_retry_queue ON notification_log (notification_id) WHERE status = 'FAILED';
    -- Partial index: the retry worker only ever scans FAILED rows — this keeps that
    -- scan cheap regardless of how large the (mostly SENT/DELIVERED) table grows.

-- Citus distribution (tentative — finalized in docs/06-sharding-strategy.md):
-- SELECT create_distributed_table('notification_log', 'user_id');
