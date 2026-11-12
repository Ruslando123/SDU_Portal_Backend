# Modal scaling verification

## Boundary

These measurements use a fixed, isolated Spring Boot and PostgreSQL Sandbox and a separate k6 Function. They use the encrypted TLS tunnel, a fresh per-run gateway key, synthetic student sessions, the route allowlist and SQL checks. The benchmark port is the only forwarded port. Spring and management listeners stay on loopback. The ordinary Sandbox Connect route retains its known intermittent pre-ingress HTTP 502. The exact provider component is unresolved. [Incident evidence](bottleneck-analysis-final.md).

The tests preserve the previously measured backend artifact and use pool 16 as the initial control. For 500 VUs the Sandbox has 1.5 physical cores and 2 GiB. The k6 Function has one physical core and 1 GiB. Both run in `us-east`. Every case records exact limits, fixture size, transport, pacing, code hashes and separate warm-up and measurement results. The scaled fixture has one synthetic account per configured VU and preserves 20 independent mixed-section seat budgets. Two successful closed profiles with matching settings at one tier are required before the next tier can start. The web controls remain scoped to the preexisting smaller sizes.

The closed workload runs a 30-second warm-up and 120-second measurement with a minimum iteration time of 1.25 seconds. Gates require zero unexpected responses, interruptions and reported drops, observed peak VUs at the configured level, at least 95% of the paced journey target, p95 at most 1 second and p99 at most 2 seconds in both windows, generator headroom, SQL invariants and verified cleanup. A missing dropped-iterations counter in the closed executor is shown as No Data. The open workload schedules one HTTP read per iteration with the constant-arrival-rate executor. Its gate requires the exact number of scheduled starts within measurement, every observed start to complete, zero unexpected outcomes and drops, at least 99% of scheduled HTTP completions inside measurement, generator headroom and the same latency thresholds for measurement. [k6 open and closed models](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/open-vs-closed/) · [Dropped iterations](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/dropped-iterations/).

## Initial 500-VU failures

The first 500-VU attempt prepared 499 authenticated sessions and stopped before load. The individual failure cause was not retained. The worker now classifies each preparation step and permits one fresh synthetic session attempt for transport failures or HTTP 502, 503 and 504. Workload errors retain their original gate semantics. [First attempt](../infrastructure/.local/modal/runs/modal-20261107T150503Z-830be3/report.json).

The simultaneous-start repeat prepared all 500 sessions. It then aborted in warm-up with 222 timeouts on 610 observed attempts. At the first resource snapshot, Hikari reported 16 active and 64 pending connections. PostgreSQL showed 16 connections and zero sampled lock waits. Spring logged transaction acquisition failures. The captured timed-out requests matched authenticated gateway ingress and cancellation. k6 used 0.49 logical cores on average and peaked at 417.53 MiB RSS. The observed pressure was concentrated in the synchronized startup wave and the application connection queue. [Simultaneous-start repeat](../infrastructure/.local/modal/runs/modal-20261107T150854Z-bdee02/report.json).

The subsequent spread-start experiment holds resources and pacing fixed and distributes the first request of each VU uniformly across the existing 1.25-second iteration interval.

The spread-start, pool-16 run completed measurement at 398.86 RPS with zero errors, but warm-up still recorded 39 timeouts and six HTTP 503 responses. It failed qualification. The 20-second ramp and pool-16 run recorded 257 full-level warm-up errors and 452 measurement errors. The measured p95/p99 were 1,814.64/3,187.41 ms. Hikari peaked at 16 active and 65 pending, with 40.17 ms mean acquisition. PostgreSQL showed no sampled Lock waits. The Java process peaked at 0.88 logical cores and 485.88 MiB RSS. The measurement k6 process averaged 0.33 logical cores and its run peak RSS was 372.27 MiB. SQL and cleanup passed. [Spread-start run](../infrastructure/.local/modal/runs/modal-20261107T151052Z-fadf91/report.json) · [Ramp run](../infrastructure/.local/modal/runs/modal-20261107T151624Z-3d627c/report.json).

The same 500-VU read with a 24-connection pool passed all phases at 399.03 RPS, with measurement p95/p99 of 345.03/477.99 ms and zero unexpected outcomes. Mean acquisition was 11.24 ms and sampled pending peaked at 56. The configured PostgreSQL maximum is 60 connections. The pool adjustment applies only to the isolated scaled benchmark. This is one observed A/B outcome on a shared Sandbox. [Passing read report](../infrastructure/.local/modal/runs/modal-20261107T152217Z-7bdc2b/report.json).

## Mixed workload

The 500-VU mixed run with pool 24 measured 518.64 RPS, p95 1,059.96 ms and p99 2,924.86 ms. It recorded 445 unexpected outcomes in measurement, including 294 HTTP 503, 120 timeouts and 31 other HTTP errors. There were 248 unexpected warm-up outcomes including the ramp. Hikari reached 24 active and 56 pending connections, with 23.66 ms mean acquisition. PostgreSQL sampled up to four lock waiters, including tuple and transaction-ID waits. Mean transaction duration was 78.75 ms. k6 averaged 0.43 logical cores and peaked at 386.33 MiB RSS. These observations identify pressure in the application queue and database write path. The blocking statement remains unidentified. [Mixed pool-24 report](../infrastructure/.local/modal/runs/modal-20261107T152703Z-5f1aa8/report.json).

