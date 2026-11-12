# Mixed 1,000-VU follow-up on Modal

## HTTP 503 attribution

The earlier [mixed run](mixed-1000-modal.md) recorded HTTP 503 responses at the authenticated edge. Its HAProxy process had no access log. The follow-up reproduced the failures using the same Modal region, HAProxy binary, HTTP tunnel and authenticated Go gateways. A local echo service answered the requests behind the backend gateway. The probe made 12,000 read-only requests from 1,000 paced clients. It used no application sessions or database. Each disposable probe had a 180-second resource timeout.

| Probe | HTTP 200 | HTTP 503 | No HTTP response | Wall time |
|---|---:|---:|---:|---:|
| `http-reuse safe` with correlated hop logs | 9,727 | 2,038 | 235 | 27.81 s |
| `http-reuse always` with correlated hop logs | 12,000 | 0 | 0 | 15.06 s |

Every client-observed 503 in the correlated probe matched a HAProxy `sC--` entry. HAProxy recorded 3,748 connection timeouts across 3,096 distinct trace IDs, including requests with no client HTTP result and repeated proxy attempts with the same trace. Every timeout had connect and response timing fields of `-1`. For 300 sampled client 503s, the outer gateway accepted the request and recorded the HAProxy response, and the backend gateway saw zero matching arrivals. The HTTP 503 originated in HAProxy after the connection deadline to the backend's Modal TLS ingress expired. These observations locate the failure to that connection phase. The probe had no visibility into internal Modal relay behavior. [HAProxy 3.2 termination flags](https://docs.haproxy.org/3.2/configuration.html#8.5) · [Modal tunnel transport](https://modal.com/docs/guide/tunnels)

## Scoped correction and gate

The private Sidecar 1,000-VU profile allows HAProxy to reuse idle upstream TLS connections on the first request of a client session. The paired probe held the rest of the route and proxy configuration constant. The benchmark retains verified SNI/TLS, both gateway authentication checks, the original 2-second connect timeout and zero HAProxy retries. HAProxy logs correlation ID, status, termination state and connection timers. The report keeps aggregate counts and correlates up to 500 failed traces with the backend gateway. [HAProxy 3.2 connection reuse](https://docs.haproxy.org/3.2/configuration.html#http-reuse)

The worker now evaluates the completed 30-second hold at 1,000 VUs before starting measurement. It requires a complete window, zero unexpected outcomes, no interrupted iterations, the existing latency thresholds and pacing, and accounted-for recovery. A failed gate retains the warm-up, SQL, reconciliation and cleanup evidence with stage `warmup-gate`.

The mixed retry retains the previous Spring Boot binary, workload script, fixture, 16-connection HikariCP pool, PostgreSQL Sidecar durability settings, resource limits, 60-second ramp and planned 120-second measurement. The only traffic handling change is the HAProxy upstream connection reuse policy. The diagnostic logs and the warm-up gate are harness changes.

## Mixed retry result

`modal-20261108T133426Z-3e76e9` passed. The post-warm-up gate observed 1,000 VUs for 30 seconds, with zero unexpected outcomes, 1,198.00 RPS and p95/p99 of 59.41/83.42 ms. The independent measurement then completed the full 120 seconds at 1,000 VUs. HAProxy recorded 215,458 correlated responses across the ramp, hold and measurement, all HTTP 200 or 201. There were no HAProxy connect timeouts.

| Metric | Passed 500 VUs | Earlier 1,000 VUs | Retry at 1,000 VUs |
|---|---:|---:|---:|
| Measurement | 120 s | 5.15 s before abort | 120 s |
| HTTP requests | 71,915 | 2,150 | 143,535 |
| Qualified HTTP RPS | 599.29 | — | 1,196.13 |
| HTTP p95 / p99 | 46.60 / 75.60 ms | 2,296.60 / 2,400.77 ms | 170.40 / 554.13 ms |
| Unexpected outcomes in k6 measurement | 0 | 131 | 0 |
| Interrupted iterations | 0 | 1,000 | 0 |
| Acknowledged enrollments / withdrawals per second | 99.87 / 99.85 | 42.68 / 25.80 in the partial window | 199.29 / 199.25 |
| Matched durable outcomes | 35,982 | 14,890 | 71,818 |
| Active enrollments after reconciliation | 0 | 174 | 0 |

The retry recorded 95,716 paced iterations against a nominal 96,000. `dropped_iterations` is unavailable for the closed-model executor. k6 used 0.980 of 2 CPU on average during measurement and peaked at 801.38 MiB RSS, with 260.06 MiB peak wrapper RSS against the 2 GiB container limit. Java peaked at 1.57 cores and 832.55 MiB RSS. PostgreSQL process RSS summed to at most 166.48 MiB across sampled processes. Cgroup throttling was unavailable.

At 1,000 VUs, HikariCP reached 16 active and 64 pending, with a 0.670 ms mean acquisition time. PostgreSQL peaked at three lock waiters, with a maximum observed wait of 2.66 ms. Mean section lock wait and hold were 0.881 and 2.484 ms. Mean application COMMIT was 0.387 ms. The startup JDBC `SELECT 1` p50/p95/p99 was 0.343/0.430/0.483 ms. The higher tail latency at 1,000 VUs remains visible even though the HTTP and SQL gates passed.

All 71,818 durable outcomes matched acknowledged HTTP mutations and audit entries. Reconciliation found zero inconsistencies, missing responses, retries or active enrollments. The live safety watcher completed 16 snapshots, and the final exact-state gate and all eight structural SQL invariants passed.

## Usage and retained evidence

Modal attributed $0.39168581 to the mixed retry and $0.05496028 to the four short HTTP diagnostic probes, for $0.44664609 before credits. The workspace summary showed $0 billed at verification time. The benchmark and diagnostic Apps were stopped, the benchmark had zero active Sandboxes, and the Modal environment had zero active containers. The runner performed one mixed retry and did not advance to 2,500 VUs.

The retry's `report.json`, `http-hop-evidence.json`, `invariants.json` and `operation-reconciliation.json` are retained under `infrastructure/.local/modal/runs/modal-20261108T133426Z-3e76e9/`. Correlated probe outputs are under `infrastructure/.local/scaling/http-503-probes/`. The Performance Lab Cloud page shows the retry and retains the earlier failed run separately.
