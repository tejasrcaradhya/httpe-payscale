"""
circuit-breaker.py
====================
Generic circuit breaker implementing the state machine in
diagrams/circuit-breaker-fsm.puml. Each protected dependency gets its own
CircuitBreaker instance with its own thresholds and fallback — this is a
bulkhead property in itself: one breaker tripping can never affect another.
"""

import time
import threading


class CircuitState:
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    def __init__(self, name, failure_threshold, window_seconds, reset_timeout_seconds,
                 half_open_probe_count=1):
        self.name = name
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.reset_timeout_seconds = reset_timeout_seconds
        self.half_open_probe_count = half_open_probe_count

        self._state = CircuitState.CLOSED
        self._failure_timestamps = []
        self._opened_at = None
        self._half_open_probes_in_flight = 0
        self._lock = threading.Lock()

    def call(self, fn, fallback_fn, *args, **kwargs):
        """Execute fn() if the circuit allows it; otherwise run fallback_fn() instead."""
        with self._lock:
            self._maybe_transition_to_half_open()

            if self._state == CircuitState.OPEN:
                return fallback_fn(*args, **kwargs)

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_probes_in_flight >= self.half_open_probe_count:
                    # Already probing at capacity — treat like OPEN for this caller
                    return fallback_fn(*args, **kwargs)
                self._half_open_probes_in_flight += 1

        # Execute the real call OUTSIDE the lock so a slow dependency doesn't
        # block other callers from checking circuit state.
        try:
            result = fn(*args, **kwargs)
        except Exception as e:
            self._on_failure()
            return fallback_fn(*args, **kwargs)
        else:
            self._on_success()
            return result

    def _on_success(self):
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._half_open_probes_in_flight -= 1
                self._close()
            elif self._state == CircuitState.CLOSED:
                pass  # nothing to do — failure window only tracks failures

    def _on_failure(self):
        with self._lock:
            now = time.monotonic()
            if self._state == CircuitState.HALF_OPEN:
                self._half_open_probes_in_flight -= 1
                self._open(now)
                return

            self._failure_timestamps.append(now)
            self._failure_timestamps = [
                t for t in self._failure_timestamps if now - t <= self.window_seconds
            ]
            if len(self._failure_timestamps) >= self.failure_threshold:
                self._open(now)

    def _open(self, now):
        self._state = CircuitState.OPEN
        self._opened_at = now
        self._failure_timestamps = []
        # In production: emit circuit_breaker_state metric (Section A5.6) and log
        # the transition — an OPEN state lasting >2 minutes triggers an alert.

    def _close(self):
        self._state = CircuitState.CLOSED
        self._opened_at = None
        self._failure_timestamps = []

    def _maybe_transition_to_half_open(self):
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self.reset_timeout_seconds:
                self._state = CircuitState.HALF_OPEN
                self._half_open_probes_in_flight = 0


# --- The 7 configured instances (thresholds justified in docs/09-fault-tolerance.md) ---

CB_FRAUD = CircuitBreaker("CB-FRAUD", failure_threshold=3, window_seconds=10, reset_timeout_seconds=15)
CB_NOTIFY = CircuitBreaker("CB-NOTIFY", failure_threshold=5, window_seconds=30, reset_timeout_seconds=60)
CB_DB_PRIMARY = CircuitBreaker("CB-DB-PRIMARY", failure_threshold=2, window_seconds=5, reset_timeout_seconds=10)
CB_DB_REPLICA = CircuitBreaker("CB-DB-REPLICA", failure_threshold=5, window_seconds=10, reset_timeout_seconds=30)
CB_CACHE = CircuitBreaker("CB-CACHE", failure_threshold=3, window_seconds=5, reset_timeout_seconds=20)
CB_EXCHANGE = CircuitBreaker("CB-EXCHANGE", failure_threshold=3, window_seconds=10, reset_timeout_seconds=30)
CB_SETTLEMENT = CircuitBreaker("CB-SETTLEMENT", failure_threshold=2, window_seconds=5, reset_timeout_seconds=60)


# --- Example usage: fraud detection call on the synchronous payment path ---

def evaluate_fraud_with_breaker(transaction, fraud_service_client):
    def call_fraud_service():
        return fraud_service_client.evaluate(transaction)  # real network call, 15ms SLA

    def fallback_allow_with_review_flag():
        return {"decision": "APPROVED", "flagged_for_manual_review": True}

    return CB_FRAUD.call(call_fraud_service, fallback_allow_with_review_flag)
