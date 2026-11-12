# HTTP baseline and reliability methodology

Phase 6 measures the real Kotlin/Spring/PostgreSQL HTTP path before optimization. Current local measurements use at most 10 VUs because the shared 8-GiB Mac has substantial swap and the user explicitly chose to run higher load on another computer. Prepared large workloads are not completed measurements. No capacity claim follows from a configured VU count or from Phase 5's service-only allocation.

## Toolchain and safe dataset lifecycle

Use JDK 21, the Gradle wrapper, Docker Compose, Python 3 and psql. On Windows use WSL2 with Docker Desktop integration, Linux JDK/Python/psql and the project on the WSL filesystem. macOS and Linux are supported by the scripts; Windows/WSL execution is documented but was not executed here. Export JAVA_HOME explicitly.

Follow [monitoring setup](../infrastructure/monitoring/README.md). The isolated database is portal_benchmark on loopback port 16432, under the fixed sdu-registration-benchmark Compose project. The reset guard requires its role, project, random database marker and stopped backend. Seven automated safety tests verify rejection of wrong project/database/role/missing marker/wrong marker/running backend. No arbitrary target URL, DROP DATABASE, destructive volume command or modified historical migration exists.

Both fixture variants use seed 20261106. UUIDv5 identifiers and capacities are reproducible. The Phase 6 fixture contains 5000 students/accounts/student-term/evidence/completion records, 250 courses/offerings/allowlist entries, 600 sections, 1200 required occurrences, two open operation windows and one published policy/gate. Its generator explicitly emitted two dated occurrences per section, one per week for two weeks. The accepted Phase 6 report records the reduced fixture. The earlier material does not record a separate rationale for reducing schedule cardinality. `phase6-v1` preserves those IDs, dates and counts. `semester-v1` changes the schedule to 14 teaching weeks with two distinct weekday meetings per section per week, yielding 16,800 required dated occurrences. Meeting slots stay inside the fixture term. It keeps the same student, course and section distribution and does not model holidays, staff availability or room constraints. Every student has COMPLETE/ACTIVE evidence, 18.00 policy credits and a matching passed prerequisite completion; offerings have 3.00 credits. First 100 sections have 100 seats each and distribute the 5000 success-profile students across 10000 seats. The independent hotspot is a designated 30-seat section. Other capacities vary across 20/40/60/100. Catalog read weights favor 10 hot courses (60%), 60 warm courses (30%) and 180 cold courses (10%). These are synthetic workload assumptions, not SDU policy or observed student behavior.

Accounts have one random isolated fixture password outside Git, with a real BCrypt cost-10 verifier. The shared synthetic password avoids spending setup time generating 5000 unrelated verifiers; every actual HTTP login still performs BCrypt verification. IDs/academic choices use the fixed seed; created/window timestamps and secrets are intentionally regenerated. No real account or transcript is imported.

Each variant has a checked-in definition and a separate generated manifest with verified PostgreSQL counts, meeting hash and source hash. `dataset.py seed --variant phase6-v1` remains the default. Changing variants requires the guarded seed operation and fresh sessions. [Phase 6 manifest](../infrastructure/benchmark/datasets/phase6-v1.json), [semester manifest](../infrastructure/benchmark/datasets/semester-v1.json).

## Reproducible small run

From the root after the stack is ready:

```sh
python3 infrastructure/benchmark/install-k6.py
python3 infrastructure/benchmark/test_safety.py
python3 infrastructure/benchmark/dataset.py sessions --count 100
python3 infrastructure/benchmark/run.py --mode auth --vus 5 --max-vus 10 --name auth5
python3 infrastructure/benchmark/run.py --mode smoke --vus 10 --max-vus 10 --name smoke10
python3 infrastructure/benchmark/run.py --mode warmup --vus 5 --max-vus 10 --duration 10s --name warmup5
python3 infrastructure/benchmark/run.py --mode normal --vus 10 --max-vus 10 --duration 20s --name normal10
python3 infrastructure/benchmark/run.py --mode arrival --vus 5 --max-vus 10 --rate 10 --duration 20s --name arrival10
python3 infrastructure/benchmark/run.py --mode ramp --vus 5 --max-vus 10 --rate 20 --duration 10s --name ramp20
python3 infrastructure/benchmark/run.py --mode spike --vus 5 --max-vus 10 --rate 10 --name spike10
```

