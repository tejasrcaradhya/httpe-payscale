# Reflection: HTTPE — PayScale

## Introduction

Working on HTTPE — PayScale was a valuable learning experience that helped me understand how different software engineering decisions influence the design of a distributed payment system. The project challenged me to think beyond individual features and consider scalability, consistency, reliability, and the practical trade-offs involved in building such systems.

One of my biggest takeaways was that designing a system is not simply about selecting technologies or completing requirements independently. Each decision affects other parts of the architecture, and understanding those connections is essential to creating a reliable solution.

## Key Challenges and Learning

### 1. Understanding the Impact of Architectural Decisions

One of the most challenging aspects of this project was understanding how an early architectural decision could affect later requirements.

For example, selecting Kafka for event streaming was only the beginning. Determining the appropriate number of partitions required considering the system's expected throughput and processing requirements. During the project, I calculated a requirement of 64 partitions based on the throughput analysis.

However, I later discovered that an earlier illustrative value of 32 partitions was still present in two documents. This highlighted the importance of reviewing and updating documentation whenever an architectural decision changes.

This experience taught me that integration and documentation are not just final-stage activities. They are important throughout the entire development and design process.

### 2. Database Sharding and Traffic Distribution

The sharding decision was another important learning experience.

Initially, using `hash(account_id)` appeared to be a straightforward approach because it provides relatively uniform distribution of accounts across shards. The distribution simulation supported this assumption.

However, analyzing the scenario of a single merchant processing 40% of all transactions revealed an important limitation: uniform account distribution does not necessarily mean uniform traffic distribution.

This helped me understand the difference between distributing data evenly and distributing workload evenly.

The solution was not necessarily to change the sharding strategy. Since splitting a single account's balance across multiple shards could complicate the Optimistic Concurrency Control model, I explored how batch settlement could help address the issue.

Batch settlement, which was also relevant to FR-006, could reduce the need for synchronous updates to a merchant's balance for every incoming payment.

This was one of the most valuable connections I made during the project because it showed me how different system requirements can influence and support each other.

### 3. Understanding Optimistic Concurrency Control

The concurrency control section helped me move from understanding a concept theoretically to analyzing how it works in a practical scenario.

Before this project, I understood Optimistic Concurrency Control as a mechanism for handling concurrent updates. However, working through a scenario involving two transactions attempting to debit the same account helped me understand the importance of version checks in maintaining balance consistency.

By tracing the transactions step by step, I was able to understand how one transaction could successfully update the account while the other would fail when its version no longer matched the expected value.

This exercise helped me appreciate the difference between simply describing a design pattern and understanding why it works under specific conditions.

It also made me more aware of the importance of testing concurrency assumptions rather than relying only on a high-level explanation.

### 4. Failure Mode and Effects Analysis

The Failure Mode and Effects Analysis (FMEA) was one of the most challenging parts of the project.

My initial approach focused mainly on common infrastructure failures such as database node failures and network partitions. However, expanding the analysis to include data and operational failures revealed several risks that I had not initially considered.

For example, an incorrect database migration could affect financial balances, while a broken alerting rule could prevent an operational issue from being detected.

These examples helped me understand that system reliability is not limited to infrastructure availability. Data integrity, monitoring, operational processes, and recovery mechanisms are equally important, especially in a payment system.

The FMEA exercise encouraged me to think more systematically about potential failures and their consequences.

## What I Would Improve

If I were to continue working on this project, I would focus on validating the design through practical implementation and testing.

The current work helped me develop a stronger understanding of architecture, concurrency, scalability, and failure analysis. However, there is a difference between proving that a design should work and measuring how it performs in a real environment.

I would like to implement and run at least one of the proposed load tests against a deployed sandbox. This would help me compare the theoretical throughput assumptions with actual measurements.

I would also improve the documentation workflow by maintaining a single source of truth for important architectural values, calculations, and decisions. This would reduce inconsistencies and make future revisions easier.

## Overall Reflection

HTTPE — PayScale helped me develop a more structured approach to software engineering and system design.

The project taught me to:

- Consider the relationship between different architectural decisions.
- Evaluate scalability beyond simple data distribution.
- Understand the practical importance of concurrency control.
- Analyze failures across infrastructure, data, and operations.
- Review and maintain consistency across technical documentation.
- Distinguish between theoretical correctness and measured performance.

As a Computer Science Engineering student interested in software development, I found this project useful because it encouraged me to think about how reliable systems are designed rather than focusing only on individual features.

The experience has motivated me to strengthen my programming and backend development skills and apply these concepts in future projects.

Overall, HTTPE — PayScale was an important step in improving my understanding of distributed systems and the engineering trade-offs involved in designing scalable and reliable payment processing systems.
