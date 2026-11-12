# Phase 8C controlled cloud benchmark

This runbook describes the bounded Phase 8C runner. The [verification report](autonomous-verification.md) records completed and failed runs. The verified Phase 8B smoke remains a separate historical record. [Phase 8B verification](modal-smoke-verification.md).

## Operator plan and approval

Use the existing Modal Python 3.12 environment with SDK 1.6.3, Java 21, Go and the verified local assets. The runner uses the Phase 8B stock images and an updated benchmark gateway. Run the following from the repository root. The `plan` command reads current workspace pricing and prints the exact cases, hard limits and estimates.

```bash
source infrastructure/.local/modal/venv/bin/activate
./backend/gradlew -p backend "-Dorg.gradle.java.home=$JAVA_HOME" bootJar
python infrastructure/modal/prepare.py
python infrastructure/modal/benchmark.py plan --suite first --max-usd 1.00
```

With operator authorization, use this command for the first suite:

```bash
python infrastructure/modal/benchmark.py run --suite first --max-usd 1.00 --confirm
```

This suite runs sequentially at 50 VUs. It schedules three steady-read repeats, three mixed repeats, one login burst and one hot-section burst. It stops at the first failed HTTP or SQL verification or unverified cleanup. A fresh Sandbox and local PostgreSQL cluster reset synthetic state for each repeat. The suite takes a local operator lock and checks that the selected Modal environment is idle before each App. The lock also protects the USD 25 project reserve check, which conservatively counts the greater of estimate and metered cost for every retained report. Keep the run directory intact. This is a host-local guard; other hosts and workspace usage require separate accounting.

Individual profiles accept `--vus 10`, `50`, `100` or `200`, `--warmup 30`, `45` or `60`, and `--repeat 1` through `3`. The CLI also accepts `500`, `1000`, `2500` and `5000` for the protected timed workloads. Closed-model escalation above 200 requires passed read and mixed reports at the previous tier. The separate `open-read` profile uses `--arrival-rate` to specify a scheduled read rate. See the current gates, resource sizing and results in the [scaling verification](cloud-scaling-verification.md).

The default HikariCP pool is 10. Use `--pool-size 16` on an individual profile to reproduce the documented pool-size experiment. Keep the profile, VUs, warm-up, artifact and compute limits fixed. The default UI launch uses pool 10. In the original comparison the 200-VU warm-up gate failed for both configurations. [Original verification](autonomous-verification.md) · [Release follow-up](performance-capacity-report.md).

For an approved transport diagnostic, add `--transport encrypted-tunnel` to an individual closed profile. It forwards only the benchmark gateway port over TLS and keeps a per-run secret gate, student sessions and the method/path allowlist. Preflight verifies rejection without the gate key and that management and admin paths are hidden. This mode is available only in the CLI. Normal starts still use Sandbox Connect. [Release investigation](bottleneck-analysis-final.md).

```bash
python infrastructure/modal/benchmark.py plan --profile steady-read --vus 100 --warmup 30
python infrastructure/modal/benchmark.py plan --profile steady-read --vus 200 --warmup 30
```

To execute an approved individual case, replace `plan` with `run` and add `--confirm`. The first suite budget applies to its eight cases. Additional cases need a separately reviewed budget.

## Resource and cost limits

Spring Boot and PostgreSQL share one Sandbox. Both Sandbox and k6 Function request Modal `us-east`. Provider selection is managed by Modal. The runner verifies the observed region before traffic. PostgreSQL uses temporary local storage with durability enabled, Spring Boot uses the existing artifact and Hikari max 10, and the generator runs separately. CPU and memory numbers below are both reservations and hard limits. The Function has one single-use container, zero warm/buffer containers and no configured retries. Across the suite, there is one Sandbox and at most one generator at a time.

| Resource | CPU | RAM | Maximum billable-time inputs | Estimated USD per case |
| --- | ---: | ---: | --- | ---: |
| Sandbox | 1.5 physical cores | 2,048 MiB | 540 s | 0.068473 |
| Generator | 1 physical core | 1,024 MiB | 45 s startup + 350 s invocation + 5 s idle | 0.010753 |
| Traffic allowance | | | 1 GiB | 0.040000 |
| Per-case estimate | | | | 0.119226 |

