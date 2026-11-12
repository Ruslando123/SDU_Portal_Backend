# Enrollment transaction optimization and HTTP reconciliation

## Why six registrations stayed active

The two archived 500-VU runs each ended with three active episodes. The HTTP ledger, persisted operation IDs, audit correlations, PostgreSQL transaction IDs and commit timestamps are joined in `infrastructure/.local/modal/enrollment-six-active-forensics.json`. Its full synthetic request and idempotency IDs are operator evidence. HTTP start is not PostgreSQL transaction start. The archives contain no per-attempt transaction begin marker or rollback log, so those timestamps and the terminal state of uncommitted attempts remain unobserved.

| Archived run suffix | Active episode prefix | Enrollment response and commit | Withdrawal intent and durable result |
| --- | --- | --- | --- |
| `91dbb0` | `c2659307` | HTTP 201, committed by attempt 1 | Two timeouts on one key, no durable withdrawal |
| `91dbb0` | `86b0c6fe` | Two timeouts, attempt 2 committed about 440 ms after its timeout | No DELETE sent |
| `91dbb0` | `007fa1a7` | Two timeouts, attempt 1 committed about 2,454 ms before its timeout | No DELETE sent |
| `d83908` | `4ebf4ae4` | Two timeouts, attempt 2 committed about 208 ms after its timeout | No DELETE sent |
| `d83908` | `41f84610` | Two timeouts, attempt 1 committed about 216 ms after its timeout | No DELETE sent |
| `d83908` | `93d0e0b4` | HTTP 201, committed by attempt 1 | Two timeouts on one key, no durable withdrawal |

The four committed enrollments without a successful HTTP acknowledgement expose a harness defect. The original script only started DELETE after receiving and validating the POST response. The other two records had genuine incomplete withdrawal intents. The archived evidence establishes the absence of a durable withdrawal, without establishing whether each attempt rolled back or failed before a transaction began. All eight structural SQL checks passed in both runs. The recorded relative timestamps use separate host clocks and are approximate. [First archived ledger](../infrastructure/.local/modal/runs/modal-20261107T175239Z-91dbb0/operation-reconciliation.json) · [Repeat archived ledger](../infrastructure/.local/modal/runs/modal-20261107T175817Z-d83908/operation-reconciliation.json).

All four withdrawal attempts reached the authenticated gateway. The first DELETE for `c2659307` received upstream HTTP 503 at the gateway, while the client recorded a timeout. Its retry and both attempts for `93d0e0b4` ended with gateway cancellation. The gateway's internal cancellation status does not establish rollback or an HTTP response received by k6.

## Changes and safeguards

