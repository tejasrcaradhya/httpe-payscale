# Capacity Planning & Cost Analysis: HTTPE — PayScale

**Executive Summary:** Prices the architecture at three scales using real AWS ap-south-1 rates. The 12,000 TPS target comes in at $20,329.84/month — 45% of the $45,000 budget ceiling — and the same new architecture would even cost less than the current system if run at today's 1,200 TPS load.

All pricing is AWS on-demand, ap-south-1 (Mumbai), sourced from current published rates (August 2026) — see citations at the end of this document. Instance counts follow the architecture designed across Days 3–9, sized per component role (not a flat multiplier), and the database/Kafka node counts follow the Day 6 sharding math rather than scaling linearly with TPS.

## 1. Cost at Three Scales

### 1,200 TPS (Current Load, Run on the New Architecture)

| Component | Instance Type | Count | Monthly Cost |
|---|---|---|---|
| API Gateway | c5.xlarge | 2 | $248.20 |
| Load Balancer (AWS ALB, managed) | — | — | $60.00 |
| Transaction Orchestrator | m5.xlarge | 2 | $294.92 |
| Payment Processing Service | m5.2xlarge | 2 | $589.84 |
| Account Service | m5.xlarge | 2 | $294.92 |
| Fraud Detection Service | m5.2xlarge | 2 | $589.84 |
| Notification Service | m5.large | 2 | $147.46 |
| Reconciliation Service | m5.large | 2 | $147.46 |
| Audit & Compliance Service | m5.large | 2 | $147.46 |
| Kafka Cluster | r5.2xlarge | 3 | $1,138.80 |
| PostgreSQL + Citus (2 shards × 3) | r5.2xlarge | 6 | $2,277.60 |
| Redis Cluster | r5.large | 3 | $284.70 |
| Observability Stack | m5.xlarge | 2 | $294.92 |
| EBS gp3 Storage (3,000 GB) | — | — | $273.60 |
| Data Transfer Out (1,600 GB) | — | — | $144.00 |
| **TOTAL** | | | **$6,933.72/mo** |

### 12,000 TPS (Target)

| Component | Instance Type | Count | Monthly Cost |
|---|---|---|---|
| API Gateway | c5.xlarge | 4 | $496.40 |
| Load Balancer (AWS ALB, managed) | — | — | $320.00 |
| Transaction Orchestrator | m5.xlarge | 8 | $1,179.68 |
| Payment Processing Service | m5.2xlarge | 12 | $3,539.04 |
| Account Service | m5.xlarge | 6 | $884.76 |
| Fraud Detection Service | m5.2xlarge | 4 | $1,179.68 |
| Notification Service | m5.large | 3 | $221.19 |
| Reconciliation Service | m5.large | 3 | $221.19 |
| Audit & Compliance Service | m5.large | 4 | $294.92 |
| Kafka Cluster | r5.2xlarge | 3 | $1,138.80 |
| PostgreSQL + Citus (6 shards × 3) | r5.2xlarge | 18 | $6,832.80 |
| Redis Cluster | r5.large | 6 | $569.40 |
| Observability Stack | m5.xlarge | 3 | $442.38 |
| EBS gp3 Storage (18,000 GB) | — | — | $1,641.60 |
| Data Transfer Out (15,200 GB) | — | — | $1,368.00 |
| **TOTAL** | | | **$20,329.84/mo** |

### 24,000 TPS (2× Target)

| Component | Instance Type | Count | Monthly Cost |
|---|---|---|---|
| API Gateway | c5.xlarge | 8 | $992.80 |
| Load Balancer (AWS ALB, managed) | — | — | $620.00 |
| Transaction Orchestrator | m5.xlarge | 16 | $2,359.36 |
| Payment Processing Service | m5.2xlarge | 24 | $7,078.08 |
| Account Service | m5.xlarge | 12 | $1,769.52 |
| Fraud Detection Service | m5.2xlarge | 8 | $2,359.36 |
| Notification Service | m5.large | 6 | $442.38 |
| Reconciliation Service | m5.large | 4 | $294.92 |
| Audit & Compliance Service | m5.large | 6 | $442.38 |
| Kafka Cluster | r5.2xlarge | 5 | $1,898.00 |
| PostgreSQL + Citus (8 shards × 3) | r5.2xlarge | 24 | $9,110.40 |
| Redis Cluster | r5.large | 9 | $854.10 |
| Observability Stack | m5.xlarge | 4 | $589.84 |
| EBS gp3 Storage (24,000 GB) | — | — | $2,188.80 |
| Data Transfer Out (30,400 GB) | — | — | $2,736.00 |
| **TOTAL** | | | **$33,735.94/mo** |

