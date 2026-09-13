"""
saga-orchestrator.py
=====================
Saga state machine for the P2P payment transaction. Executes a sequence of
local steps, persisting each state transition to transaction_events (so an
in-flight saga survives an orchestrator restart), and runs compensating
actions in reverse order if any step fails after earlier steps committed.

See diagrams/p2p-payment-flow.puml for the sequence-diagram version of this
same logic, including Failure Scenario 3 (receiver's shard down mid-saga).
"""

import uuid

from occ_balance_update import (
    debit_account,
    credit_account,
    InsufficientBalanceError,
    AccountNotFoundError,
    ConcurrencyConflictError,
)


class SagaStep:
    def __init__(self, name, execute, compensate):
        self.name = name
        self.execute = execute       # fn(context) -> result
        self.compensate = compensate  # fn(context) -> None (best-effort undo)


class SagaFailedError(Exception):
    def __init__(self, message, failed_step, reason):
        super().__init__(message)
        self.failed_step = failed_step
        self.reason = reason


class TransactionOrchestrator:

    def __init__(self, db, kafka_producer, redis):
        self.db = db
        self.kafka = kafka_producer
        self.redis = redis

    def process_p2p_payment(self, source_account_id, destination_account_id, amount, idempotency_key):
        # --- Idempotency short-circuit (Failure Scenario 1) ---
        cached = self.redis.get(f"idempotency:{idempotency_key}")
        if cached is not None:
            return cached  # already processed — return the original result, do nothing else

        transaction_id = str(uuid.uuid4())  # UUIDv7 in the real implementation
        saga_id = str(uuid.uuid4())
        context = {
            "transaction_id": transaction_id,
            "saga_id": saga_id,
            "source_account_id": source_account_id,
            "destination_account_id": destination_account_id,
            "amount": amount,
        }

        self._record_event(saga_id, transaction_id, from_state=None, to_state="INITIATED")

        # --- Same-shard fast path vs cross-shard saga (Day 6 sharding strategy) ---
        if self._same_shard(source_account_id, destination_account_id):
            result = self._execute_same_shard(context)
        else:
            result = self._execute_cross_shard_saga(context)

        self.redis.set(f"idempotency:{idempotency_key}", result, ttl_seconds=86400)  # 24hr TTL, FR-002
        return result

    def _execute_same_shard(self, context):
        """Both accounts on one shard: no saga needed, just one local ACID transaction."""
        with self.db.transaction():
            debit_account(self.db, context["source_account_id"], context["amount"])
            credit_account(self.db, context["destination_account_id"], context["amount"])
            self._write_ledger_entries(context)
            self._write_outbox_event(context, event_type="COMPLETED")
        self._record_event(context["saga_id"], context["transaction_id"], "INITIATED", "COMPLETED")
        return {"transaction_id": context["transaction_id"], "status": "COMPLETED"}

    def _execute_cross_shard_saga(self, context):
        """Cross-shard: run each step as an independent local transaction; compensate on failure."""

        steps = [
            SagaStep(
                name="DEBIT_SENDER",
                execute=lambda ctx: debit_account(self.db, ctx["source_account_id"], ctx["amount"]),
                compensate=lambda ctx: credit_account(self.db, ctx["source_account_id"], ctx["amount"]),
            ),
            SagaStep(
                name="CREDIT_RECEIVER",
                execute=lambda ctx: credit_account(self.db, ctx["destination_account_id"], ctx["amount"]),
                compensate=lambda ctx: None,  # nothing to undo — this step never committed if it failed
            ),
        ]

        completed_steps = []
        try:
            for step in steps:
                self._record_event(context["saga_id"], context["transaction_id"],
                                    from_state=completed_steps[-1].name if completed_steps else "INITIATED",
                                    to_state=step.name)
                step.execute(context)
                completed_steps.append(step)

            self._write_ledger_entries(context)
            self._write_outbox_event(context, event_type="COMPLETED")
            self._record_event(context["saga_id"], context["transaction_id"], "CREDIT_RECEIVER", "COMPLETED")
            return {"transaction_id": context["transaction_id"], "status": "COMPLETED"}

        except (InsufficientBalanceError, AccountNotFoundError) as e:
            # No compensation needed — nothing committed yet if this is the first step,
            # or the failure was a pre-write validation, not a partial commit.
            self._mark_failed(context, reason=str(e))
            raise SagaFailedError("payment failed validation", step.name, str(e))

        except ConcurrencyConflictError as e:
            # A step failed AFTER one or more earlier steps already committed.
            # This is exactly Failure Scenario 3: run compensations in reverse order.
            self._compensate(context, completed_steps)
            raise SagaFailedError("payment reversed after partial failure", step.name, str(e))

    def _compensate(self, context, completed_steps):
        for step in reversed(completed_steps):
            self._record_event(context["saga_id"], context["transaction_id"],
                                from_state=step.name, to_state=f"COMPENSATING_{step.name}")
            step.compensate(context)  # best-effort; a failure here is escalated, not silently swallowed
        self._mark_reversed(context)
        self._record_event(context["saga_id"], context["transaction_id"], "COMPENSATING", "REVERSED")

    def recover_incomplete_sagas(self):
        """Called on orchestrator startup: find sagas that never reached a terminal
        state (COMPLETED/FAILED/REVERSED) and resume or compensate them, by replaying
        their transaction_events history rather than assuming any in-memory state."""
        incomplete = self.db.query(
            "SELECT DISTINCT saga_id FROM transaction_events te "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM transaction_events te2 "
            "  WHERE te2.saga_id = te.saga_id AND te2.to_state IN ('COMPLETED','FAILED','REVERSED')"
            ")"
        )
        for saga in incomplete:
            events = self.db.query(
                "SELECT * FROM transaction_events WHERE saga_id = %s ORDER BY created_at",
                [saga.saga_id],
            )
            self._resume_from_event_history(events)

    # --- helpers (persistence / messaging plumbing) ---

    def _record_event(self, saga_id, transaction_id, from_state, to_state):
        self.db.execute(
            "INSERT INTO transaction_events (transaction_id, saga_id, event_type, from_state, to_state) "
            "VALUES (%s, %s, 'STATE_TRANSITION', %s, %s)",
            [transaction_id, saga_id, from_state, to_state],
        )

    def _write_ledger_entries(self, context):
        pass  # INSERT INTO ledger_entries (one DEBIT row, one CREDIT row) — same DB transaction

    def _write_outbox_event(self, context, event_type):
        pass  # INSERT INTO outbox_events — same DB transaction as the balance write (Day 7 Outbox Pattern)

    def _mark_failed(self, context, reason):
        pass  # UPDATE transactions SET status='FAILED', failure_reason=reason

    def _mark_reversed(self, context):
        pass  # UPDATE transactions SET status='REVERSED'

    def _same_shard(self, account_a, account_b):
        pass  # hash(account_a) == hash(account_b) — Day 6 routing layer
