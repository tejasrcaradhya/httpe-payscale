"""
LT-002: Target Load
=====================
Sustained load at the 12,000 TPS target for 60 minutes — the primary
"does the architecture actually work" validation.

To sustain 12,000 TPS with an expected ~40ms average response time, the
concurrency math is: TPS = concurrent_users / avg_response_time_seconds,
so concurrent_users ≈ 12,000 × 0.04 = 480 minimum "in-flight" requests.
In practice, Locust users include think-time between requests, so the
actual simulated-user count is provisioned well above that floor —
run with ~3,000 simulated users at a spawn rate of 100 users/second to
reach steady state within the 10-minute warm-up window.

Run: locust -f lt_002_target_load.py --users 3000 --spawn-rate 100 \
     --run-time 1h --host https://api.payscale.example.in
"""

import random
import uuid

from locust import HttpUser, task, between


class P2PPaymentUser(HttpUser):
    wait_time = between(0.1, 0.3)  # light think-time between requests per simulated user

    def on_start(self):
        # In a real run: authenticate and pull a pair of test account IDs
        # from a pre-seeded pool of ~100,000 synthetic accounts, so traffic
        # spreads realistically across shards rather than hammering one account.
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
            # Pass/fail criteria (see docs/11-load-testing-strategy.md, LT-002 row):
            # p50 <= 50ms, p99 <= 100ms, p99.9 <= 250ms, error rate < 0.1%
            if response.status_code == 201:
                response.success()
            elif response.status_code == 402:
                # Expected outcome for a deliberately underfunded test account —
                # not counted as a system error, just a business-logic rejection.
                response.success()
            else:
                response.failure(f"unexpected status {response.status_code}: {response.text}")

    def _random_test_account(self, exclude=None):
        pool = self._account_pool()  # pre-seeded pool, loaded once at test-start
        choice = random.choice(pool)
        while choice == exclude:
            choice = random.choice(pool)
        return choice

    def _account_pool(self):
        pass  # loaded from a fixture file in the real implementation