At the workspace rates checked on November 7, 2026, the eight cases plus a $0.025 suite reserve total **$0.978807 before credits**. The calculation uses 1.75× narrow-region pricing, Sandbox rates of $0.14190 per core-hour and $0.02400 per GiB-hour, Function rates of $0.04730 and $0.00800, and $0.04 per GiB egress. Pricing is refreshed before provisioning and the runner rejects a planning total above the operator ceiling, which itself cannot exceed $1. The gateway caps authenticated proxied requests at 80,000, each response body at 64 KiB and aggregate response payload at 256 MiB. The traffic allowance also covers overhead and bounded artifact transfers. It is a planning allowance rather than a provider invoice cap. The workspace spend control is available for provider-side billing limits. [Modal pricing](https://modal.com/pricing), [regional pricing](https://modal.com/docs/guide/region-selection), [workspace budgets](https://modal.com/docs/guide/budgets).

The runner obtains the timed origin from the newly created Sandbox Connect Token or authenticated TLS tunnel. The remote worker verifies authorization, the separate gateway key, student-session enforcement and the unavailability of management routes before warming or measuring. Spring Boot, PostgreSQL and Actuator bind to loopback. The authenticated gateway accepts only fixture paths and retains request and response budgets. The baseline Sandbox has a 540-second hard expiry. Scaled cases set bounded time and traffic limits during planning. Cleanup terminates the Sandbox in `finally`, exits the ephemeral App, and checks the exact App state and active resource inventory before another case. [Stop and cleanup procedures](modal-deployment.md#evidence-monitoring-and-stop).

## Workload and measurement

Each database starts from Flyway V1–V7. The baseline uses the fixed `cloud-bench-v1` synthetic fixture with 205 students and twenty 32-seat mixed sections. Scaled cases use one synthetic account per configured VU and at least two spare seats per assigned mixed section. Each actor attempts its own withdrawal before its next registration. One existing 30-seat synthetic section is reserved for the hotspot. The original benchmark data and business policies stay separate.

| Profile | Warm-up | Measurement and expected behavior |
| --- | --- | --- |
| Steady read | Authenticated reads at selected VUs | 120 s of paced course, schedule, own enrollment and current-student reads |
| Mixed | The same mixed profile at selected VUs | 120 s of paced reads and a registration/withdrawal pair every fourth iteration, with capacity headroom |
| Login burst | Read warm-up with a separate five-student cohort | One simultaneous login attempt per VU, with anonymous sessions and CSRF prepared separately. The existing admission gate can return 429. |
| Hot section | Authenticated reads at selected VUs | One enrollment intent per VU against 30 seats. Expect `min(VUs, 30)` successes and the remaining SECTION_FULL outcomes. |

Session preparation runs with four bounded workers before k6. Warm-up lasts 30–60 seconds in a separate k6 process. Steady read and mixed use constant VUs and a 1.25-second minimum iteration period. The login and hot burst have a 30-second one-shot limit and are reported separately from steady state. Registration and withdrawal each receive an Idempotency-Key. A single retry of an ambiguous response reuses that intent's key. An unexpected original outcome still invalidates the clean run even if its durable state is recovered.

For steady workloads, achieved RPS is the number of request attempts completing in the active 120-second window divided by 120. HTTP p50, p95 and p99 describe responses in that window and omit attempts with no HTTP response. A bounded drain finishes in-flight operations outside the window. The runner keeps full-run safety counters and validates durable enrollment, withdrawal, audit and terminal outcomes across warm-up, measurement and drain. Burst RPS uses the observed k6 runtime. The selected VUs define the offered concurrency. Started iterations show observed work. Closed-model VUs do not specify a fixed offered RPS. Dropped iterations display No Data if k6 does not emit that counter. Interrupted iterations use starts minus completions.

Reports distinguish HTTP successes, business denials, 429, 503, timeouts, other transport errors, HTTP errors, invalid responses and gateway limits. Expected login 429 and hotspot SECTION_FULL are reported separately. Each successful case must reconcile exact operation counts and pass the eight independent SQL invariants. Mixed requires successful writes in both phases and zero active rows at the end. Read and login profiles require zero enrollment effects.

## Monitoring and interpretation

A loopback sampler reads allowlisted Spring Actuator Prometheus metrics and aggregates PostgreSQL connection counts and wait events at roughly five-second intervals. The controller selects samples inside the timed window. Peaks are sampled values. Hikari acquisition, section lock query and transaction means use first/last counter deltas. Sample coverage and the sample span accompany the report. A short burst can fall between samples. Missing values, counter resets and timers with no sampled operations stay unavailable.

Sandbox and Function cgroup CPU and peak memory are recorded where the runtime exposes them. The Function also records process-level CPU time and peak RSS separately for its Python worker and k6 child. Those process counters are not container-wide usage. The Sandbox may return No Data for cgroup fields. The shared Sandbox makes CPU and RAM contention between Spring and PostgreSQL a limitation of all comparisons. A section-lock query includes the JDBC round trip. The UI flags observable queues, PostgreSQL Lock waits, repeated Spring CPU pressure and login admission separately. These are investigation signals. A completed run does not establish maximum backend capacity.

The ignored per-run directory holds warm-up and timed k6 summaries, sample frames, sanitized logs, independent SQL evidence, image and workload fingerprints, and cleanup status. The Phase 8C runner saves aggregate k6 evidence rather than per-request raw points. Use the existing billing reconciler to attach actual Modal metered usage after the report appears.

```bash
python infrastructure/modal/billing.py --date YYYY-MM-DD
```

Performance Lab **Cloud runs** accepts Phase 8B and 8C records. Select a completed report to inspect evidence. Comparison rows show medians of verified comparable runs. Profile, region, fixture, build fingerprints, limits and protocol must match. A row without qualifying runs is Not Tested. Scaled closed-model qualification and open-model plateaus have separate tables. Billing is shown before credits only when attributable to the exact App and run tag.

The mixed diagnostic mode records per-attempt durable outcomes and PostgreSQL lock edges. It supports a shared-Sandbox control and a separate authenticated database topology. Performance Lab keeps those results separate. [Mixed database diagnostic runbook](modal-mixed-database-diagnostics.md).

## Preparation verification

The original local preparation compiled the benchmark gateway and validated the 16 baseline profile/VU combinations with k6 inspect. Isolated loopback checks exercised the login burst and a hot-section 503 retry with the same Idempotency-Key. A fresh PostgreSQL fixture verified the 205 accounts and 640 mixed seats, and Spring Boot completed secure CSRF session preparation and a separate anonymous login-burst cohort. The JVM suite passed 92 regular tests and the existing correctness stress test. See the [scaling verification](cloud-scaling-verification.md) for the latest executed cloud cases.
