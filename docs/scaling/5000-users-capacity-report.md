# Phase 9 — Capacity ladder and 5,000-user qualification

The target is 5,000 concurrently active authenticated students under separately defined workloads. VUs measure concurrency. RPS measures HTTP throughput. Unique acknowledged enrollments and withdrawals per second measure business throughput. Record all three, including observed VUs and the completed measurement window.

## Prepared workload definitions

| Scenario | Workload | Qualification gate | Current 5,000-VU status |
| --- | --- | --- | --- |
| A | `catalog-read`, four published-catalog reads for each sections read | All JSON checks, zero technical failures or timeouts, p95 below 1 s, p99 below 2 s, adequate generator headroom | Not Tested |
| B | `browse-read`, catalog, instructor details and own schedule with course titles | Same gate as A, separate endpoint and journey throughput | Not Tested |
| C | Authenticated mixed enrollment/withdrawal and reads | Zero unexpected warm-up and measured outcomes, p95 at most 1 s and p99 at most 2 s, no interrupted or dropped iterations, at least 95% of planned paced iterations, exact SQL/durable reconciliation and cleanup | Not Tested |
| D | `hot`, 2,000 unique students compete for 30 seats, with generator concurrency configured independently | Exactly 30 committed enrollments and 1,970 valid full denials, all invariants and audit checks, no technical errors, separate p95 for success and denial | Not a 5,000-VU claim |
| E | Open-model single-read iteration, with a fixed offered arrival rate and an independently budgeted generator | All arrivals accounted for, zero dropped iterations and technical errors, at least 99% of offered HTTP RPS, p95 at most 1 s and p99 at most 2 s | Not Tested at a 5,000-VU ceiling |

The k6 definitions were inspected without starting load. The local harness retains its host resource guard. The isolated JVM stress independently verified exactly 30 allocated seats among 2,000 synthetic students with 32 workers and zero invariant violations. The previously observed open-model 160 reads/s plateau and the 500-VU read-only pass remain separate from mixed capacity evidence. [Local workload definitions](../../infrastructure/k6/README.md) · [Prior open-model evidence](../cloud-scaling-verification.md#open-model-capacity).

Start at 200 VUs and qualify each exact configuration at 500, 1,000, 2,500 and 5,000 in that order. Pause on a failed warm-up or measurement. An aborted ramp provides only partial evidence. The current detailed Modal ledger is bounded. Before qualifying mixed tiers above its supported ceiling, provide a scalable export and demonstrate that HTTP outcomes and durable operations are fully accounted for without oversizing the generator or the report transfer. The safe default for the demonstration remains the verified direct-primary release configuration.

## Current evidence

- Mixed 200 VUs remains verified.
- Mixed 500 VUs remains failed. In this phase the transaction-pool configuration reached 500 during warm-up and triggered the safety stop. No steady-state throughput was recorded for that run. See the [PgBouncer results](pgbouncer-results.md).
- 1,000, 2,500 and 5,000 VUs remain Not Tested. A Kubernetes deployment is also Not Tested.
- Isolated Modal profiles A and B reached 200 VUs and failed the HTTP gate. Profile C stopped at 93 observed VUs during ramp. The separate-resource ladder stopped at that point. [Resource scaling and database RTT analysis](resource-scaling-modal.md).

At production scale, repeat the same SLOs with independent app and database resource budgets, enforce the aggregate JDBC budget, and validate query ownership, cleanup, rollback and timeout handling with durable outcomes. Use a separately sized generator and explicit arrival-rate tests to qualify steady throughput.
