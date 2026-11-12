# Resource Scaling Benchmark on Modal

## Baseline and comparison

The successful shared-Sandbox baseline is `modal-20261108T110143Z-1a07f6`. It passed mixed at 200 VUs with 239.81 HTTP RPS, 26.63/41.44 ms p95/p99 and zero unexpected outcomes. Its JAR is preserved as `infrastructure/.local/scaling/phase9-balancing.jar`, SHA-256 `39ffe51faa9558239167af7dd227aac3e361a11fe5e1f5f03d02b4331ffdbc42`. The source Release Candidate remains in `sdu-rc-phase8`. The prior catalog and standby runs are complete. Their results describe the shared topology. [Phase 9 comparison](architecture-comparison.md).

| Profile | Application Sandboxes | Aggregate application CPU / RAM | JVM max heap per node | Runtime JDBC connections |
| --- | --- | --- | --- | --- |
| A | One at 4 CPU / 8 GiB | 4 CPU / 8 GiB | 4 GiB | 16 |
| B | Two at 2 CPU / 4 GiB each | 4 CPU / 8 GiB | 2 GiB | 8 + 8 |
| C | Two at 4 CPU / 8 GiB each | 8 CPU / 16 GiB | 4 GiB | 8 + 8 |

Each case has a separate PostgreSQL 18.6 primary at 4 CPU / 8 GiB, an edge Sandbox at 1 CPU / 1 GiB, and a single k6 Function at 2 CPU / 2 GiB for the first two tiers. Modal CPU and memory request equal limit. Reported Linux cgroup quota and visible processor count are recorded separately from the SDK allocation. Spring Boot uses 4 or 2 active processors according to the profile. PostgreSQL uses 1 GiB shared buffers, 4 GiB effective cache size, max connections 60 and `commit_delay=0`, with `fsync`, `synchronous_commit`, and `full_page_writes` on. Operational database clients are additional to the fixed runtime pool. Profile C is the larger-budget scale-out comparison.

All profiles use a fresh 5,000-student synthetic fixture with 20 sections of 252 seats. The 200-VU tests use 200 prepared accounts. The mixed workload ramps from 1 to target over 60 seconds, holds the target for 30 seconds, then measures for 120 seconds. The hot comparison uses one section and the same configured capacity. The distributed comparison hashes distinct actor indices over 20 sections. PgBouncer metrics are Not Tested in the isolated direct-JDBC comparison. The shared PgBouncer measurements remain available separately. [PgBouncer results](pgbouncer-results.md).

## Network and evidence