Each run name is new; evidence is never overwritten. Session preparation is a separate real HTTP CSRF/login/refresh flow with four workers. Cached sessions are checked and expired sessions are reauthenticated. A 25-minute freshness preflight rejects an old preparation before generating traffic. Login is not repeated each ordinary iteration. k6 also tests the login/logout flow explicitly in auth. On macOS a process-scoped caffeinate assertion prevents idle sleep during a run; explicit system suspension can still invalidate it.

`normal` is closed model with 0.5–1.5 second think times and a deterministic action choice. `arrival`/`ramp` issue exactly one read per iteration, so its nominal request offer matches the scheduled iteration rate when each iteration starts. Measure achieved HTTP RPS independently, including effects of drops and interruptions. `spike` runs enrollment/schedule/withdrawal journeys: configured iteration rate is **not** total HTTP RPS. Hot mode uses one distinct student per iteration and never withdraws; with 100 local attempts it expects 30 ENROLLED/70 SECTION_FULL. The prepared 2000-attempt mode instead expects 30/1970 when every eligible attempt completes. Any missing attempt or mismatch between HTTP results, durable outcomes, audit, active rows and the section counter fails complete allocation. `distributed` makes one write per student across up to 100 independently provisioned sections and checks the exact number of active seats and distinct students and sections.

## Controlled repetition and AD-13

For comparable repetitions, stop the isolated backend, run dataset.py reset-run, restart the same image, perform the same recorded warmup and then the measured profile. Academic fixture, active state, JVM/container/pool limits, script and seed remain identical. Initial cold smoke, warmup and target workload are separate artifacts. Earlier exploratory repeats with growing retained history are identified separately; they are not substituted for reset-controlled measurements.

`python3 infrastructure/benchmark/compare-locking.py` performs two repetitions of late and early section locking, each from a reset ledger and restarted identical backend, with a five-student smoke warmup and 100 hotspot attempts, five workers and 0.2-second pacing. Early mode exists only under the explicit benchmark profile and is false by default. Both variants preserve claim → gate → student → section ordering, all rules and post-wait time checks. It is an instrumentation/comparison option, not a shipped optimization or a second default protocol.

Fresh protected Prometheus snapshots before/after each profile provide counter-difference mean durations for student/section lock query, section hold, academic preparation, root attempt and commit. No resource ID label is needed because the isolated interval has only the declared workload. Section hold starts when the lock query returns and ends after transaction completion: it approximates physical hold time and includes local completion/acknowledgement overhead. Lock-query durations include SQL/network overhead and are not pure PostgreSQL wait-event time. Use PostgreSQL wait samples to distinguish contention.

The root-attempt timer includes transaction interceptor/pool acquisition/commit or rollback; stage timers overlap and must not be added to infer a complete timeline. Authentication timing covers account validation, while session lookup/persistence and framework/network time also contribute to HTTP latency. SQL client timers include client/JDBC overhead; pg_stat_statements gives server execution/I/O separately. All metrics are nonfatal and audit remains durable.

## Windows load stages

Run a single stage at a time from the isolated Windows setup. Prepare and refresh at least as many distinct sessions as the configured VU maximum, or as many attempts for the hot section. Record a warmup separately and use identical fixture and resource limits across comparable runs. Reset the dedicated ledger between independent write comparisons. For a steady mixed series record the intended starting history and use that same policy for both sides of a comparison. [Windows benchmark setup](windows-benchmark-setup.md).

| Closed model VUs | Purpose | Initial measured hold |
| ---: | --- | ---: |
| 10 | Validate session freshness, metrics, status classification and SQL assertions | 30 s |
| 50 | Smoke and ordinary mixed traffic | 30 s |
| 200 | Low load and first repeatable baseline | 45 s |
| 500 | Moderate load | 45 s |
| 1,000 | High load | 60 s |
| 2,500 | Controlled stress | 60 s |
| 5,000 | Target experiment after independent generator and host review | 60 s |

