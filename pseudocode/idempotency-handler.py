"""
idempotency-handler.py
========================
Handles client-generated idempotency keys (FR-002) with a 24-hour TTL in
Redis. Goes one step further than the simple "check cache, then process"
version implied in the Day 4 sequence diagram: it also protects against
CONCURRENT duplicate submissions — e.g., a UI double-tap plus a client-side
network retry both in flight for the same idempotency key at the same time,
which a simple check-then-process race could let through twice.
"""

import time

IDEMPOTENCY_TTL_SECONDS = 86400          # 24 hours, per FR-002
IN_PROGRESS_SAFETY_TTL_SECONDS = 30      # bounds how long a crashed request can block a retry


class DuplicateRequestInProgressError(Exception):
    """Raised when a request with this idempotency key is already being processed."""
    pass


def check_and_reserve(redis, idempotency_key):
    """
    Call this BEFORE processing a transaction. Returns:
      - ("CACHED_RESULT", result)  if this key was already fully processed
      - ("PROCEED", None)          if this caller now owns processing this key
    Raises DuplicateRequestInProgressError if another request with the same
    key is currently mid-processing (the concurrent-duplicate case).
    """
    # Atomic "set if not exists" — this is the actual mutual-exclusion point.
    # Only one caller can win this for a given key.
    acquired = redis.set(
        f"idempotency:{idempotency_key}",
        value="IN_PROGRESS",
        nx=True,
        ex=IN_PROGRESS_SAFETY_TTL_SECONDS,
    )

    if acquired:
        return ("PROCEED", None)

    # Key already exists — either it's a finished result, or another request
    # is still processing it right now.
    existing = redis.get(f"idempotency:{idempotency_key}")

    if existing == "IN_PROGRESS":
        # A concurrent duplicate — the earlier request hasn't finished yet.
        # Do NOT process again. The caller should surface a retryable error
        # to the client rather than silently proceeding.
        raise DuplicateRequestInProgressError(idempotency_key)

    # Otherwise it's a completed, cached result (a JSON-serialized transaction outcome)
    return ("CACHED_RESULT", existing)


def complete(redis, idempotency_key, result):
    """Call this AFTER processing succeeds — overwrites the IN_PROGRESS marker
    with the real result and extends the TTL to the full 24 hours."""
    redis.set(
        f"idempotency:{idempotency_key}",
        value=result,  # JSON-serialized {transaction_id, status, ...}
        ex=IDEMPOTENCY_TTL_SECONDS,
    )


def release_on_failure(redis, idempotency_key):
    """Call this if processing raises an unhandled exception AFTER reserving
    the key but BEFORE completing it — otherwise a legitimate retry would be
    blocked as a 'duplicate' for up to IN_PROGRESS_SAFETY_TTL_SECONDS for no
    reason (the original attempt never actually succeeded)."""
    current = redis.get(f"idempotency:{idempotency_key}")
    if current == "IN_PROGRESS":
        redis.delete(f"idempotency:{idempotency_key}")
    # If it's no longer IN_PROGRESS, someone else already completed or is
    # handling cleanup — don't clobber a real result.


# --- Example usage inside the Transaction Orchestrator ---

def process_transaction_idempotently(redis, idempotency_key, process_fn):
    status, cached = check_and_reserve(redis, idempotency_key)
    # DuplicateRequestInProgressError propagates to the caller if raised —
    # the API layer turns this into a 409 Conflict with a "retry shortly" hint,
    # rather than a silent no-op, since we don't yet know the outcome.

    if status == "CACHED_RESULT":
        return cached

    try:
        result = process_fn()  # the actual saga execution
    except Exception:
        release_on_failure(redis, idempotency_key)
        raise
    else:
        complete(redis, idempotency_key, result)
        return result