All processes use the fixed `us-east` region. The edge authenticates k6 using a per-run capability. HAProxy uses a separate capability on certificate-validated TLS connections to allowlisted node gateways. PostgreSQL listens on loopback and the dedicated database bridge requires its own capability. JDBC also validates the PostgreSQL certificate inside the tunnel. Only the first backend runs Flyway and seeds the fresh primary. HAProxy uses round robin without affinity and retries are disabled. Independent readiness checks cover each instance, and the Function checks the full route and management endpoint isolation. Inter-Sandbox TLS and WebSocket hops are part of this experiment. [Modal Sandbox networking](https://modal.com/docs/guide/sandbox-networking) · [Modal resource limits](https://modal.com/docs/guide/resources).

The report preserves observed HTTP, application and database counts, every durable mixed outcome and retry disposition, plus node-specific JVM RSS, GC, Hikari and throttling when the source is available. Missing telemetry stays No Data. PostgreSQL process RSS is a sum and can count shared pages more than once. The SQL and full HTTP gates are separate. A pass requires a complete window, no warm-up or steady unexpected responses, no dropped/interrupted iterations, latency thresholds, generator headroom, matched outcomes, zero residual active enrollments, and verified cleanup.

## Operator commands

Use the pinned Modal Python environment and prepared assets. Substitute `a`, `b` or `c` as the profile. Preview with `plan`, then use `run --confirm` under the authorized credit cap.

~~~sh
python infrastructure/modal/benchmark.py plan \
  --profile mixed --vus 200 --resource-profile a --split-database \
  --transport encrypted-tunnel --pool-size 16 --prefill-pool \
  --spread-start --ramp-seconds 60 --ramp-start-vus 1 --max-usd 1.20
~~~

Use `--hot-mixed` for the separate 200-VU hot-section comparison. Advance each architecture only after its own predecessor passed with durable reconciliation. The runner requires a fresh, idle Modal environment and verifies termination on every path. The first tier's conservative ceilings, including startup and cleanup, are USD 0.79045 for each of A and B and USD 1.00757 for C. Metered receipts are attached by App ID and run tag. [Modal pricing](https://modal.com/pricing).

## Startup evidence

Two startup attempts were retained. The first routing check tried to read a backend identifier from an HTTP 401 response through a helper that reports headers only for successful responses. The second attempt reached the edge but the operator-side TLS handshake timed out during preflight. The final readiness check originates inside the edge Sandbox, and the independent Function verifies the external TLS route before authentication and load. Both startup attempts stopped before load and verified cleanup.

## Measured results

| Profile, distributed sections | Run ID | Gate | HTTP RPS | p95 / p99 ms | Successful enroll / withdraw per s | Warm-up / steady errors |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| Shared baseline | `modal-20261108T110143Z-1a07f6` | Passed | 239.81 | 26.63 / 41.44 | 39.97 / 39.96 | 0 / 0 |
| A | `modal-20261108T113659Z-b815fa` | Failed | 140.21 | 1,958.20 / 2,183.16 | 23.38 / 23.16 | 0 / 0 |
| B | `modal-20261108T114305Z-eddec7` | Failed | 147.10 | 2,830.82 / 3,448.72 | 24.33 / 24.11 | 12 / 71 |
| C | `modal-20261108T114952Z-3a6fe2` | Failed in ramp at 93 observed VUs | No Data | No Data | No Data | 32 / Not Tested |

The gate required at least 18,240 paced iterations. A completed 11,366 and B completed 11,919. Both had zero interrupted iterations. The closed executor did not emit a dropped-iteration counter. B's 83 unexpected results were timeouts. The edge observed 106 cross-instance sessions in B and 44 in C. A and B passed structural and exact SQL checks, reconciled 9,272 and 9,566 durable outcomes, and ended with zero active enrollments.

C stopped after 27.85 seconds of ramp. The independent journal contains 33 unexpected results, one transport error in addition to the 32 aggregate timeouts, and 26 mutation starts without a completed response. All 204 durable outcomes reconciled and recovery resolved 27 pending journeys. The full reconciliation and exact HTTP/SQL gates failed. All eight structural SQL checks passed and the final snapshot had zero active enrollments. All runs verified cleanup.

## Latency breakdown

Each idle RTT is the median of 30 sequential psql `SELECT 1` timings through the same local database endpoint as Spring Boot. It measures a database request and response, including the configured transport and client/server handling. It is distinct from a TCP-only RTT. C's two node timings came from its original startup samples. Its runtime metrics cover the partial ramp. All other runtime rows use the full measurement window.

| Node | Idle DB RTT ms | Hikari acquire mean ms / pending peak | Spring proxy to transaction entry, enroll / withdraw ms | Section lock wait / hold ms | Inside transaction through completion, enroll / withdraw ms | Pooled pre-commit estimate ms | App COMMIT mean ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Shared | 0.09 | <0.01 / 0 | 0.21 / 0.20 | 0.15 / 1.60 | 3.83 / 3.06 | 3.21 | 0.24 |
| A | 8.35 | 105.14 / 64 | 120.86 / 122.68 | 10.31 / 45.56 | 161.28 / 108.33 | 127.34 | 7.53 |
| B node 1 | 7.34 | 226.78 / 72 | 322.38 / 343.65 | 8.43 / 44.08 | 152.61 / 103.16 | 120.48 | 7.28 |
| B node 2 | No Data | <0.01 / 0 | 2.46 / 2.51 | 3.76 / 15.53 | 54.38 / 36.83 | 43.10 | 2.55 |
| C node 1 | 42.98 | 296.49 / 40 | 342.93 / 369.26 | 53.52 / 257.52 | 878.93 / 614.26 | 732.44 | 42.77 |
| C node 2 | 42.74 | 198.91 / 20 | 280.95 / 213.94 | 59.93 / 256.30 | 913.55 / 612.39 | 737.79 | 42.56 |

Hikari acquisition covers all checkouts, including sessions. Proxy-to-entry also includes Spring and connection setup. Transaction timings start at method entry and end at its completion callback. The pre-commit estimate subtracts the aggregate COMMIT mean from the count-weighted enrollment and withdrawal completion time. Callback overhead and differences in the sampled cohorts limit its precision, particularly for C.

| Profile | PostgreSQL sampled peak row-lock waiters | WAL sync calls / cumulative sync ms | Mean WAL sync, µs | PostgreSQL process CPU-s / peak RSS sum MiB |
| --- | ---: | ---: | ---: | ---: |
| Shared | 0 | 38,110 / 117.066 | 3.07 | 17.88 / 171.52 |
| A | 1 | 22,328 / 25.896 | 1.16 | 25.59 / 213.98 |
| B | 2 | 23,404 / 35.776 | 1.53 | 29.98 / 360.70 |
| C, partial ramp | 0 | 743 / 0.886 | 1.19 | 1.83 / 233.61 |

WAL counters span all server activity, including session writes, and cannot assign a flush to a specific transaction. No WAL write wait appeared in the sampled active sessions for these four runs. C's PostgreSQL log captured row-lock waits outside the instantaneous snapshots. Process RSS sums can count shared pages more than once.

| Backend | CPU quota / RAM limit | Peak Java CPU core equivalents / RSS MiB | GC pauses / total ms | CPU throttling |
| --- | --- | ---: | ---: | --- |
| Shared app and DB Sandbox | 1.5 CPU / 2 GiB configured | 0.25 / 435.12 | No Data | No Data |
| A | 4 CPU / 8 GiB observed | 0.29 / 811.47 | 12 / 64 | No Data |
| B node 1 | 2 CPU / 4 GiB observed | 0.24 / 749.86 | 6 / 55 | No Data |
| B node 2 | 2 CPU / 4 GiB observed | 0.16 / 666.10 | 6 / 51 | No Data |
| C node 1, partial ramp | 4 CPU / 8 GiB observed | 0.20 / 615.20 | 1 / 9 | No Data |
| C node 2, partial ramp | 4 CPU / 8 GiB observed | 0.16 / 625.20 | 1 / 7 | No Data |

Primary quota was 4 CPU / 8 GiB and edge quota was 1 CPU / 1 GiB in A/B/C. JVM startup confirmed a 4 GiB max heap for A/C and 2 GiB on the captured B startup. k6 averaged 0.083 and 0.127 core equivalents and peaked at 202.44 and 204.63 MiB RSS for A and B. C's partial ramp averaged 0.054 core equivalents with 150.13 MiB peak k6 RSS. Samples showed CPU headroom. CPU throttling counters were unavailable.

## SQL round trips and bottleneck

A disposable PostgreSQL 18.6 instance used the exact benchmark JAR with PgJDBC 42.7.14 and the same synthetic fixture. A local loopback protocol observer counted frontend `Query` and `Sync` boundaries for seven successful authenticated POST/DELETE pairs. It saved only counts and fixed statement labels. TLS was disabled in the local observer to inspect frame boundaries. Cloud JDBC retained certificate verification. Replay, failure paths and other academic data can change the counts. [Sanitized wire observations](../../infrastructure/.local/scaling/resource-jdbc-wire-observation.json).

| Scope | Enrollment | Withdrawal |
| --- | ---: | ---: |
| Business transaction, including isolation setting, BEGIN and COMMIT | 23 | 16 |
| Total authenticated HTTP request including session and principal lookup | 30 first, 33 on each of the next six | 26 on each of seven |
| Exchanges after acquiring the section row lock | 6 | 6 |

The six exchanges under the lock are the decision clock, seat-counter write, enrollment row write, audit write, durable idempotency outcome write and COMMIT. Withdrawal writes the enrollment row before the seat counter. Six times the measured COMMIT mean is 45.20 ms in A, 43.66 and 15.27 ms on B, and 256.60 and 255.34 ms on C. Observed hold was 45.56, 44.08, 15.53, 257.52 and 256.30 ms respectively. Individual post-lock calls in C each took about 43 ms at the application. PostgreSQL's mean WAL sync was about 1.19 µs. With identical per-node quotas and similar completed transaction counts in B, the slower node also had a much larger Hikari queue.

**The bottleneck is the serial JDBC exchanges over the variable inter-Sandbox database route.** Its latency extends each transaction and section lock, which ties up JDBC connections and queues subsequent requests. The experiment did not identify the component inside the route responsible for placement-to-placement variation.

Spring Session JDBC performed one or two session-read transactions and one session save on the observed path. Each used BEGIN, SQL and COMMIT. With a separate principal lookup, these added seven or ten exchanges per request outside the business transaction. In A the dominant session read and write SQL averaged 0.031/0.042 ms of server execution over 42,395/28,221 calls including preparation and warm-up. In B the corresponding means were 0.029/0.042 ms. Session JDBC adds round trips and pool pressure even though its SQL is cheap.

HAProxy round robin preserved sessions across nodes, and B recorded roughly 2,700 transaction completions per node in the sampled interval. The dedicated edge added an internal TLS hop. Its independent latency contribution was outside this comparison. A's HTTP p95 was 1,958.20 ms with 1,957.92 ms p95 waiting for the first byte and about 0.006 ms p95 client connection blocking. The idle database probes bypassed HAProxy and still showed the large delay.

## Configuration differences

| Parameter | Shared baseline | A/B/C |
| --- | --- | --- |
| JAR, PostgreSQL and client script | Same JAR, PostgreSQL 18.6 base image, k6 binary and fixture SQL | A/B/C share one workload script hash. The older script hash differs because it predates the optional catalog branch, which was inactive in this comparison. |
| Load | 200 VUs, ramp 60 s from 1, hold 30 s, measurement 120 s, minimum iteration 1.25 s | Same plan. C stopped in ramp. |
| Dataset | 500 synthetic students, 20 sections × 32 seats | 5,000 synthetic students, 20 sections × 252 seats. Same first 200 account indices and operation mix. |
| PostgreSQL | Loopback, 96 MiB shared buffers, max connections 60 | Separate PostgreSQL over authenticated TLS/WebSocket. 1 GiB shared buffers, 4 GiB effective cache explicitly set, max connections 60. |
| Durability and locks | `commit_delay=0`, fsync, synchronous commit and full page writes enabled, same lock order | Same |
| Hikari and sessions | One prefilled pool of 16, database-backed sessions | Prefilled 16 in A or 8 + 8 in B/C, same session implementation |
| Timeouts | Hikari acquisition 3 s/validation 1 s, PgJDBC connect 3 s/socket 20 s, k6 request 4 s, lock 2 s, statement 5 s, transaction 10 s, operation deadline 15 s | Same. The DB bridge adds a TLS connection and forwarding behavior. |
| HTTP topology | HAProxy loopback upstream, maxconn 160, server connection close | Dedicated edge and verified TLS to each node, maxconn 2048 and safe reuse. Both use round robin and disable HAProxy retries. |
| Compute | Shared app, edge and PostgreSQL 1.5 CPU / 2 GiB. k6 1 CPU / 1 GiB. | Dedicated edge, database and backend budgets. k6 2 CPU / 2 GiB at 200 VUs. |

The shared versus isolated comparison changes several variables. A/B/C use matched logical workload and database settings. Actual database RTT differed between placements even in the same configured region. This limits attribution to CPU/RAM or instance count alone.

## Minimum corrective plan and cost

1. Keep the qualified shared baseline for the demonstration. For the split architecture, use a direct private PostgreSQL link with verified TLS and same-zone placement where supported. Gate the next comparison on measured per-node idle database RTT before starting load.
2. Re-run a matched 200-VU control using the same fixture, PostgreSQL settings, Hikari budget and timeouts on both routes. Change only the route to quantify its effect on client COMMIT, section lock hold, Hikari pending and the full HTTP/SQL gate.
3. Revisit the duplicate session lookup and serial SQL in the lock scope only if they remain material after the route fix. Preserve the lock order, durability, atomic audit and durable idempotency. Require the same correctness and HTTP gates before escalation.

After A/B/C the already-started separate 200-VU hotspot was interrupted. It has no qualified performance result. All isolated 500–5,000 VU tiers remain Not Tested. The shared 200-VU pass remains the mixed qualification.

A/B/C metered USD 0.92179692 before credits. Including the two setup attempts and the interrupted hotspot, the resource experiment metered USD 1.18080933. All six Apps stopped, and the final scoped inventory reported zero active containers or Sandboxes. The workspace billed amount was USD 0 at verification.