The mixed SQL result showed zero active enrollments at drain and zero violations across the eight structural checks, including capacity, duplicates and missing audit events. The database retained 13,431 committed withdrawals and k6 confirmed 13,428 across both phases. Each committed withdrawal had an audit event. The exact client-to-database count gate failed. A response can be lost after commit even when a retry reuses the same Idempotency-Key. [SQL evidence](../infrastructure/.local/modal/runs/modal-20261107T152703Z-5f1aa8/invariants.json).

The same mixed configuration with pool 32 stopped 5.65 seconds into the ramp at 154 observed VUs after 25 unexpected outcomes on 262 attempts. No measurement window started. The single resource snapshot showed 21 active Hikari connections, 59 pending and one PostgreSQL transaction-ID lock wait. SQL retained 21 active registrations and a mismatch between acknowledged and committed operations. The eight structural checks were zero and cleanup was verified. The closed 1,000, 2,500 and 5,000-VU levels remain untested. [Ramp-aborted report](../infrastructure/.local/modal/runs/modal-20261107T153628Z-88d555/report.json) · [SQL evidence](../infrastructure/.local/modal/runs/modal-20261107T153628Z-88d555/invariants.json).

The ramp-aborted run had four PostgreSQL connections immediately before warm-up, and 24 in its first ramp sample. The pool-32 prefill repeat set minimum idle to 32 and checked all 32 idle application connections before readiness. It reached 500 VUs and completed measurement. It recorded 410.28 RPS, p95/p99 of 1,837.56/3,140.56 ms, 232 unexpected warm-up outcomes and 399 measurement outcomes. Hikari peaked at 32 active and 49 pending, with 26.41 ms mean acquisition. PostgreSQL sampled up to six lock waiters. Mean section lock query and transaction time were 17.03 and 126.81 ms. k6 averaged 0.34 logical cores and peaked at 396.57 MiB RSS. The SQL check retained one active registration. The structural violations remained zero, the exact gate failed, and cleanup was verified. This repeat passed through the ramp and still exceeded latency thresholds in the full window. [Pool-32 prefill report](../infrastructure/.local/modal/runs/modal-20261107T154418Z-4909c2/report.json) · [SQL evidence](../infrastructure/.local/modal/runs/modal-20261107T154418Z-4909c2/invariants.json).

## Open-model capacity

Each measured plateau schedules one HTTP read per iteration, after warming 50 authenticated users for 30 seconds. The 500 preallocated k6 VUs define the generator's concurrency allowance. The achieved RPS includes all completed HTTP attempts, with unexpected outcomes listed separately.

| Offered per second | Pool max / min idle | Achieved RPS | p95 / p99 ms | Unexpected outcomes | Dropped iterations | k6 mean logical cores / peak MiB | Gate |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| [160](../infrastructure/.local/modal/runs/modal-20261107T161645Z-314ec5/report.json) | 24 / 2 | 159.98 | 19.68 / 183.16 | 0 | 0 | 0.27 / 345.62 | Passed |
| [160 repeat](../infrastructure/.local/modal/runs/modal-20261107T162032Z-11b396/report.json) | 24 / 2 | 160.00 | 31.77 / 485.87 | 0 | 0 | 0.31 / 346.49 | Passed |
| [200](../infrastructure/.local/modal/runs/modal-20261107T161202Z-eba6d4/report.json) | 24 / 2 | 199.98 | 172.73 / 745.35 | 20 | No Data | 0.35 / 349.12 | Failed |
| [240](../infrastructure/.local/modal/runs/modal-20261107T154921Z-0c3c21/report.json) | 24 / 2 | 239.98 | 19.85 / 466.05 | 0 | 0 | 0.33 / 358.12 | Passed |
| [240 repeat](../infrastructure/.local/modal/runs/modal-20261107T160757Z-e61970/report.json) | 24 / 2 | 239.98 | 1,262.85 / 2,032.95 | 45 | 0 | 0.28 / 350.61 | Failed |
| [300](../infrastructure/.local/modal/runs/modal-20261107T153926Z-78f77a/report.json) | 24 / 2 | 299.12 | 1,340.22 / 2,138.22 | 91 | 87 | 0.43 / 350.67 | Failed |
| [300](../infrastructure/.local/modal/runs/modal-20261107T155337Z-1ed09e/report.json) | 24 / 24 | 299.70 | 1,322.32 / 2,132.36 | 82 | 33 | 0.35 / 355.74 | Failed |
| [400](../infrastructure/.local/modal/runs/modal-20261107T153213Z-daf8bb/report.json) | 24 / 2 | 376.28 | 1,810.48 / 2,725.09 | 192 | 2,807 | 0.41 / 359.85 | Failed |

