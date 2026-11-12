# Student Portal and performance verification

Verified on November 7, 2026. All accounts, courses and enrollment operations used synthetic data. Cloud measurements and the local hotspot experiments are separate from the 523 original Phase 6 artifact files. Those files and the seven historical Flyway migrations match their preexisting SHA-256 manifest.

## Student and operator workflows

The [demo guide](demo.md) starts a disposable PostgreSQL 18 database and Spring Boot listener on loopback. Next.js serves the catalog, sections, instructors, seat snapshots, mutations and the authoritative timetable. The same-key browser recovery test lets Spring commit the enrollment, substitutes a transport failure at the browser boundary, reloads the page and verifies that the original key returns the same enrollment ID. It then verifies credits, withdrawal and sign-out.

Performance Lab keeps Phase 6, local experiments and Modal runs separate. It displays observed and configured VUs when available, SQL invariant counters, classified failures, measured-window resource charts, billing and cleanup. Live Prometheus access requires a Spring ADMIN session and uses fixed bounded server queries. The separate, opt-in controls require ADMIN, same origin, CSRF and explicit confirmation.

## Regression verification

- JVM: 93 functional and integration tests and one service concurrency test. The latter exercised 2,000 distinct students with four workers and confirmed 30 enrollments, 1,970 full denials and zero technical or invariant failures.
- Next.js: seven frontend unit tests, typecheck and optimized build. Nine Playwright tests pass. The browser suite uses the actual Spring, PostgreSQL and private Prometheus stack for the student and administrator flows.
- Modal: Python unit tests and the Go gateway tests pass. The runner verifies the exact App, Sandbox and generator cleanup after each run. An authenticated live start/stop check proved that the saved report records an interruption and that the App, Sandbox and Function are cleaned up. The first stop check cleaned up resources but retained an incorrect running status. The retry verified the status fix; both receipts are retained.

## Modal benchmarks

The generator and Sandbox used `us-east`. Spring and PostgreSQL shared one Sandbox with 1.5 physical CPU cores, 2 GiB RAM and durability enabled. k6 used a separate 1-core, 1-GiB Function. Steady profiles used separate authentication, 30 seconds of warm-up and 120 seconds of measurement. RPS includes all measured-window request attempts. The latency columns describe HTTP responses. The unexpected counts include errors from every route in the corresponding window.

