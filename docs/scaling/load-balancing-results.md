# Phase 9 — HAProxy and backend instances

## Controlled configuration

HAProxy 3.2.26 is built from a checksum-pinned upstream release. The fixed-budget comparison places HAProxy, Spring Boot processes and PostgreSQL in the same 1.5-core, 2 GiB Sandbox. The private app and health ports bind loopback. The public Modal tunnel reaches only the existing authenticated, allowlisted benchmark gateway. HAProxy uses round-robin HTTP balancing, no session affinity, no request retries and readiness checks on the private management listener. Graceful Spring shutdown is enabled. The launcher signals HAProxy to stop accepting and drain before terminating the applications. Production fault domains and cross-host latency are outside this experiment. [HAProxy manual](https://docs.haproxy.org/3.2/configuration.html).

The one-instance case has Hikari maximum and minimum idle 16. The two-instance case uses 8 + 8. Three instances, when qualified, use 6 + 5 + 5. Operational clients remain additional to that total. Total Java heap is capped at 256 MiB in the fixed comparison. JVM overhead grows with instance count inside the same Sandbox limit. One application applies Flyway directly on primary, and secondary instances disable migration at boot. The scale-out case separately uses 2.5 cores, 3 GiB and 512 MiB of aggregate Java heap. Its result cannot isolate an architectural effect.

## Functional evidence

The test used two actual Spring Boot processes behind local HAProxy. A login and an authenticated request were observed on different backends without a new session. CSRF-protected enrollment committed on one backend, and the other backend returned the same enrollment and the replay marker for the original key. The browser suite also covered response loss, permissions, the final seat and session expiry. In a separate fault test the secondary process was suspended, HAProxy excluded it, and the existing session continued on the primary process. After the secondary resumed, it served the same authenticated session again. The regression suite exercises database-backed cross-instance idempotency and concurrent allocation independently.

## Mixed 200-VU results

The tests used the same application JAR, authenticated TLS tunnel, 500-student fixture, 120-second measurement, and 16 aggregate Hikari connections. The paced workload offers approximately 240 HTTP requests per second at this tier. Interpret latency and gate results at that offered workload.

| Architecture | Run ID | Gate | HTTP RPS | p95 / p99 ms | Successful enroll / withdraw per s | Warm-up / steady errors | Hikari pending / PG connections, peaks |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 backend, direct, fixed | `modal-20261108T110143Z-1a07f6` | Passed | 239.81 | 26.63 / 41.44 | 39.97 / 39.96 | 0 / 0 | 0 / 16 |
| 2 backends, direct, fixed | `modal-20261108T110638Z-66e1cd` | Failed | 239.83 | 46.67 / 223.31 | 39.97 / 39.97 | 1 / 0 | 0 / 16 |
| 2 backends, direct, scale-out | `modal-20261108T111140Z-97ce37` | Passed | 239.81 | 27.32 / 38.93 | 39.98 / 39.96 | 0 / 0 | 0 / 16 |
| 2 backends, transaction pool, fixed | `modal-20261108T111632Z-0b6f84` | Failed | No Data | 2,948.22 / 3,643.85 (partial) | No Data | 309 / 71 (partial) | 65 / 16 |

The fixed two-backend run observed 82 cross-instance sessions during preparation. One enrollment received a backend 503 during warm-up and succeeded with the original key on retry. All 14,268 durable operations reconciled. The transaction mean rose from 3.65 to 6.24 ms and mean section-lock hold from 1.60 to 2.30 ms. The sampler observed one transaction-ID lock wait, one WAL write wait and no Hikari queue. Aggregate Java peak process RSS rose from 435.12 to 767.84 MiB, with aggregate Java peak CPU rising from 0.25 to 0.56 core equivalents. Primary PostgreSQL used 17.88 and 18.83 CPU-s in the respective measurement windows. The cause of the 503 remains undetermined. The larger-budget control recovered the gate and had similar paced RPS and latency to one backend. All three runs passed the SQL invariants and verified cleanup.

The first direct controls exposed a launcher error. HAProxy dropped privileges before opening a root-only generated configuration. The verified runs use a readable configuration containing only loopback addresses. The initial runs were retained, including a stopped scale-out attempt. They contain no qualified workload measurements.

The transaction-pool combination authenticated 200 students and observed 92 cross-instance sessions. It was stopped about 12 seconds into the measurement. The three in-window pooler samples had zero waiting clients and up to 14 active servers, while Hikari pending reached 65 and mean section-lock hold reached 47.51 ms. The traced failures include application 503s, timeouts and a separate upstream 502. In this run, the operation ledger contained one more timed failure than the aggregate window counted. The full reconciliation gate failed. All 3,432 durable outcomes and final enrollment state reconciled, recovery resolved all 69 pending journeys, and the eight structural checks passed. The interrupted run does not establish an isolated cause or a throughput result.

**Decision.** Keep the single direct backend as the default. Retain HAProxy as an opt-in test configuration. Revert the two-backend fixed profile, including the PgBouncer combination, as a deployment candidate. The separate scale-out pass at 200 VUs establishes a working configuration with a larger budget. A third backend and higher HAProxy VU levels remain Not Tested.

Stage 2 metered USD 0.20273852 before credits across seven runs. All seven verified cleanup.

## Reproduction

Build the Linux binary for Modal.

```sh
dir=infrastructure/.local/scaling/haproxy-amd64
mkdir -p "$dir"
docker build --platform linux/amd64 --target artifact --output "type=local,dest=$dir" infrastructure/scaling/load-balancing
install -m 755 "$dir/haproxy" infrastructure/.local/modal/assets/haproxy-linux-amd64
```

For the loopback demo on macOS, build the same checksum-verified source with `make TARGET=osx` and install the executable at `infrastructure/.local/scaling/haproxy-native`. Start the isolated stack with `SDU_DEMO_BACKEND_INSTANCES=2 bash infrastructure/student-demo.sh --with-portal`.

The Phase 9 Modal runner accepts `--database-path direct --backend-instances 1` or `2` for the fixed comparison. Use `--scale-out` only for the separate two-backend larger-budget run. Start at 200 VUs and use the same workload, limits and SQL gates from the [PgBouncer run](pgbouncer-results.md).