At 400 offered reads per second, 45,193 of 48,000 scheduled iterations started and completed. The rest were dropped after in-flight requests occupied all 500 available VUs. Hikari reached 24 active and 56 pending connections, and PostgreSQL sampled no Lock waits. The k6 process stayed below its CPU and RSS limits. These drops reflect exhausted in-flight slots under the observed response times. SQL and cleanup passed.

At 300 offered reads per second, all 91 measurement errors occurred in the first 20 seconds. Every later five-second bucket recorded zero unexpected outcomes. This interval is diagnostic evidence for a startup transient. Qualification uses the full scheduled window. Hikari reached 24 active and 56 pending connections, and sampled PostgreSQL Lock waits remained zero. SQL and cleanup passed.

The prefilled 300-read repeat still failed. All 82 errors occurred in the first 25 seconds. Hikari again reached 24 active and 57 pending, with 7.51 ms mean acquisition and no sampled PostgreSQL Lock waits. Pool prefill alone did not resolve the initial overload. SQL and cleanup passed.

In the passing 240-read plateau, all 28,800 scheduled iterations started and completed, with 28,797 HTTP completions inside the window. The generator peaked at 158 active VUs. Hikari peaked at 23 active and 17 pending, with 0.028 ms mean acquisition and zero sampled PostgreSQL Lock waits. An independent repeat completed the same scheduled work and still failed. All 45 errors occurred in the first 15 seconds, with Hikari peaking at 24 active and 56 pending. SQL and cleanup passed in both cases. One repeat command timed out on the read-only Modal inventory preflight, before allocating an App. The independent repeat above ran after checking the inventory again.

At 200 offered reads per second, the 20 HTTP errors occurred in the first 10 seconds. k6 started the scheduled 24,000 iterations within measurement and one additional iteration at the boundary. All started iterations completed. Subsequent runs check the exact in-window start count separately from whole-run completion. The 200-read result remains failed because of its HTTP errors.

The sampled failures in the full mixed runs and baseline 300/400 open runs reached the authenticated local gateway. The 334 saved traces in those four cases all include ingress. This evidence is separate from the pre-ingress HTTP 502 recorded on standard Sandbox Connect.

The two independent 160-read runs each started all 19,200 scheduled iterations in the 120-second window. The first recorded 19,198 HTTP completions in the window and the second recorded 19,200. Peak active k6 VUs were 108 and 95. SQL and cleanup passed in both. This is the highest reproducible qualified open rate observed on this route and configuration. The 240-read success is a single observation, with a failed repeat. Production throughput and the exact saturation point remain unmeasured.

## Billing and cleanup

Modal attributed **USD 0.57132983 before credits** to the 16 executed Apps, using the exact App ID and run tag for each receipt. The conservative estimates for those executed cases plus their suite reserves sum to USD 2.28530. A single-case plan was capped at USD 0.20, and a two-case open repeat plan at USD 0.28. Each start refreshed the workspace rates and enforced the USD 25 reserve. At the final check the workspace had zero active Apps, zero active containers and USD 0 billed after adjustments. All 16 reports independently verified Sandbox and Function cleanup.

## Local verification

The JVM suite passed 93 tests, and the separate 2,000-student allocation stress test produced 30 successful enrollments and 1,970 `SECTION_FULL` outcomes with zero violations. The Go gateway tests and 10 Python tests passed. Offline k6 inspection covered both phases for read and mixed at each of the four scaled tiers and both phases for open read. Performance Lab passed seven unit checks, six browser checks and the production build. The predecessor guard rejected 1,000, 2,500 and 5,000 VUs locally before any provisioning.

## Reproduce

Run one case at a time using the pinned Modal SDK and verified assets. The ladder checks previous passed reports on the same backend artifact and transport. Above 500 VUs it also requires matching pool maximum, minimum idle and start pacing. The isolated pool-24 and pool-32 experiments at 500 VUs start from the previously approved 200-VU pool-16 evidence. [Cloud runbook](modal-controlled-benchmark.md).

```sh
python infrastructure/modal/benchmark.py plan --profile steady-read --vus 500 --transport encrypted-tunnel --pool-size 24 --spread-start --ramp-seconds 20 --max-usd .20
python infrastructure/modal/benchmark.py run --profile steady-read --vus 500 --transport encrypted-tunnel --pool-size 24 --spread-start --ramp-seconds 20 --max-usd .20 --confirm
python infrastructure/modal/benchmark.py plan --profile mixed --vus 500 --transport encrypted-tunnel --pool-size 32 --prefill-pool --spread-start --ramp-seconds 20 --max-usd .20
python infrastructure/modal/benchmark.py plan --profile open-read --vus 500 --arrival-rate 240 --transport encrypted-tunnel --pool-size 24 --spread-start --max-usd .20
python infrastructure/modal/benchmark.py plan --profile open-read --vus 500 --arrival-rate 160 --transport encrypted-tunnel --pool-size 24 --spread-start --repeat 2 --max-usd .28
```
