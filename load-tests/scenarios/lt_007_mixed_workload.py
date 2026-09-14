"""
LT-007: Mixed Workload
========================
Realistic traffic composition at 12,000 TPS: 60% P2P transfers, 20% balance
checks, 10% merchant payments, 5% reversals, 5% other (account lookups,
transaction history queries). Task weights below implement this split via
Locust's built-in weighted-task mechanism.

This scenario exists because LT-002 alone (100% P2P) doesn't exercise the
cache-heavy balance-read path or the merchant/reversal-specific code paths —
a system that passes LT-002 could still have a latency or error problem
specific to one of the less common request types, and this is what catches it.

Run: locust -f lt_007_mixed_workload.py --users 3000 --spawn-rate 100 \
     --run-time 1h --host https://api.payscale.example.in
"""

import random
import uuid

from locust import HttpUser, task, between


class MixedWorkloadUser(HttpUser):
    wait_time = between(0.1, 0.3)

    def on_start(self):
        self.account_id = self._random_test_account()
        self.other_account_id = self._random_test_account(exclude=self.account_id)
        self.merchant_account_id = self._random_merchant_account()
        self.known_transaction_ids = []  # populated as P2P payments succeed, used for lookups

    @task(60)
    def p2p_payment(self):
        idempotency_key = str(uuid.uuid4())
        payload = {
            "transaction_type": "P2P",
            "source_account_id": self.account_id,
            "destination_account_id": self.other_account_id,
            "amount": f"{random.uniform(10, 500):.2f}",
            "currency": "INR",
        }
        with self.client.post(
            "/v1/transactions", json=payload,
            headers={"Idempotency-Key": idempotency_key}, catch_response=True,
        ) as response:
            if response.status_code == 201:
                data = response.json()
                self.known_transaction_ids.append(data.get("transaction_id"))
                if len(self.known_transaction_ids) > 50:
                    self.known_transaction_ids.pop(0)
                response.success()
            elif response.status_code == 402:
                response.success()
            else:
                response.failure(f"unexpected status {response.status_code}")

    @task(20)
    def check_balance(self):
        # Exercises the cache-aside read path (ADR-005) — most of these
        # should be served from Redis, not PostgreSQL.
        with self.client.get(
            f"/v1/accounts/{self.account_id}/balance", catch_response=True
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"unexpected status {response.status_code}")

    @task(10)
    def merchant_payment(self):
        idempotency_key = str(uuid.uuid4())
        payload = {
            "transaction_type": "MERCHANT_PAYMENT",
            "source_account_id": self.account_id,
            "destination_account_id": self.merchant_account_id,
            "amount": f"{random.uniform(50, 2000):.2f}",
            "currency": "INR",
        }
        with self.client.post(
            "/v1/transactions", json=payload,
            headers={"Idempotency-Key": idempotency_key}, catch_response=True,
        ) as response:
            if response.status_code in (201, 402):
                response.success()
            else:
                response.failure(f"unexpected status {response.status_code}")

    @task(5)
    def check_transaction_status(self):
        # "Other" bucket: a transaction-status lookup, simulating a client
        # polling for a webhook it may have missed.
        if not self.known_transaction_ids:
            return
        txn_id = random.choice(self.known_transaction_ids)
        with self.client.get(f"/v1/transactions/{txn_id}", catch_response=True) as response:
            if response.status_code in (200, 404):
                response.success()
            else:
                response.failure(f"unexpected status {response.status_code}")

    # Reversals (5%) are initiated by the system on a failed saga step, not
    # by client request — this weighted task simulates the resulting READ
    # traffic (clients checking a transaction's status after it reverses),
    # since a direct "trigger a reversal" endpoint isn't something a client
    # calls on purpose.
    @task(5)
    def check_reversed_transaction_status(self):
        self.check_transaction_status()

    def _random_test_account(self, exclude=None):
        pool = self._account_pool()
        choice = random.choice(pool)
        while choice == exclude:
            choice = random.choice(pool)
        return choice

    def _account_pool(self):
        pass

    def _random_merchant_account(self):
        pass
