# Performance requirements and validation plan

Status: [Phase 6](phase6-performance-report.md) measured real small HTTP profiles (at most 10 VUs), observed PostgreSQL/Hikari/JVM behavior and exercised isolated failures. The 5,000-user performance hypotheses below remain unverified. Phase 5 service allocation and the small Phase 6 hotspot are correctness/limited baseline evidence, not a capacity benchmark.

## Preliminary targets

| ID | Metric | Unverified target hypothesis | Future acceptance evidence |
| --- | --- | --- | --- |
| PERF-01 | Simultaneously active virtual students | 5,000 in the agreed mixed workload | k6 scenario configuration, observed active VUs, independent synthetic identities and achieved throughput during the measurement interval |
| PERF-02 | Enrollment decision p95 latency | ≤ 1 second | End-to-end HTTP attempt timing for enrollment under PERF-01; report all attempts and successful-commit cohort separately |
| PERF-03 | Enrollment decision p99 latency | ≤ 2 seconds | Same measurement scope and cohorts as PERF-02, with sample counts |
| PERF-04 | Unexpected request failures | < 0.5% | Global and per-critical-operation numerator/denominator, including transport, unplanned responses and overload; no hiding expected failures by status alone |
| PERF-05 | Observed section capacity violations | 0 | Independent database invariant checks and concurrency evidence; include counter/ledger agreement |
| PERF-06 | Observed duplicate active enrollments | 0 | Database uniqueness verification and post-run grouping by student/section and provisional student/offering identity |

BR-01 through BR-08 are mandatory correctness requirements. Calling zero observed violations a hypothesis means verification is pending, not that violating correctness is acceptable. Credit-limit and meeting-conflict violations must also be absent in the later correctness suite. A fast system that rejects every eligible student fails functional/allocation acceptance even if safety checks are clean.

## Active students, RPS, and in-flight requests

- **Concurrent users / active VUs:** simulated students executing independent journeys during the same interval. They can be reading, thinking, waiting, or making requests; an authenticated session alone does not prove activity.
- **Requests per second (RPS):** request arrival or completion rate over time. Distinguish offered RPS from completed RPS, overall from enrollment RPS, and requests from journey iterations.
- **Simultaneous HTTP requests:** requests in flight at an instant, including network/server waiting. This is different from user count and database connection count. A user journey may have sequential or parallel calls; HTTP/2 may multiplex requests on connections.

Illustrative assumptions only: if each of 5,000 students makes one request every 5 seconds including response and think time, offered traffic is approximately 1,000 RPS. With a stable mean response time of 0.2 seconds, mean in-flight requests would be approximately `1,000 × 0.2 = 200` by Little's Law. The values are a hypothetical calculation, not a workload claim or sizing recommendation. Use mean latency, not p95, in that calculation; instability and changing queues invalidate this steady-state estimate.

k6 provides active VUs and request metrics, but the journey design determines traffic. A closed VU model lowers its arrival rate when responses slow; an arrival-rate model controls offered iterations separately. Use both for different questions and report achieved load rather than assuming that VU count proves throughput. See [k6 Open and closed models](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/open-vs-closed/).

## Latency and outcome measurement

The primary enrollment timing starts immediately before issuing the HTTP request and ends when the full response is received or the request fails/times out. It includes client connection/pool waiting, network transfer, proxy/backend waiting, database waits, and commit acknowledgement as observed by that generator. It excludes human think time and page rendering. Use a future custom elapsed-time Trend around the call for this scope.