**Note on non-linear scaling:** instance counts don't scale as a flat multiplier of TPS. The database and Kafka layers in particular follow the Day 6 capacity math — the jump from 6 shards (12K TPS) to 8 shards (24K TPS) is a 33% increase for a 100% traffic increase, because the 6-shard baseline was already provisioned with headroom (67% utilization at sustained 12K TPS, per Day 6). This is why per-TPS cost actually *drops* as scale increases ($5.78/TPS at 1,200 TPS → $1.69/TPS at 12,000 TPS → $1.41/TPS at 24,000 TPS) — fixed-overhead components (coordinator, observability stack, minimum HA footprint) get amortized across more traffic.

## 2. Budget Validation

| Scale | Monthly Cost | vs. $45,000 Ceiling |
|---|---|---|
| 1,200 TPS | $6,933.72 | 15% of budget |
| **12,000 TPS (target)** | **$20,329.84** | **45% of budget — $24,670.16 headroom** |
| 24,000 TPS (2× target) | $33,735.94 | 75% of budget — $11,264.06 headroom |

The target-scale architecture comes in at less than half the budget ceiling, and even the 2× stress scenario stays comfortably under it. This also means the new architecture, run at *today's* current 1,200 TPS load, would cost **$6,933.72/month — nearly half of the current system's actual $12,000/month spend** (Section A3.1). The redesign isn't just more scalable, it's more cost-efficient at every scale, largely because PgBouncer connection pooling and OCC (replacing pessimistic locking) let each node do meaningfully more useful work than the current single-primary, unpooled setup.

## 3. Cost Optimization Opportunities

Even with substantial headroom already, the following are identified for further savings, targeting the always-on, non-bursty infrastructure specifically:

| Optimization | Applies To | Estimated Discount | Rationale |
|---|---|---|---|
| **1-Year Reserved Instances (No Upfront)** | Kafka Cluster, PostgreSQL + Citus, Redis Cluster | ~35% | These run 24/7 by design — they're never scaled down, making them ideal RI candidates. At 12K TPS this alone saves roughly $2,989/month. |
| **Compute Savings Plans** | Transaction Orchestrator, Account Service, Observability Stack | ~28% | Steady baseline instances (not the auto-scaling Payment Processing tier); Savings Plans are preferred over instance-family-locked RIs here since these services' exact instance type may evolve. |
| **Spot Instances** | Notification Service only | Up to 50% (conservative estimate) | The only service in the architecture explicitly designed to tolerate abrupt interruption without correctness risk — failed notifications already retry via the DLQ mechanism (Day 9), so a Spot reclaim just looks like a transient failure the system already handles. **Not applied** to Reconciliation or Audit & Compliance consumers, since those have stricter lag tolerances (P1 alert policy) that make Spot's 2-minute reclaim notice a real operational risk. |
| **Right-sizing via load-test data** | All services | Ongoing | Once LT-002/LT-007 (Day 11) run against the actual deployed system, instance sizes here (based on capacity-planning estimates) should be validated against observed CPU/memory utilization and adjusted — this plan is a starting point, not a permanent allocation. |

Applying RI and Savings Plan discounts to the always-on infrastructure at 12,000 TPS brings the effective total from $20,329.84 down to approximately **$16,500/month** — comfortably qualifying for a sub-$35,000/month capacity plan without sacrificing any SLA, since none of these optimizations touch the auto-scaling, customer-facing tiers that need on-demand flexibility to burst to 18,000 TPS.

## Sources

AWS EC2 on-demand pricing for ap-south-1 and EBS gp3 pricing: aws-pricing.com (accessed September 2026, reflecting AWS's published rates as of August 2026). Figures are accurate within the ~20% tolerance this exercise calls for; exact production pricing should be re-verified in AWS Pricing Calculator before final budget sign-off, since rates and available instance types change over time.
