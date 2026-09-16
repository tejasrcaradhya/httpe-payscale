# Failure Mode and Effects Analysis (FMEA): HTTPE — PayScale

RPN = Severity (S) × Occurrence (O) × Detection (D), each scored 1–10. RPN > 200 requires immediate mitigation (per the project's own scoring rule).

## 1. Full FMEA Table

### Carried Over from the Load-Test Bottleneck Report (FM-001 to FM-010)

These are the 10 reference failure modes already used throughout this project's design work (referenced in ADR-001, the Day 6 sharding strategy, and the Day 9 chaos experiments).

| ID | Failure Mode | Effect | S | O | D | RPN | Mitigation |
|---|---|---|---|---|---|---|---|
| FM-001 | Primary DB node failure | All writes fail; in-flight txns lost | 8 | 4 | 2 | 64 | Patroni auto-failover; WAL replication |
| FM-002 | Kafka broker failure (1/3) | Temporary partition unavailability | 5 | 5 | 2 | 50 | Replication factor=3; min.insync=2 |
| FM-003 | Redis cluster node failure | Cache miss spike; DB load 3–5x | 6 | 4 | 2 | 48 | Redis Sentinel failover; bulkhead DB limits |
| FM-004 | Network partition between AZs | Replication lag; potential split-brain | 9 | 3 | 4 | 108 | Fencing tokens; quorum writes |
| FM-005 | Deadlock in accounts table | Affected txns timeout and retry | 4 | 6 | 3 | 72 | OCC eliminates locks; auto-retry with backoff |
| FM-006 | Fraud service timeout | Processing halted; p99 latency spike | 6 | 5 | 2 | 60 | CB-FRAUD with 15s fallback |
| FM-007 | Idempotency key collision | Legitimate txn rejected as duplicate | 7 | 2 | 5 | 70 | UUIDv7; monitoring for unexpected rejections |
| FM-008 | Full Kafka disk | Messages rejected; user-facing errors | 9 | 3 | 2 | 54 | Alerting at 70%; log compaction |
| FM-009 | Certificate expiration (mTLS) | All inter-service comms fail | 10 | 2 | 3 | 60 | Auto-rotation 30d before expiry |
| FM-010 | Memory leak in orchestrator | Gradual OOM; increasing latency | 6 | 5 | 4 | 120 | Heap alerting at 80%; pod recycling every 12h |

### New Failure Modes (FM-011 to FM-022)

| ID | Failure Mode | Category | Effect | S | O | D | RPN | Mitigation |
|---|---|---|---|---|---|---|---|---|
| FM-011 | Bug in saga compensation logic causes double-credit | Application | Sender's reversal credit applied twice for one failed transaction | 9 | 2 | 6 | 108 | Compensation actions are themselves idempotent (keyed by transaction_id + step name); reconciliation cross-checks debit/credit pairs |
| FM-012 | OCC retry storm under extreme hot-partition contention | Application | Retries compound faster than they resolve, approaching livelock | 5 | 3 | 3 | 45 | Bounded `MAX_RETRIES=5`; exponential backoff with jitter (Day 8); LT-008 load test validates this empirically |
| FM-013 | Race condition in webhook delivery sends duplicate webhook | Application | Merchant receives the same status update twice | 4 | 4 | 4 | 64 | Webhook payload includes the same idempotent `event_id`; merchants are instructed to dedupe on it |
| FM-014 | Citus coordinator node failure | Infrastructure | Query routing blocked for ALL shards, even healthy ones | 10 | 2 | 3 | 60 | Coordinator deployed with a streaming-replication standby (Day 6, Section 6) |
| FM-015 | PgBouncer connection pool misconfiguration causes silent leak | Infrastructure | Available connections shrink gradually until exhaustion | 6 | 3 | 5 | 90 | `db_connection_pool_active` alerting (Section A5.6) catches gradual creep, not just hard exhaustion |
| FM-016 | Cross-AZ network partition during active shard rebalancing | Infrastructure | Risk of a shard briefly appearing in two places (split-brain) | 9 | 2 | 5 | 90 | Rebalancing operations use the same fencing-token discipline as Day 8's distributed lock design |
| FM-017 | Silent data corruption from a bad schema migration | Data | A subset of transactions get miscalculated ledger balances, undetected at deploy time | 10 | 3 | 7 | **210** | See Section 2 — mandatory mitigation |
| FM-018 | Backup/restore process fails silently (bad backup, never tested) | Data | A real disaster recovery event discovers the backup was unusable | 8 | 3 | 7 | 168 | Scheduled automated restore-and-checksum verification (not just "backup job exited 0") |
| FM-019 | PII leak via unmasked application logs | Data | KYC document numbers or other PII appear in plaintext in centralized logs | 9 | 3 | 5 | 135 | Centralized log-scrubbing middleware as a defense-in-depth layer, independent of any single service remembering to mask fields |
| FM-020 | Fraud detection model drift causes a false-negative spike | Operational | Real fraud increasingly passes undetected as fraud patterns evolve past the model's training data | 8 | 5 | 6 | **240** | See Section 2 — mandatory mitigation |
| FM-021 | Compromised API credentials / leaked JWT signing key | Operational | Unauthorized transactions could be initiated | 10 | 2 | 5 | 100 | Key rotation policy; anomaly detection on auth patterns; short-lived tokens limit exposure window |
| FM-022 | Regulatory reporting pipeline silently stalls due to a broken alert rule | Operational | Audit consumer lag goes undetected because the very alert meant to catch it is itself misconfigured | 9 | 4 | 7 | **252** | See Section 2 — mandatory mitigation |

## 2. Mitigation for High-RPN Failures (RPN > 200)

Three failure modes exceed the 200 threshold — notably, none of them are the "obvious" infrastructure failures (a dead database node, a dead Kafka broker); all three are failures where the danger isn't the failure itself but **how long it takes to notice**. That pattern is itself a useful finding, not a coincidence.

**FM-017 (RPN 210) — Silent data corruption from a bad migration.** A schema or trigger change that subtly miscalculates balances wouldn't necessarily throw an error — it would just produce quietly wrong numbers. Mitigation: (1) all schema migrations require a staging run against a full production-shaped dataset with automated before/after ledger-sum validation (the conservation-of-money check from Section A1.2 — total balances before must equal total balances after, for any migration that isn't supposed to change them) before being allowed to reach production; (2) the Reconciliation Service's double-entry verification (Day 4) is the production-side backstop — this is exactly the failure mode that service exists to catch, and this FMEA entry is what justifies tightening reconciliation's run frequency rather than treating it as a nice-to-have.

