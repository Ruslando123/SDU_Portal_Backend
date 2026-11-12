# Mixed 2,500-VU retry with verified loopback TLS

## Route diagnosis and change

The [original 2,500-VU attempt](mixed-2500-modal.md) stopped at 1,534 VUs during ramp. HAProxy emitted `503 sC--` after connection attempts to the second Modal ingress expired. None of 500 correlated failed requests reached the backend gateway. The instrumentation identified the failing connection phase. Modal's internal relay behavior was outside its visibility. [HAProxy termination flags](https://docs.haproxy.org/3.2/configuration.html#8.5)

The retry runs the authenticated outer gateway, HAProxy, the internal TLS gateway and Spring Boot in one Sandbox. Modal forwards only the existing HTTPS ingress port. HAProxy reaches `127.0.0.1:8443` and verifies the `localhost` certificate with a run-specific CA. The internal TLS gateway accepts a separate key and forwards to Spring Boot over loopback. The PostgreSQL 18.6 Sidecar still uses the private bridge, SCRAM, verified JDBC TLS and the same durability configuration. [Modal tunnel transport](https://modal.com/docs/guide/tunnels) · [Modal Sidecars](https://modal.com/docs/guide/sandbox-sidecars)

The aggregate allocation is the same as the first attempt. HAProxy now shares the 9 CPU / 17 GiB Sandbox quota with Spring Boot and PostgreSQL, where the earlier experiment had a separate 1 CPU / 1 GiB edge Sandbox. The JVM still uses a 4 GiB maximum heap and four active processors. HikariCP remains prefilled at 16. k6 remains at 4 CPU / 4 GiB. The backend JAR, workload, PostgreSQL fixture, 60-second ramp, 30-second warm-up hold, 120-second measurement, evidence ceilings and timeouts are retained. The run estimate with reserve was $1.40257.

## Controlled network probe

The disposable probe `sdu-loopback-http-probe-1794146466` used the same gateways and HAProxy configuration with a synthetic echo service. All 25,000 requests from 2,500 paced clients returned HTTP 200, with 25,000 normal HAProxy terminations and no connect timeouts. The probe finished in 18.34 seconds. The internal gateway accepted valid TLS and an authorized key, returned 403 without the key, rejected an untrusted CA and a mismatched hostname, and returned 400 to plaintext. The probe App stopped with zero active Sandboxes or containers.

## Mixed retry outcome

`modal-20261108T140223Z-c15cf8` failed at the post-warm-up gate. The retry reached 2,500 VUs and held that target for the full 30 seconds. The gate recorded errors, a latency SLO breach and 20,495 paced iterations against a nominal 60,000. The independent 120-second measurement did not start.

| Signal | Earlier 2,500-VU attempt | Loopback TLS retry |
|---|---:|---:|
| Observed VU peak | 1,534 | 2,500 |
| Completed target-level hold | — | 30 s |
| HTTP RPS during target-level hold | — | 1,017.43 |
| HTTP p95 / p99 during target-level hold | — | 2,988.66 / 3,100.89 ms |
| Unexpected outcomes during target-level hold | — | 1,112 |
| Interrupted iterations during warm-up | 1,502 | 0 |
| Durable outcomes reconciled | 7,304 | 28,014 |
| Active enrollments after bounded recovery | 164 | 0 |

The hold processed 30,523 HTTP attempts, with 590 HTTP 503s and 522 client timeouts. There were 446 HTTP 503s and 241 client timeouts during ramp. The first traced 503 arrived 49.812 seconds into the ramp. Acknowledged enrollment and withdrawal throughput during the hold was 161.43/s and 160.80/s, respectively. These rates count unique successful operations acknowledged inside the 30-second hold and exclude post-test recovery. The prior qualified level remains 1,000 VUs at 1,196.13 RPS.

## What the retry established about the connection failures

HAProxy recorded 2,266 `503 sC--` entries with the upstream configured as a verified local TLS listener. The second Modal ingress was absent from this route. Of the 500 correlated failure IDs, 390 were HAProxy `503 sC--` with no matching arrival at the TLS gateway. Their HAProxy total time ranged from 1,999 to 2,116 ms. The other 110 reached the internal gateway and HAProxy recorded an eventual HTTP 200 or 201. Those clients had already timed out. This separates connection-establishment failures from late HTTP responses. The second ingress is insufficient to explain the recurring failure class. The evidence cannot separate loopback TCP accept pressure, the TLS handshake and process scheduling within the Sandbox. [HAProxy termination flags](https://docs.haproxy.org/3.2/configuration.html#8.5)

During the target-level hold, HikariCP peak pending was nine, mean acquisition was 0.185 ms, mean section lock hold was 3.260 ms and mean COMMIT was 0.523 ms. The entire ramp and hold had a HikariCP pending peak of 64. PostgreSQL logged one 304.263 ms transaction lock wait in the hold, which fell between the periodic database samples. The startup JDBC `SELECT 1` p95 was 0.557 ms. k6 averaged 1.038 of 4 cores across warm-up and used 1,690.45 MiB peak RSS. These data do not establish saturation in k6 or PostgreSQL as the cause of the connection timeout. Per-process proxy throttling, socket queue depth and TLS handshake timers were unavailable.

## SQL evidence and usage

The recovery resolved all 29 pending journeys. All 28,014 durable outcomes matched acknowledged HTTP mutations after retries and recovery, with zero inconsistencies, zero missing mutation responses and zero active enrollments. PostgreSQL recorded 14,007 enrollments and 14,007 withdrawals, and the eight structural invariants passed. The exact final state passed reconciliation. The combined run gate failed because the measurement did not execute and the warm-up missed its SLO and pacing targets.

Modal attributed $0.35576892 to the mixed attempt and $0.01541882 to the network probe, totaling $0.37118774 before credits. The workspace showed $0 billed when verified. Both Apps stopped and each had zero active Sandboxes or containers. The runner performed one mixed retry and did not advance to 5,000 VUs.

Performance Lab distinguishes the verified loopback TLS route from the earlier two-ingress route. The mixed retry evidence is under `infrastructure/.local/modal/runs/modal-20261108T140223Z-c15cf8/`, and the probe receipt is under `infrastructure/.local/scaling/http-loopback-probe/`.
