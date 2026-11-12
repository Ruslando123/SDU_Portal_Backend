# Performance capacity report

## Verified envelope

| Workload | Test scope | Achieved HTTP RPS | Gate |
| --- | --- | ---: | --- |
| Steady read, 50 VUs | Original three repetitions | 40.00 | Independently Tested |
| Mixed, 50 VUs | Original two clean repetitions and two new correlated repetitions | 60.00 | Independently Tested |
| Steady read, 100 VUs | Original controlled cloud run | 80.00 | Independently Tested |
| Mixed, 100 VUs | Original controlled cloud run | 120.00 | Independently Tested |
| Steady read, 200 VUs | Original and instrumented Connect runs, pool 10 and 16 | 160.00 in measurement | One new pass, failed repetitions |
| Steady read, 200 VUs | Encrypted TLS tunnel, pool 16 | 160.00 | Independently Tested, one pass |
| Mixed, 200 VUs | Encrypted TLS tunnel, pool 16 | 240.00 | Independently Tested, one pass |
| Mixed, 200 VUs | Sandbox Connect | No Data | Not Tested |
| Steady read, 500 VUs | Encrypted TLS tunnel, pool 24 | 399.03 | One pass |
| Mixed, 500 VUs | Encrypted TLS tunnel, pool 24 | 518.64 in a failed measurement | Failed HTTP and SQL reconciliation |
| Mixed, 500 VUs planned | Encrypted TLS tunnel, pool 32 | No Data | Ramp aborted at 154 observed VUs |
| Mixed, 500 VUs | Encrypted TLS tunnel, prefilled pool 32 | 410.28 in a failed measurement | Failed HTTP and SQL reconciliation |
| 1,000, 2,500 and 5,000 VUs | Closed-model cloud capacity | No Data | Not Tested |

The previously qualified Sandbox Connect level is 100 configured and observed VUs for both closed-model profiles. The mixed result had p50 104.09 ms, p95 355.38 ms and p99 495.63 ms. It completed 14,400 HTTP attempts without unexpected responses in 120 seconds. This is one passed level on a specific fixture. One new 200-VU read repetition also passed at 160.00 RPS. Its p50/p95/p99 were 184.53/466.06/580.24 ms, with zero warm-up and measurement errors. The 200-VU configuration is not qualified as a stable level because other read repetitions failed and the mixed scenario on Sandbox Connect remains untested. [Original result table](autonomous-verification.md).

The independent local HTTP hotspot used 2,000 distinct students, 20 VUs and 30 seats. Both warmed configurations produced 30 `ENROLLED` and 1,970 `SECTION_FULL` with zero SQL invariant violations. The late-lock run recorded 113.78 RPS, HTTP p95 90.97 ms and p99 227.68 ms. The local registration spike completed 270 journeys and 810 requests with zero unexpected responses and zero dropped iterations. [Hotspot allocation](../infrastructure/k6/results/autonomous-hot2000-late-warmed-v20/allocation.json) · [Spike summary](../infrastructure/k6/results/autonomous-spike15/summary.json).

The open-model read profile passed two independent 120-second runs at 160 offered reads per second, with 159.98 and 160.00 achieved RPS and zero unexpected outcomes or drops. At 240 offered reads per second, one run passed and one failed. The 200, 300 and 400 offered-rate experiments failed qualification. [Open-model evidence](cloud-scaling-verification.md#open-model-capacity).

### Matched 200-VU Connect diagnostics

| Hikari maximum | Requests in measurement | Achieved RPS | p95 / p99 ms | Warm-up / measurement errors | Peak pending / mean acquisition | Gate |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| [10](../infrastructure/.local/modal/runs/modal-20261107T143752Z-836c67/report.json) | 19,200 | 160.00 | 466.06 / 580.24 | 0 / 0 | 65 / 20.59 ms | Passed |
| [16](../infrastructure/.local/modal/runs/modal-20261107T144203Z-766210/report.json) | 19,200 | 160.00 | 427.02 / 548.14 | 15 / 1 | 20 / 11.31 ms | Failed |

Both reports verified all SQL invariants and cleanup. Both used the same historical backend JAR, fixture, region, resource limits, pacing and instrumentation. Java process RSS peaked near 495 MiB and 493 MiB, respectively. The generator k6 process peaked at 203 MiB and 192 MiB RSS. Process metrics do not describe the whole Sandbox or worker host. One run per configuration cannot establish the causal performance effect of a larger pool.

### Independent 200-VU encrypted-tunnel runs

| Profile | HTTP attempts | Achieved RPS | p50 / p95 / p99 ms | Warm-up / measurement errors | SQL and cleanup |
| --- | ---: | ---: | ---: | ---: | --- |
| [Steady read](../infrastructure/.local/modal/runs/modal-20261107T144711Z-bb3a74/report.json) | 19,200 | 160.00 | 92.41 / 246.80 / 313.90 | 0 / 0 | Verified |
| [Mixed](../infrastructure/.local/modal/runs/modal-20261107T145122Z-01aece/report.json) | 28,800 | 240.00 | 106.97 / 417.87 / 497.80 | 0 / 0 | Verified |

Both runs used separate fresh databases, 200 observed VUs, pool 16, the original benchmark JAR, the same resource limits and a separate 30-second warm-up. The mixed measurement completed 4,800 enrollments and 4,800 withdrawals. It sampled a peak of 65 Hikari waiters and five PostgreSQL lock waiters. Its mean pool acquisition was 18.04 ms and k6 RSS peaked at 195.82 MiB. The closed executor did not emit a dropped-iterations counter. This remains the highest VU tier with a pass for both closed profiles. A successful alternate-path run leaves the standard route's HTTP 502 investigation open.

## Method and follow-on gates

The cloud profiles use fresh PostgreSQL per run, separate authentication, a 30-second warm-up and 120-second measurement. The baseline fixture has 205 accounts. The scaled fixture has one account per configured VU. The closed runner paces each iteration to a minimum of 1.25 seconds. A VU count describes concurrent synthetic users, measured iterations per second describe completed or started journeys, and HTTP RPS counts request attempts. Dropped iterations are unavailable in k6 summaries when the executor did not emit that counter. An unexpected warm-up or measurement outcome, missing SQL verification or unverified cleanup invalidates the overall gate.

Cloud escalation remains gated on passing both closed workloads at 500 VUs with the same configuration. The mixed reports show application queue pressure and sampled PostgreSQL lock waits. The isolated pool increase did not resolve the mixed errors. For production sizing, reproduce the mixed errors with blocking-statement and pool wait evidence, verify end-to-end mutation outcomes through response loss, and repeat the qualified SLO under representative data and infrastructure. The Sandbox combines Spring and PostgreSQL, with a separate generator budget. The open-model plateaus and run receipts are in the [scaling verification](cloud-scaling-verification.md).

The follow-up diagnostics identified section-row lock queues and reconciled 42,935 durable outcomes across two independent full 500-VU mixed runs. Both failed. The separate-database baseline also failed qualification at 200 VUs. [Mixed database evidence](modal-mixed-database-diagnostics.md).
