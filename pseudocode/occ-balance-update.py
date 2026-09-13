"""
occ-balance-update.py
======================
Optimistic Concurrency Control for account balance mutations.

Core idea: instead of locking a row before reading it (pessimistic locking),
we read the balance + a version counter, compute the new balance in the
application, then write it back with an UPDATE whose WHERE clause checks
that the version hasn't changed since we read it. If another transaction
committed a change in between, the UPDATE affects 0 rows, and we retry.

See docs/08-concurrency-control.md for the formal correctness argument
(this mechanism prevents double-spend and write-skew — Section A5.4's
write-skew scenario is worked through explicitly there).
"""

import random
import time

MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 0.05  # 50ms


class InsufficientBalanceError(Exception):
    pass


class AccountNotFoundError(Exception):
    pass


class AccountNotActiveError(Exception):
    pass


class ConcurrencyConflictError(Exception):
    """Raised when all retries are exhausted without a successful update."""
    pass


def debit_account(db, account_id, amount):
    """Debit `amount` from account_id's available_balance using OCC."""
    return _occ_balance_update(db, account_id, delta=-amount, operation="DEBIT")


def credit_account(db, account_id, amount):
    """Credit `amount` to account_id's available_balance using OCC."""
    return _occ_balance_update(db, account_id, delta=+amount, operation="CREDIT")


def _occ_balance_update(db, account_id, delta, operation):
    for attempt in range(1, MAX_RETRIES + 1):
        # Step 1: read current balance + version (READ COMMITTED is sufficient —
        # see docs/08-concurrency-control.md for why we don't need SERIALIZABLE here)
        row = db.query_one(
            "SELECT available_balance, ledger_balance, version, status "
            "FROM accounts WHERE account_id = %s",
            [account_id],
        )
        if row is None:
            raise AccountNotFoundError(account_id)
        if row.status != "ACTIVE":
            raise AccountNotActiveError(f"account {account_id} is {row.status}")

        new_available = row.available_balance + delta
        new_ledger = row.ledger_balance + delta

        # Step 2: validate BEFORE attempting the write (cheap fail-fast path —
        # no point racing to write an update we already know is invalid)
        if operation == "DEBIT" and new_available < 0:
            raise InsufficientBalanceError(
                f"account {account_id}: balance {row.available_balance} < debit amount {-delta}"
            )

        # Step 3: the actual compare-and-swap. The WHERE clause's version check
        # is what makes this optimistic concurrency control rather than a plain
        # UPDATE — Postgres evaluates the WHERE clause against the CURRENT
        # committed row, not the value we read in Step 1, so a concurrent
        # committed change is detected here, atomically, by the database itself.
        rows_affected = db.execute(
            """
            UPDATE accounts
            SET available_balance = %s,
                ledger_balance = %s,
                version = version + 1
            WHERE account_id = %s AND version = %s
            """,
            [new_available, new_ledger, account_id, row.version],
        )

        if rows_affected == 1:
            return {
                "account_id": account_id,
                "operation": operation,
                "new_available_balance": new_available,
                "new_version": row.version + 1,
                "attempts": attempt,
            }

        # rows_affected == 0 means someone else committed a change to this row
        # between our read and our write. This is a conflict, not an error —
        # retry with a fresh read.
        if attempt < MAX_RETRIES:
            backoff = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
            jitter = random.uniform(0, backoff * 0.5)
            time.sleep(backoff + jitter)
            continue

    # All retries exhausted — surface this to the caller (the Transaction
    # Orchestrator), which will fail the saga step and, if this was the
    # second leg of a cross-shard transfer, trigger compensation.
    raise ConcurrencyConflictError(
        f"account {account_id}: {operation} failed after {MAX_RETRIES} attempts (high contention)"
    )