| Run | Profile | Configured / observed VUs | Requests | RPS | p50 ms | p95 ms | p99 ms | Warm-up / measurement errors | SQL / cleanup | Result |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| [`modal-20261107T132420Z-7f39e2`](../infrastructure/.local/modal/runs/modal-20261107T132420Z-7f39e2/report.json) | steady-read | 50 / 50 | 4,800 | 40.00 | 40.50 | 87.93 | 104.39 | 0 / 0 | Verified / Verified | Passed |
| [`modal-20261107T132745Z-d9e787`](../infrastructure/.local/modal/runs/modal-20261107T132745Z-d9e787/report.json) | steady-read | 50 / 50 | 4,800 | 40.00 | 27.28 | 130.37 | 187.65 | 0 / 0 | Verified / Verified | Passed |
| [`modal-20261107T133052Z-892747`](../infrastructure/.local/modal/runs/modal-20261107T133052Z-892747/report.json) | steady-read | 50 / 50 | 4,800 | 40.00 | 96.65 | 229.51 | 276.18 | 0 / 0 | Verified / Verified | Passed |
| [`modal-20261107T133402Z-f2243d`](../infrastructure/.local/modal/runs/modal-20261107T133402Z-f2243d/report.json) | mixed | 50 / 50 | 7,198 | 59.98 | 48.80 | 98.84 | 118.56 | 0 / 3 | Verified / Verified | Failed |
| [`modal-20261107T133910Z-0d1a70`](../infrastructure/.local/modal/runs/modal-20261107T133910Z-0d1a70/report.json) | mixed | 50 / 50 | 7,196 | 59.97 | 50.84 | 182.28 | 246.94 | 0 / 3 | Verified / Verified | Failed |
| [`modal-20261107T134407Z-d33670`](../infrastructure/.local/modal/runs/modal-20261107T134407Z-d33670/report.json) | mixed | 50 / 50 | 7,200 | 60.00 | 49.34 | 184.92 | 251.48 | 0 / 0 | Verified / Verified | Passed |
| [`modal-20261107T134825Z-f2747e`](../infrastructure/.local/modal/runs/modal-20261107T134825Z-f2747e/report.json) | mixed | 50 / 50 | 7,200 | 60.00 | 20.81 | 140.69 | 230.37 | 0 / 0 | Verified / Verified | Passed |
| [`modal-20261107T135131Z-90de70`](../infrastructure/.local/modal/runs/modal-20261107T135131Z-90de70/report.json) | steady-read | 100 / 100 | 9,600 | 80.00 | 32.39 | 181.34 | 252.00 | 0 / 0 | Verified / Verified | Passed |
| [`modal-20261107T135443Z-d134b7`](../infrastructure/.local/modal/runs/modal-20261107T135443Z-d134b7/report.json) | mixed | 100 / 100 | 14,400 | 120.00 | 104.09 | 355.38 | 495.63 | 0 / 0 | Verified / Verified | Passed |
| [`modal-20261107T135802Z-cb6fdf`](../infrastructure/.local/modal/runs/modal-20261107T135802Z-cb6fdf/report.json) | steady-read | 200 / 200 | 19,200 | 160.00 | 116.72 | 439.47 | 581.84 | 19 / 3 | Verified / Verified | Failed |
| [`modal-20261107T140255Z-6298d3`](../infrastructure/.local/modal/runs/modal-20261107T140255Z-6298d3/report.json) | steady-read, pool 16 | 200 / 200 | 19,200 | 160.00 | 150.09 | 404.53 | 526.02 | 4 / 0 | Verified / Verified | Failed |
| [`modal-20261107T140654Z-17796b`](../infrastructure/.local/modal/runs/modal-20261107T140654Z-17796b/report.json) | login-burst | 50 / 19 | 50 | 36.21 | 551.85 | 1,257.98 | 1,306.16 | 0 / 0 | Verified / Verified | Passed |
| [`modal-20261107T140806Z-ccdcec`](../infrastructure/.local/modal/runs/modal-20261107T140806Z-ccdcec/report.json) | hot-section | 50 / No Data | 50 | 142.39 | 233.56 | 294.05 | 308.46 | 0 / 0 | Verified / Verified | Passed |
| [`modal-20261107T141035Z-af595a`](../infrastructure/.local/modal/runs/modal-20261107T141035Z-af595a/report.json) | steady-read | 10 / No Data | No Data | No Data | No Data | No Data | No Data | No Data / No Data | Not Tested / Verified | Status mismatch |
| [`modal-20261107T141233Z-25a75d`](../infrastructure/.local/modal/runs/modal-20261107T141233Z-25a75d/report.json) | steady-read | 10 / No Data | No Data | No Data | No Data | No Data | No Data | No Data / No Data | Not Tested / Verified | Interrupted |

### Reliability and configuration investigation