**FM-020 (RPN 240) — Fraud model drift.** Mitigation: scheduled model-performance audits (weekly, not just at initial deployment) comparing the live model's false-negative rate against a held-out labeled dataset of recent confirmed-fraud cases; an automated alert if false-negative rate drifts more than a defined threshold above its baseline; a documented model-retraining cadence rather than a "deploy once and leave it" assumption.

**FM-022 (RPN 252, the highest in the table) — Broken alerting on the alerting.** This is the most dangerous entry precisely because it's a failure of the safety mechanism itself. Mitigation: a **secondary, independent "dead man's switch"** — a separate, simpler monitor that checks not "is the audit consumer lagging" but "has the audit-lag alert rule evaluated successfully in the last N minutes at all." This doesn't try to be smart about detecting the underlying problem; it just confirms the primary detection mechanism is still alive, which is a fundamentally different (and more robust) check than monitoring the same signal twice through the same pipeline.

## 3. Chaos Engineering Experiments for the Top 5 Failure Modes

Ranked by RPN: FM-022 (252), FM-020 (240), FM-017 (210), FM-018 (168), FM-019 (135). These are deliberately different in character from the Day 9 experiments (CE-001–CE-005), which targeted infrastructure kill-switches — these target **data integrity and detection-mechanism failures**, which need a different kind of chaos test entirely.

### CE-006 — Deliberately Deploy a Balance-Miscalculating Migration (targets FM-017)

- **Hypothesis:** The Reconciliation Service's double-entry check flags the discrepancy within one reconciliation cycle, before it affects a meaningful number of accounts.
- **Blast Radius:** A staging environment with a full production-shaped synthetic dataset — never run against real account data.
- **Injection Method:** Deploy a deliberately buggy migration that skips updating `ledger_balance` correctly for one transaction type, run a batch of synthetic transactions through it.
- **Success Criteria:** Reconciliation raises a discrepancy alert within one cycle; the conservation-of-money pre-deploy check (Section 2) independently would have caught this before it ever reached this stage.
- **Rollback Procedure:** Revert the migration; restore the staging database from its pre-migration snapshot.

### CE-007 — Replay a Labeled Fraud Dataset to Measure Model Drift (targets FM-020)

- **Hypothesis:** The scheduled model-performance audit correctly flags a false-negative rate above baseline when fed transactions the current model is known to under-score.
- **Blast Radius:** Shadow/offline evaluation only — no live transactions are affected.
- **Injection Method:** Run a held-out labeled fraud dataset through the current production model version in shadow mode.
- **Success Criteria:** The audit correctly measures and reports the false-negative rate, and triggers an alert if it exceeds the defined drift threshold.
- **Rollback Procedure:** None required (offline evaluation); if run as a live canary instead, revert to the previous model version.

### CE-008 — Break the Audit-Lag Alert Rule Itself (targets FM-022)

- **Hypothesis:** The independent "dead man's switch" monitor detects that the primary alert rule has stopped evaluating, even though the primary alert never fires.
- **Blast Radius:** Staging alerting configuration only.
- **Injection Method:** Simultaneously misconfigure the audit-lag alert rule (so it silently stops evaluating) and stall the audit consumer.
- **Success Criteria:** The secondary heartbeat-based monitor raises an alert within 15 minutes, despite the primary alert path being broken.
- **Rollback Procedure:** Restore the correct alert rule configuration; resume the audit consumer; confirm both monitors report healthy.

### CE-009 — Corrupt a Backup and Run It Through Verification (targets FM-018)

- **Hypothesis:** The automated restore-and-checksum verification pipeline catches a corrupted backup rather than trusting a "backup job exited successfully" signal alone.
- **Blast Radius:** An isolated restore-testing sandbox, never production.
- **Injection Method:** Intentionally truncate a backup file, then run it through the standard verification pipeline.
- **Success Criteria:** Verification fails loudly and alerts, rather than silently accepting a corrupted backup as valid.
- **Rollback Procedure:** Delete the corrupted test artifact; confirm the verification pipeline returns to reporting healthy on a known-good backup.

### CE-010 — Deploy a Log Statement With an Unmasked PII Field (targets FM-019)

- **Hypothesis:** The centralized log-scrubbing middleware redacts the sensitive field before it reaches durable storage, even though the application code "forgot" to mask it explicitly — defense in depth working as intended.
- **Blast Radius:** Staging logging pipeline only.
- **Injection Method:** Deploy a test log statement containing an unmasked KYC document number field.
- **Success Criteria:** The field is redacted before reaching ELK/Loki storage, or an alert fires if a PII-shaped pattern is detected in raw log output.
- **Rollback Procedure:** Remove the test log statement; purge any test log lines that reached the pipeline during the experiment.