The instrumented control retains the original SQL order. The reduced-scope candidate reads the pinned policy window while holding the term gate and student-term lock, before acquiring the section row lock. Withdrawal rechecks ownership and state under the student-term lock before taking the section lock. The original section lock query and subsequent `clock_timestamp()` call still determine the time for natural window closing. Seat accounting, episode writes, audit, terminal idempotency state and synchronous commit retain their original transaction and order. [PostgreSQL clocks](https://www.postgresql.org/docs/18/functions-datetime.html).

The diagnostic workload stops submitting new mutations for a VU with an incomplete journey. The worker journals bounded recovery requests separately between warm-up and measurement and after measurement. For an ambiguous POST it reuses the original key and target. After confirming an enrollment it sends the intended DELETE. For an ambiguous DELETE it reuses the original withdrawal key. The recovery path uses the synthetic actor's existing HTTP session, CSRF and gateway credentials. No database rows are edited or removed by the worker. Timed HTTP errors and the original SLO gate remain in force, and a run that needed recovery cannot qualify.

The retained stage timers include idempotency claim, student-term lock, academic checks, section acquisition, seat write, episode write, audit, terminal outcome write and COMMIT. Additional timers cover decision clock, window lookup, withdrawal recheck, section hold and total transaction time. The sampler reports call counts and wall-time deltas in the measured interval. JDBC timers include driver and scheduling time. PostgreSQL lock and WAL observations are separate evidence and do not attribute a physical flush to an individual application timer. Counts differ by stage because replays and denials do not execute the full successful path.

In the archived runs, section hold rose by 19.94–23.65 ms, while measured COMMIT rose by 3.11–5.45 ms. PostgreSQL recorded section-row waiters whose blockers were still writing audit rows, updating the seat counter or completing idempotency. In cumulative PostgreSQL statement statistics, section acquisition rose from 0.019 ms per call at 200 VUs to 2.199–2.914 ms at 500. Seat writes rose from 0.051 to 0.080–0.082 ms, enrollment insert from 0.112 to 0.310–0.714 ms, withdrawal update from 0.117 to 0.315–0.792 ms, and idempotency completion from 0.257 to 0.573–1.049 ms. Acquisition occurs before hold, while the slower write and commit work occurs during hold. The cumulative server means and measurement-window application means describe different windows and cannot be summed to assign the entire hold increase. [Archived database analysis](modal-mixed-database-diagnostics.md).

## Controls on the shared Sandbox

The original SQL with instrumented timers first measured 239.82 RPS, p95/p99 14.26/24.56 ms, and zero errors at 200 VUs. SQL aggregate checks passed. The operator export timed out, so the run could not pass per-request reconciliation. The export now creates one protected compressed snapshot and reads it in verified bounded pages. [Initial control](../infrastructure/.local/modal/runs/modal-20261107T181718Z-2588ca/report.json).

The matched control used 500 seeded synthetic accounts, twenty 32-seat sections, pool maximum and minimum idle 16, 1.5 physical Sandbox cores and 2 GiB, the authenticated TLS route in `us-east` and `commit_delay=0`. The ramp started at one VU, took 60 seconds, then held the target for 30 seconds before 120 seconds of measurement. At 200 VUs it measured 239.81 RPS, p95/p99 26.97/43.94 ms, and zero measurement errors. Three warm-up read failures prevented qualification. All 14,230 durable operations reconciled, the final database had zero active enrollments, and all SQL invariants passed. [Matched control](../infrastructure/.local/modal/runs/modal-20261107T183248Z-33af8a/report.json).

The first candidate also folded the post-lock clock into the section query with a materialized CTE. It failed twice at 200 VUs and the CTE was withdrawn. Section acquisition in PostgreSQL averaged 2.042 and 2.385 ms per call against 0.261 ms in the matched control. Lock samples continued to identify competing section writers. Driver and application time rose in nearly every stage. For example, PostgreSQL's cumulative idempotency claim average remained in the 0.165–0.210 ms range, while its measured application wall time rose from 0.451 to 5.337–6.111 ms. HikariCP queued up to 65 callers with all 16 connections active. Both runs recorded WAL and row-lock waits. The evidence demonstrates the queue under contention, while it leaves the causal contribution of the CTE and the shared host's scheduling unresolved. [First CTE run](../infrastructure/.local/modal/runs/modal-20261107T183826Z-162be6/report.json) · [Second CTE run](../infrastructure/.local/modal/runs/modal-20261107T184858Z-3f9ff4/report.json).

| Measurement-window application timer | Control mean (ms) | First CTE mean (ms) | Second CTE mean (ms) | Reduced scope mean (ms) |
| --- | ---: | ---: | ---: | ---: |
| Idempotency claim | 0.451 | 5.337 | 6.111 | 0.412 |
| Student-term lock acquisition | 0.219 | 3.929 | 4.849 | 0.161 |
| Academic validation | 1.434 | 28.614 | 37.890 | 1.255 |
| Section lock acquisition | 0.226 | 6.191 | 7.080 | 0.164 |
| Seat update | 0.248 | 4.190 | 5.175 | 0.195 |
| Enrollment or withdrawal write | 0.371 | 5.001 | 5.875 | 0.292 |
| Audit write | 0.330 | 4.708 | 5.415 | 0.244 |
| Terminal idempotency write | 0.612 | 6.117 | 6.774 | 0.515 |
| COMMIT callback interval | 0.303 | 4.943 | 5.805 | 0.239 |
| Section hold | 2.297 | 24.993 | 29.083 | 1.612 |
| Total transaction | 4.820 | 130.848 | 139.652 | 4.104 |

The first CTE run measured 220.10 RPS, p95/p99 1,282.77/2,844.37 ms and 173 HTTP errors. The repeat measured 205.31 RPS, p95/p99 1,837.65/3,172.52 ms and 434 HTTP errors. The first reconciled 13,234 operations, while the repeat reconciled 11,946. All eight structural SQL invariants passed and zero active episodes remained in each database. The second run recovered five journeys after warm-up and five after measurement, with two original-key replays returning the stored result. Recovery writes are excluded from the timed workload counters. The repeat therefore failed the original HTTP-versus-SQL workload gate while passing durable final-state verification. Separate host clocks leave three near-boundary commit timings uncertain.

The first launch of the reduced-scope candidate encountered a DNS resolution error on the controller during Sandbox creation. Load never started. Direct App lifecycle inspection and container inventory later confirmed cleanup. [Provisioning attempt](../infrastructure/.local/modal/runs/modal-20261107T185751Z-6e51ce/report.json).

The reduced-scope control passed at 200 VUs. It measured 239.80 RPS, p95/p99 43.47/99.89 ms, with zero warm-up or measurement errors and zero interrupted iterations. HikariCP had zero sampled waiters and PostgreSQL had zero sampled heavyweight lock waiters. All 14,386 intents matched acknowledged durable outcomes. The final database contained 7,193 withdrawn episodes and zero active episodes. The sampled mean section hold was 1.61 ms compared with 2.30 ms in the matched instrumented control. HTTP tail latency was higher, so this pair establishes the control gate and the observed hold time, with no demonstrated HTTP latency improvement. [Qualified reduced-scope control](../infrastructure/.local/modal/runs/modal-20261108T071017Z-c47593/report.json).

## Evidence transport at 500 VUs

The first 500-VU attempt reached the measurement phase and made recovery requests. The Spring log captured a timed-out HikariCP acquisition with all 16 connections active and 63 waiting. Modal then failed while packaging the function return. The pinned SDK attempted `BlobCreate` for an oversized result, and Modal returned HTTP 401 under restricted Function permissions. The complete k6 and durable ledgers are unavailable for that attempt. The controller now streams bounded chunks through the Modal generator API while retaining `restrict_modal_access=True`. This changes evidence transfer after k6 and recovery complete. [Initial 500-target attempt](../infrastructure/.local/modal/runs/modal-20261108T071948Z-eea578/report.json) · [Modal Function API](https://modal.com/docs/sdk/py/latest/Function).

The second call with the original transport hit the same `BlobCreate` failure. The first streaming registration then failed local SDK validation because generator functions reject the explicit `retries=0` option. No App was created. Omitting the unsupported option allowed both retries to proceed with the same backend, workload and resource limits. [Second original transfer attempt](../infrastructure/.local/modal/runs/modal-20261108T072432Z-080aa1/report.json) · [Local SDK validation](../infrastructure/.local/modal/runs/modal-20261108T072939Z-2247b1/report.json).

## Full 500-VU measurements

The first full evidence run sustained the configured 500 VUs for 120 seconds. It recorded 413.62 RPS including failed requests, p95/p99 1,936.97/3,253.76 ms and 438 measurement-window errors. Those errors were 295 client timeouts, 140 unavailable responses and three other HTTP errors. Warm-up recorded 151 unexpected outcomes. The k6 closed executor reported zero interruptions and emitted no dropped-iteration counter.

All 25,308 mutation intents matched acknowledged durable outcomes. PostgreSQL ended with 12,654 withdrawn episodes, zero active episodes and zero violations across the eight structural checks. Recovery resolved two warm-up journeys and one measurement journey through four requests, including three stored same-key replays. The original timed HTTP and aggregate SQL workload checks still failed. The ledger distinguished eight mutation timeouts after their attempt's COMMIT, twelve before that attempt's COMMIT, and twelve within the cross-host clock uncertainty bound. Ninety-five other timed-out attempts belonged to keys committed by a different attempt. [Run report](../infrastructure/.local/modal/runs/modal-20261108T073014Z-af7e73/report.json) · [Operator ledger](../infrastructure/.local/modal/runs/modal-20261108T073014Z-af7e73/operation-reconciliation.json).

HikariCP peaked at 65 pending and averaged 35.79 ms acquisition. Application section acquisition and hold averaged 3.40 and 15.94 ms. The sampled maximum PostgreSQL lock wait was 29.24 ms. Sampled blockers were commonly writing terminal idempotency outcomes, audit, or seat state. PostgreSQL also reported WAL waits. The k6 process averaged 0.29 logical cores in measurement and peaked at 398.90 MiB RSS. The worker's separate peak RSS was 170.85 MiB. These observations show no generator resource saturation in this run.

The independent repeat also sustained 500 VUs for 120 seconds. It measured 302.33 RPS, p95/p99 2,133.92/3,453.65 ms, and 1,320 measurement-window errors. Those errors were 1,279 timeouts, 37 unavailable responses and four other HTTP errors. Warm-up recorded 667 unexpected outcomes. It again reported zero interrupted iterations and emitted no drops counter. All 18,946 mutation intents matched acknowledged durable outcomes. The database held 9,473 withdrawn episodes, zero active episodes and zero structural violations. Recovery resolved all 17 incomplete journeys with 24 requests and ten stored same-key replays. Forty near-boundary mutation timeouts had an uncertain clock ordering, with each durable outcome established. [Independent repeat](../infrastructure/.local/modal/runs/modal-20261108T073708Z-d4ef30/report.json) · [Repeat operator ledger](../infrastructure/.local/modal/runs/modal-20261108T073708Z-d4ef30/operation-reconciliation.json).

| Measurement-window application timer | First 500-VU mean (ms) | Second 500-VU mean (ms) |
| --- | ---: | ---: |
| Idempotency claim | 3.254 | 4.182 |
| Student-term lock acquisition | 2.219 | 3.243 |
| Academic validation | 16.488 | 22.396 |
| Section lock acquisition | 3.398 | 5.355 |
| Seat update | 2.309 | 3.177 |
| Enrollment or withdrawal write | 2.705 | 3.748 |
| Audit write | 2.774 | 3.797 |
| Terminal idempotency write | 3.684 | 5.285 |
| COMMIT callback interval | 2.482 | 4.247 |
| Section hold | 15.940 | 23.166 |
| Total transaction | 114.717 | 125.992 |

The repeat again reached 65 HikariCP waiters, with 49.09 ms mean acquisition. The longest sampled PostgreSQL lock wait rose to 85.64 ms. The same audit and terminal idempotency blocker types recurred, along with row-lock, WAL write and WAL sync waits. k6 averaged 0.52 logical cores with 411.44 MiB peak RSS and the worker peaked at 164.16 MiB. Both full attempts used identical artifact and workload fingerprints, region, CPU and memory limits and a fresh 500-student fixture. The Sandbox had 1.5 cores and 2 GiB shared by the application and database. The separate k6 Function had one core and 1 GiB. Both used the authenticated TLS route, pool 16 and `commit_delay=0`.

The reproducible failure is connection queueing during overlapping database work. Section locks remain held during seat and episode writes, audit, durable idempotency completion and COMMIT. PostgreSQL samples identify these write stages as blockers, and the server captured JDBC acquisition timeouts with the pool full. A shared host and sampled telemetry limit attribution of the entire delay to a particular statement, host or WAL mechanism. The historical 500-VU lock hold was 21.62–25.33 ms with `commit_delay=200 µs`, while these runs measured 15.94 and 23.17 ms at zero delay and with the revised recovery harness. These are observations across configurations. They do not establish a causal 500-VU latency improvement.

Both full 500-VU attempts passed durable reconciliation after HTTP recovery and failed the timed workload gate. The confirmed mixed level for the reduced-scope artifact is 200 VUs. The 1,000–5,000 VU tiers were not run.

## Cost and verification

Modal attributed USD 0.49689187 before credits to the measured and failed launch attempts in this investigation. The two full 500-VU runs accounted for USD 0.06830586 and USD 0.05480087. The DNS failure and local SDK validation failure had no attributed billing rows. Modal reported USD 0 billed for the workspace after adjustments. App-scoped checks and final workspace inventory verified zero active Apps, Sandboxes and containers.

Verification passed for 95 backend tests, the correctness stress task, 15 Modal Python tests, the Go gateway tests, seven frontend unit tests and six browser checks. Flyway V1–V7 and the two historical 500-VU reports and ledgers matched their saved hashes.

## Reproducing the control

Use the pinned Python and Modal SDK described in [Modal deployment](modal-deployment.md). From the repository root, build the backend with `./gradlew test correctnessStress bootJar` in `backend/`. Use the following plan for the reduced-scope control. With authorization and an available budget, substitute `run` for `plan` and append `--confirm`. A 500-VU plan uses the same flags with `--vus 500` and requires a qualifying 200-VU result for the exact backend artifact, workload and fixture. Start each repetition as a separate run with a fresh database.

```sh
python infrastructure/modal/benchmark.py plan --profile mixed --vus 200 \
  --transport encrypted-tunnel --pool-size 16 --diagnose-mixed \
  --prefill-pool --spread-start --ramp-seconds 60 --ramp-start-vus 1 --max-usd .20
```

Performance Lab displays the saved reports with transaction-stage means and sample counts, PostgreSQL wait events, HikariCP, HTTP timing, durable final state, recovery and billing. Individual keys and correlation IDs remain in the local operator ledger.