- Two initial 50-VU mixed runs failed with three unexpected responses each. The diagnostic repetition identified three HTTP 502 responses. Two subsequent mixed repetitions passed before the next level was attempted.
- At 100 VUs, steady read passed at 80.00 RPS and mixed passed at 120.00 RPS. The mixed profile sampled Hikari pending connections in 5 of 24 samples, with a peak of 55. Mean acquisition was 15.01 ms and PostgreSQL recorded up to four lock waiters. The 5-second sampling interval gives snapshots of transient pressure.
- The default 200-VU read run failed during warm-up and measurement. Warm-up recorded 17 timeouts, one backend 503 and one backend 500. Spring logged `CannotCreateTransactionException`. The measurement recorded three HTTP 502 responses without the diagnostic header that identifies Spring responses or gateway-generated errors. Their exact upstream source remains unresolved.
- The pool-16 read experiment retained the same fixture, pacing, backend JAR, region and compute limits. Measurement had zero unexpected responses and the warm-up error count fell from 19 to 4. Sampled pending peaked at 64 versus 52, and mean acquisition changed from 16.89 to 16.10 ms. The warm-up gate still failed. Keep pool 10 as the default.
- The backend now classifies a confirmed transient transaction acquisition failure as retryable 503 with `Retry-After: 1`. Permanent acquisition failures still produce 500. JVM regression tests cover both branches. The Modal pool comparison uses the original benchmark artifact and isolates the configuration experiment from this error-contract change.
- The 200-VU result is a failed workload gate even though the pool-16 measurement window served 160.00 RPS. Mixed 200 VUs and 500–5,000 VUs are **Not Tested**. The current cloud fixture is bounded to 205 accounts, 640 mixed-workload seats and 80,000 gateway requests. Larger runs require a new measured fixture and request budget after the 200-VU gate is met.
- The 50-VU login burst completed 50 requests with 25 accepted logins and 25 expected HTTP 429 responses. The 50-VU hotspot independently committed 30 enrollments and 20 `SECTION_FULL` outcomes. The short burst RPS uses the observed runtime and cannot be compared directly with the steady windows.
- Spring process CPU and whole-Sandbox/whole-Function cgroup telemetry were unavailable in these runs. JVM heap, pool counters, PostgreSQL connection and wait snapshots, k6 RSS/CPU, and worker process RSS/CPU are retained. k6 does not emit dropped iterations for every executor. Missing counters remain **No Data**.

Modal attributed `$0.34696602` before credits to 15 of 15 completed Phase 8C attempts. The recorded per-run estimates total `$1.78839`, excluding suite reserves. Total metered project evidence including the earlier Phase 8B receipts is `$0.37658796` before credits. The first suite was planned at `$0.97881`, and an individual case was planned at `$0.14423` including the operator reserve. The runner checks the host-local `$25` project reserve under its operator lock. [Modal pricing](https://modal.com/pricing) · [Cloud runbook](modal-controlled-benchmark.md).

## Local HTTP hotspot and spike

The independent local experiment used the `semester-v1` synthetic fixture and the same backend image for both lock configurations. The hotspot used 2,000 authenticated students and 20 k6 VUs. Each configuration had a 30-second read warm-up, then 2,000 attempts on one 30-seat section.

| Profile | Requests | RPS | HTTP p95 ms | HTTP p99 ms | Successful enrollment p95 ms | Unexpected / dropped |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| [Early section lock](../infrastructure/k6/results/autonomous-hot2000-early-warmed-v20/summary.json) | 2,000 | 120.95 | 21.13 | 63.12 | 400.24 | 0 / 0 |
| [Late section lock (default)](../infrastructure/k6/results/autonomous-hot2000-late-warmed-v20/summary.json) | 2,000 | 113.78 | 90.97 | 227.68 | 521.96 | 0 / 0 |
| [Registration spike](../infrastructure/k6/results/autonomous-spike15/summary.json) | 810 | 25.28 | 20.09 | 28.83 | 23.28 | 0 / 0 |

Both warmed hotspot configurations produced exactly 30 `ENROLLED`, 1,970 `SECTION_FULL`, 2,000 durable outcomes, 30 enrollment audit events and zero violations. A first early-lock attempt without the matched warm-up missed the successful-enrollment p95 threshold at 1,334.46 ms. The warmed comparison reversed that direction, so the single pair does not justify a locking change. One late warm-up was aborted by the existing host swap guard. Its evidence remains saved, and the repeat used the same thresholds after stopping Grafana to reduce shared-host pressure.

The spike ramped from 5 to 15 offered journeys per second with a ceiling of 24 k6 VUs. It completed 270 journeys and 810 HTTP requests with no errors, drops or SQL invariant violations. The RPS in the table is the full-run average. The hotspot and spike are local evidence from a shared host and should be interpreted separately from Modal.

## Reproduction

[Student demo and checks](demo.md) · [Architecture](architecture.md) · [Database ERD](database-erd.md) · [API contract](api-specification.md) · [Cloud limits and cleanup](modal-controlled-benchmark.md)