The hold is preceded by a 10-second ramp and followed by a 5-second step down. Launch exactly one chosen level after reviewing the preceding result. For example, the following is the 50-VU step. Update the selected VUs, duration and reviewed resource budgets explicitly for each later step.

```bash
python3 infrastructure/benchmark/dataset.py sessions --count 50
python3 infrastructure/benchmark/run.py --mode ladder --vus 50 --max-vus 50 \
  --resource-budget-vus 50 --duration 30s --generator-memory-mb 768 \
  --generator-cpu-percent 300 --minimum-free-ram-mb 2048 --name win-semester-ladder50-r1
```

Open-model read steps offer **100, 250, 500 and 1,000 iterations/s**. Measure the first step before sizing the next one. Use the previous step's iteration duration to estimate concurrency, roughly rate × seconds of execution, then leave measured headroom. Report scheduled versus started/completed iterations, dropped iterations, actual HTTP requests/s and generator utilization. A VU ceiling or offered rate is an input to the experiment. The following allocations are provisional starting budgets and must fit the measured resources.

| Read iterations/s | Preallocated VUs | Maximum VUs | Hold |
| ---: | ---: | ---: | ---: |
| 100 | 25 | 75 | 30 s |
| 250 | 75 | 200 | 45 s |
| 500 | 150 | 400 | 45 s |
| 1,000 | 300 | 800 | 60 s |

```bash
python3 infrastructure/benchmark/dataset.py sessions --count 75
python3 infrastructure/benchmark/run.py --mode arrival --rate 100 --vus 25 --max-vus 75 \
  --resource-budget-vus 75 --duration 30s --generator-memory-mb 1024 \
  --generator-cpu-percent 300 --minimum-free-ram-mb 2048 --name win-semester-read100-r1
```

| Scenario | Profile and independent check |
| --- | --- |
| Preauthenticated student reads | `warmup`, `arrival` or `ramp` for catalog, enrollment history and schedule. Check HTTP statuses and latency by endpoint and no write delta in a read-only interval. |
| Login opening burst | `auth` in a separate cold or explicitly warmed window. Begin with 5/10/50 VUs as independently named steps. Report authenticated completions and 429 separately. The eight-verification gate can reject legitimate attempts at peak and invalidates the healthy SLO hypothesis for that burst. |
| Distributed enrollment | `distributed --vus N --max-vus N` after a guarded reset with N prepared sessions. Expect N 201 results and N active rows in up to 100 sections, with the matching operation and audit deltas. |
| Contended 30-seat section | `hot --vus 32 --max-vus 32 --iterations 2000 --hot-pause 0` after reset and read warmup, with 2,000 distinct prepared sessions. Require exactly 30 201/ENROLLED and 1,970 SECTION_FULL, 30 active distinct students and counter 30. Keep any incomplete run as overload evidence. |
| Mixed enrollment and withdrawal | `normal` or `ladder` with preauthenticated actors, reads, enrollment, schedule verification and withdrawal. Check withdrawals and same-key replay separately. |
| Sudden registration spike | `spike` starts at five journeys/s, steps to an explicitly selected target for ten seconds and returns to five. Report achieved HTTP RPS separately because a journey can issue several requests. Review unexpected responses, latency, Hikari pending and PG waits by window. |
| Recovery | Inspect the final 15 seconds at five journeys/s and then run `normal --vus 10 --max-vus 10 --duration 20s` under a new name without resetting, after verifying readiness. Compare to the pre-spike ten-VU profile. Check that new writes complete, no queue remains and the ledger/audit match. The generator retries enrollment in the same iteration with the original key. If the run stops mid-iteration, retain partial evidence and reconcile authoritative state before resetting. |

All write runs execute independent PostgreSQL checks for capacity/counter reconciliation, active uniqueness, credits, meeting overlap, terminal outcome integrity and audit presence. For hot and distributed runs the runner also checks exact HTTP counts, distinct active students, distinct sections and operation/audit deltas. Failed or interrupted runs retain raw evidence and any collected invariants. No Docker Compose or Gradle target starts a large workload.

## Resource and validity gates

