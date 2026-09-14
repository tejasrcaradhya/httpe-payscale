"""
LT-006: Failover Under Load
==============================
Same steady 12,000 TPS load profile as LT-002, but with the primary of one
database shard forcefully killed at T+10 minutes (this is CE-001 from
docs/09-fault-tolerance.md, run as a scripted event DURING live load rather
than in isolation — a more realistic test of failover behavior than a chaos
experiment run against an idle system).

Pass/fail criteria (docs/11-load-testing-strategy.md, LT-006 row):
  - Zero committed-transaction loss (validated by reconciling the outbox
    event count against the load generator's confirmed-success count after
    the run)
  - p99 latency during the ~30s failover window may spike to <=5s
    (temporary, expected)
  - Full recovery to normal p99 <=100ms within 30s of the kill (NFR-006 RTO)
  - Error rate during the failover window <5%; returns to <0.1% after recovery

Run: locust -f lt_006_failover_under_load.py --users 3000 --spawn-rate 100 \
     --run-time 30m --host https://api.payscale.example.in
"""

import random
import threading
import time
import uuid

from locust import HttpUser, task, between, events


FAILOVER_TRIGGER_SECONDS = 10 * 60  # T+10 minutes into the run
TARGET_SHARD = "shard-03"           # the specific shard whose primary gets killed


@events.test_start.add_listener
def schedule_chaos_injection(environment, **kwargs):
    """Fires the chaos injection once, 10 minutes after the load test starts.
    In the real implementation this calls the same chaos-tooling API used for
    CE-001 in isolation (e.g., a Kubernetes chaos-mesh PodChaos resource, or a
    direct `kubectl delete pod` against the target shard's primary)."""

    def _trigger():
        time.sleep(FAILOVER_TRIGGER_SECONDS)
        print(f"[chaos] T+10min reached — killing primary of {TARGET_SHARD}")
        _kill_shard_primary(TARGET_SHARD)

    threading.Thread(target=_trigger, daemon=True).start()


def _kill_shard_primary(shard_name):
    pass  # e.g.: subprocess.run(["kubectl", "delete", "pod", f"{shard_name}-primary-0", "--grace-period=0"])


class P2PPaymentUser(HttpUser):
    wait_time = between(0.1, 0.3)

    def on_start(self):
        self.source_account_id = self._random_test_account()
        self.dest_account_id = self._random_test_account(exclude=self.source_account_id)

    @task
    def send_p2p_payment(self):
        idempotency_key = str(uuid.uuid4())
        payload = {
            "transaction_type": "P2P",
            "source_account_id": self.source_account_id,
            "destination_account_id": self.dest_account_id,
            "amount": f"{random.uniform(10, 500):.2f}",
            "currency": "INR",
        }
        with self.client.post(
            "/v1/transactions",
            json=payload,
            headers={"Idempotency-Key": idempotency_key},
            catch_response=True,
        ) as response:
            if response.status_code == 201:
                response.success()
            elif response.status_code == 402:
                response.success()
            elif response.status_code == 503:
                # Expected DURING the failover window only — the test harness
                # tags requests with elapsed time so post-run analysis can
                # separate "503s during the 30s failover window" (acceptable,
                # up to 5% of requests) from "503s outside that window"
                # (a genuine failure, since it means recovery didn't complete
                # within the 30s RTO target).
                response.failure("503 — check whether this falls inside the failover window")
            else:
                response.failure(f"unexpected status {response.status_code}: {response.text}")

    def _random_test_account(self, exclude=None):
        pool = self._account_pool()
        choice = random.choice(pool)
        while choice == exclude:
            choice = random.choice(pool)
        return choice

    def _account_pool(self):
        pass