Also retain `http_req_duration` and its blocked/connect/TLS/send/wait/receive components for diagnosis. Built-in `http_req_duration` does not include all connection/blocked phases, so it must not silently replace the defined elapsed-time metric. See [k6 Built-in metrics](https://grafana.com/docs/k6/latest/using-k6/metrics/reference/).

Report latency for all enrollment attempts, committed successes, expected business denials, unexpected failures, and idempotent replays. Apply the proposed enrollment percentile gates to all attempts and separately to committed successes when the fixture has sufficient successful samples; a mostly sold-out hotspot cannot establish successful-enrollment latency. Include timeouts as failures and elapsed observations, not dropped samples. For client-visible retries, separately report total logical-operation latency from the initial attempt through terminal response, including retry waits; retries must not make slow operations appear fast.

Calculate the unexpected-failure rate as failed attempts divided by all attempted target API requests in the named measurement interval. Count network errors/timeouts, all unplanned `5xx`, invalid response contracts, and unexpected statuses/domain codes. Report server `5xx` rate separately as well. Report the overall rate and critical operations separately so cheap reads cannot hide enrollment failures.

Expected business denials are only allowlisted **status-and-code combinations predicted by each fixture**, such as `409 SECTION_FULL` after a known full section. Do not blanket-exempt `4xx`. Authentication failures in a valid session workload are failures; missing eligibility data is an unavailable technical outcome. Report `429`, `503`, queue rejection and `REQUEST_IN_PROGRESS` separately; for the target workload count them as unsuccessful service in PERF-04 rather than calling them successful business decisions. A replay is not another new seat allocation.

Later k6 scripts must implement custom outcome classification, not rely solely on default HTTP status classification. Use explicit thresholds to fail a run and independent invariant validation to gate correctness. k6 checks alone do not define the test's pass/fail threshold policy; see [k6 Thresholds](https://grafana.com/docs/k6/latest/using-k6/thresholds/).

## Draft workload specification

The following is a **proposed benchmark hypothesis** to make the target reviewable. It is not a claim about observed SDU behavior. All durations, percentages, and resource values require agreement before execution.

### Mixed active-student benchmark

- Pre-provision at least 5,000 independent synthetic eligible student identities, stable student-term rows, and valid sessions. Each active VU maps to a distinct student. Authentication/session issuance is measured separately; it must not be an invisible bottleneck or an uncontrolled production-provider load.
- Draft stages: warm up for 2 minutes, ramp to 5,000 VUs over 5 minutes, hold for 10 minutes, then ramp down over 2 minutes. Gate percentiles on the designated hold interval; publish opening/ramp behavior separately.
- Proposed request mix during the hold: 40% catalog reads, 25% section/availability reads, 15% own schedule reads, 15% enrollment attempts, and 5% withdrawals. These are **request proportions**, not guaranteed proportions of iteration branches. The future scenario must record actual achieved proportions and its request dependency graph.
- Proposed think time is randomized between 2 and 8 seconds per journey action; confirm polling behavior and any parallel browser requests. Active VUs do not continuously submit enrollment with no think time unless the scenario says so.
- Publish fixture cardinalities and distribution before the test: courses, offerings, sections, capacities, meeting patterns, transcript volume, starting student credits and hot-section selection. Provide a cohort of known eligible attempts with available nonconflicting seats throughout the interval so successful allocation latency is measurable. Avoid turning the entire run into sold-out rejections. Reset/replenish fixtures only between measured runs, not through an undocumented mutation bypass.
- Report offered/completed total RPS, enrollment RPS, HTTP in-flight count, active students, achieved mix, success/denial fractions, and dataset evolution. No exact RPS target is derivable until think time, response time, and journey are agreed.

### Separate contention and failure scenarios

| Scenario | Fixture and purpose | Required evidence |
| --- | --- | --- |
| Last seats | 2,000 distinct eligible students submit one enrollment each for an initially empty 30-seat section within a proposed 1-second arrival window. No withdrawals/admin edits. Arrival skew must be recorded; this is an opening burst, not a promise of literally simultaneous processing. | ≤ 30 active enrollments in committed state; healthy complete run gives exactly 30 distinct successes and 1,970 `SECTION_FULL` denials. Technical failures fail that allocation run, even if capacity is safe. |
| Same student races | Concurrent requests across sections test credit exhaustion, overlapping meetings and duplicate enrollment, including different keys. | CT-02 through CT-04 pass; student state remains valid across all sections. |
| Retry/withdrawal/failure | Lost commit response, rollback, same-key replay, withdrawal race, admin close, database/cache outage, timeout and deadlock. | CT-05 through CT-10 pass, with no partial writes and explicit technical failures. |
| Arrival-rate stress | Increment offered enrollment/journey arrivals independently of completion. The rate steps and stop conditions are TODOs after a baseline. | Offered versus achieved rate, dropped iterations, generator saturation, bounded queues, recovery and the first breached target. A closed-VU slowdown is not mistaken for a pass. |
| Extended stability | Longer sustained traffic after an initial passing baseline; duration is TODO. | Memory/connection/queue behavior, counter reconciliation and recovery; no numerical stability claim yet. |

The mixed-workload latency hypothesis does not automatically apply to the concentrated last-seat burst. Publish its latency separately and set an explicit burst SLO after measurement/review. Do not combine the scenarios into one percentile that hides contention. Per-run percentiles need sufficient samples; do not average percentiles from different runs.

Detailed resource/admission evolution and planned failure verification are owned by [Scaling strategy](scaling-strategy.md); transaction-level crash/replay cases are owned by [Enrollment design](enrollment-design.md). The original PERF targets and workload hypotheses remain unchanged. Phase 3 adds a [synthetic dataset and physical invariant verification plan](data-integrity.md), not test results.

## Test environment and observability

The brief specifies Intel Core i5-12400F, NVIDIA RTX 4060, 32 GB RAM and Windows 11 for intended local development/testing. No GPU acceleration is assumed for this HTTP/database workload. The execution environment used to write documents is not benchmark evidence for that host.

Before running, record Windows/WSL2/Docker versions and allocations, CPU/memory quotas for each container, PostgreSQL/version/configuration, JVM/heap, Spring Boot/JDK versions, HikariCP settings, frontend build mode, connection reuse/TLS, dataset version, commit hashes, storage characteristics, and load-generator placement. Docker Compose is planned; nothing is running now.

If k6 shares the host with the application, its CPU/memory/network consumption competes with the system under test. Measure generator resource use and scheduled/dropped iterations; repeat from a separate adequately sized host before treating generator-limited results as server capacity. Declare whether the path includes Next.js rendering or tests only the API through routing; browser rendering and login UX require separate validation.

Required diagnostic signals: route/operation/outcome latency and counts, offered/completed throughput, application queues and rejection, HikariCP active/idle/pending/acquisition time, database transactions/queries/lock waits/deadlocks, CPU/memory/GC/disk I/O, and optional cache hit/miss/fallback. Keep label cardinality bounded: no raw student IDs, idempotency keys, or section-specific URL labels.

## Valid run and report

A future result is reviewable only with the workload configuration, seed/data identity, raw metrics, measurement interval, versions/resources, response-code breakdown, successful enrollment count, invariant report, and limitations. A generator-saturated run, missing success cohort, inadequate samples, or unachieved offered workload is inconclusive rather than a capacity pass. Any observed invariant violation fails correctness regardless of latency.

TODO: approve the workload, dataset, warm/cold cache variants, repeat-run policy, sample sufficiency, timeout/retry budgets, stress stop limits, login-opening scenario, and production-like topology. Reconcile PERF-01 through PERF-06 with NFR-01/NFR-04 after measurements. Do not state that the system supports 5,000 students until valid evidence demonstrates the agreed workload on a named environment.