The runner defaults to a 50-VU operator budget and hard-caps macOS and hosts with at most 8 GiB of visible RAM at 50 VUs. This Mac was used only at up to 10 VUs in Phase 7A. For Windows, keep at least 2 GiB available in WSL and separately monitor Windows physical memory, paging, CPU and disk. Choose a generator RSS budget before each step. Start with 768 MiB for smoke and explicitly size higher stages using observed headroom. Choose a k6 CPU budget with 100% per logical core. An initial shared-machine ceiling is 300% for k6, adjusted only after reviewing contention and host CPU.

The wrapper interrupts the stage on more than 256 MiB swap growth, free RAM below the configured Linux/WSL floor, k6 RSS over budget, three CPU samples over budget, three failed liveness samples, a failed or unresponsive resource sampler, a sample gap over 15 seconds or a wall-clock overrun (300 seconds by default). Resource stops persist a `RESOURCE-STOP` marker. Review the evidence before clearing it. Ctrl+C preserves partial evidence and gives k6 a bounded flush interval. k6 thresholds abort on timeouts, dropped iterations or sustained unexpected responses under the configured evaluation delay. A nonzero generator exit, a missing fixed-iteration target, failed evidence collection or violated SQL invariant fails the run.

Stop manually if Windows paging grows persistently, Windows CPU remains above roughly 90% or available physical RAM falls below 4 GiB for about 15 seconds, WSL/Docker becomes unresponsive, or storage headroom threatens evidence capture. Review container CPU throttling, heap/GC, PostgreSQL locks/I/O and readiness after each step. If the generator is the first saturated component, repeat on a separate generator and record network and TLS details. Increase one axis only after the preceding stage is valid. An overloaded test, a threshold failure or an extended host gap stays in the evidence under its actual outcome.

## Results and SLO classification

Per-run artifacts include source file hashes, workload source, k6 version, fixture manifest, deployed image and JAR identities, actual Compose and Hikari limits, host and resource samples, raw gzipped metrics, k6 summary, classified per-endpoint/result latencies, Prometheus before/after and time series, normalized runtime SQL and independent invariant checks. Retain the Phase 6 raw and report assets in their original directories. Compare a baseline and candidate on the same hardware and workload with the same fixture, configuration and generator placement. Keep Mac M1 and Windows i5 results separately labeled. Setup and failure runs are retained and labeled.

Use actual k6 http_reqs rate/duration for achieved RPS; runner wall duration also includes sampling/collection and is not a throughput denominator. Request samples independently produce p50/p95/p99 for success, replay and business denial. Report successful enrollment and SECTION_FULL separately. Count 429/503, network errors, timeouts, dropped iterations and unplanned outcomes explicitly. The technical-error hypothesis applies to portal_unexpected, not built-in http_req_failed (which includes expected 409). Short/cohort-limited percentiles are descriptive and have no capacity confidence interval.

The provisional hypotheses are enrollment-success p95 ≤1000 ms, main API p99 ≤2000 ms, unexpected errors <0.5%, and zero capacity/uniqueness/counter-ledger violations. Evaluate them only for a named valid workload. A small passing profile neither establishes 5000-user performance nor makes overload responses successful service.

## Reliability

`python3 infrastructure/benchmark/reliability.py` resets only the marked isolated fixture and uses real container SIGKILL, HTTP proxy socket loss, a real clean PostgreSQL stop/restart and actual Hikari exhaustion. Do not run it concurrently with another experiment. It distinguishes audit INSERT before COMMIT, COMMIT already executing a deferred trigger, commit acknowledged upstream but hidden from the client, and an ordinary lost response. Every case checks database state and same-key recovery, not merely a returned HTTP status. Controlled SQL sleep triggers are instrumentation for an observable point; the application process really dies.

A real disconnect during COMMIT may still commit. The test records the eventual server result and recovers with the same key; it never treats client death as automatic rollback. PostgreSQL durability is never disabled. The Phase 7A run requires safe HTTP 503 during database unavailability and pool exhaustion, with an independent recovery check. Fault-profile HTTP 500/503 and disconnects are reported separately from the healthy baseline. [Phase 6 performance report](phase6-performance-report.md), [Phase 7A reliability verification](phase7a-reliability-verification.md).
