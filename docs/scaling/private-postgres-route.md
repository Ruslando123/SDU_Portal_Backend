# Private PostgreSQL route on Modal

## Supported route

The pinned Modal Python SDK is 1.6.3. Its public `Sandbox.create()` does not accept `i6pn`. The SDK's own documented V2 experimental creation method exposes `i6pn=True` and requires placement on the same routable network. The official Cluster Networking guide documents the Function API, and the official Go Sandbox reference independently describes the Sandbox capability and placement constraint. A bounded CPU-only Sandbox probe requested the SDK's precise `us-east-1` example. Modal rejected that region under the workspace's effective plan entitlements before starting a Sandbox. The earlier broader `us-east` cross-Sandbox attempt resolved IPv6 addresses and timed out. The benchmark does not rely on unqualified cross-Sandbox placement. [Cluster networking](https://modal.com/docs/guide/private-networking) · [Python Sandbox reference](https://modal.com/docs/sdk/py/latest/Sandbox) · [Go Sandbox reference](https://modal.com/docs/sdk/go/latest/Sandbox) · [Region selection](https://modal.com/docs/guide/region-selection).

The documented Modal Sandbox Sidecar is the alternative. In `us-east`, a bounded TCP probe connected from `main` to its `db` Sidecar with zero forwarded ports. The first connection and read took 12.786 ms. The next four took 0.986, 0.710, 0.559 and 0.565 ms. These are TCP connection-plus-read timings, separate from JDBC. A second address probe verified bridge name resolution from both containers. Both probe Apps stopped with zero active resources. [Modal Sandbox Sidecars](https://modal.com/docs/guide/sandbox-sidecars).

PostgreSQL lives in a separate container on the same host as Spring Boot. It binds loopback and its private bridge address. A separate TCP probe confirmed that the bridge rewrites the backend source to the Sidecar's private gateway. PostgreSQL logs confirmed the same peer. The HBA rule allows that exact gateway address with a /32 mask for the two benchmark roles on the benchmark database. The startup code derives the address from the Sidecar routing table and verifies it is within RFC 1918 space. Connections require TLS and SCRAM. The application verifies the DNS name `db` and an ephemeral run-specific CA with `sslmode=verify-full`. The Sidecar has an empty egress allowlist and no forwarded port. The benchmark verifies a successful TLS session and rejects plaintext, an incorrect server hostname and an incorrect password before starting k6. The pre-existing authenticated HAProxy edge carries only HTTP benchmark traffic.

## Matched comparison

The historical reference is profile A `modal-20261108T113659Z-b815fa`. The experiment retains its one backend, JAR, driver, k6 binary and workload, 5,000-student fixture, 20 sections with 252 seats each, prefilled Hikari pool of 16, 4 GiB JVM max heap, PostgreSQL 18.6, 1 GiB shared buffers, 4 GiB effective cache, 60 maximum database connections, and `commit_delay=0`. PostgreSQL keeps `fsync`, `synchronous_commit` and `full_page_writes` enabled. The HTTP ramp, hold, measurement windows and thresholds remain those of profile A.

Profile A allocated 4 CPU and 8 GiB to each of the two Sandboxes. The Sidecar alternative retains the total 8 CPU and 16 GiB, with a common quota shared across the two containers. Individual CPU and memory quotas differ as a consequence of the Sidecar topology. The separate HAProxy edge retains 1 CPU and 1 GiB, and k6 retains 2 CPU and 2 GiB. The primary comparison therefore matches aggregate compute and workload, with route and resource placement as explicit topology differences.

The startup psql metric is the median of 30 `SELECT 1` requests through the application route. The new probe additionally times 100 JDBC operations per operation type using the PgJDBC driver embedded in the backend JAR. `BEGIN READ ONLY` and its read-only `COMMIT` measure wire exchanges with no write flush. The application COMMIT timer under mixed load covers the write transaction completion. PostgreSQL WAL counters remain separate. Historical reports retain their original fields, including the original psql median. Historical JDBC percentiles that were never collected remain No Data.

The first two full-size provisioning attempts stopped before k6 because the application-facing hostname resolved correctly but PostgreSQL saw the bridge gateway as the source. The second attempt captured the rejected peer in PostgreSQL logs. A dedicated TCP probe verified that it matched the Sidecar default gateway. The final startup admitted only that gateway, kept SCRAM and TLS mandatory, and then passed all negative checks.

| Startup JDBC operation, 100 samples each | p50, ms | p95, ms | p99, ms |
| --- | ---: | ---: | ---: |
| SELECT 1 | 0.442203 | 0.591617 | 0.636552 |
| BEGIN READ ONLY | 0.360779 | 0.491448 | 0.639207 |
| Read-only COMMIT | 0.372274 | 0.484018 | 0.542303 |

## Mixed 200-VU result

The Sidecar run was `modal-20261108T123753Z-70687c`. The full 120-second window observed 200 VUs, 28,775 HTTP requests, 19,187 started paced iterations and zero interrupted iterations. Warm-up and measurement both recorded zero unexpected outcomes. It reconciled all 14,388 durable operations and the final enrollment state, passed the SQL invariants and verified cleanup. The earlier shared Sandbox column is a locality reference and used a different fixture and resource budget.

| Metric | Shared Sandbox reference | Historical A, separate Sandboxes | Private PostgreSQL Sidecar |
| --- | ---: | ---: | ---: |
| Startup psql SELECT 1 median / maximum, ms | 0.0915 / 0.561 | 8.3475 / 8.635 | 0.274 / 0.846 |
| Spring transaction entry, enroll / withdraw mean, ms | 0.208 / 0.202 | 120.856 / 122.679 | 0.335 / 0.334 |
| Application COMMIT mean, ms | 0.235 | 7.534 | 0.419 |
| Section lock wait / hold mean, ms | 0.146 / 1.596 | 10.306 / 45.559 | 0.340 / 2.660 |
| Hikari pending peak | 0 | 64 | 0 |
| Hikari acquisition mean, ms | 0.000213 | 105.137 | 0.000187 |
| HTTP RPS | 239.81 | 140.21 | 239.79 |
| HTTP p95 / p99, ms | 26.63 / 41.44 | 1,958.20 / 2,183.16 | 31.63 / 37.87 |
| HTTP/SQL gate | Passed | Failed | Passed |

Startup psql and JDBC probe percentiles describe different clients and sample windows. Transaction entry is an application timer that includes acquiring a connection. Application COMMIT is measured through completion of real write transactions. The comparison restores qualified mixed 200-VU throughput while keeping PostgreSQL in a separate container. It establishes sub-millisecond database exchanges on Modal's same-host private Sidecar bridge.

## Mixed 500-VU qualification

The runner launched `modal-20261108T124456Z-bef7c4` after the 200-VU run passed its full gate and cleanup. Its repeated idle JDBC SELECT 1 p50/p95/p99 was 0.434610/0.576921/0.673421 ms. It reached 500 VUs, passed warm-up with zero errors and ran the full 120-second measurement. It recorded 586.69 HTTP RPS, p95 294.03 ms and p99 582.45 ms. The 1,866 in-window unexpected responses and 32 additional drain responses were gateway budget HTTP 429s in the final five-second bucket. The response byte ceiling was reached at 256 MiB. These values include rejected traffic and the gate failed.

The ledger matched all 34,857 durable outcomes and identified 25 committed mutations without an HTTP acknowledgement. The final snapshot retained 17 active enrollments. Structural SQL invariants passed and the exact final-state gate failed. The retry keeps all resources, scenario settings and connection limits. It raises only the cumulative gateway byte budget to 512 MiB for the private route at 500 VUs. The 64 KiB per-response and 142,500-request limits remain in force.

The corrected repeat `modal-20261108T125901Z-c6de7a` passed. The startup psql median was 0.3075 ms. Its JDBC SELECT 1 p50/p95/p99 was 0.430750/0.539870/0.570481 ms. The full 120-second window observed 500 VUs, 71,915 requests at 599.29 RPS, HTTP p95/p99 46.60/75.60 ms, 47,971 started paced iterations, and zero interrupted iterations. Warm-up and measurement both had zero unexpected outcomes. Acknowledged enrollments and withdrawals were 99.87/s and 99.85/s. The peak Hikari pending count was zero, mean section lock wait and hold were 0.406/2.529 ms, and application COMMIT averaged 0.403 ms.

The full gate matched all 35,982 durable outcomes and passed the SQL checks with zero active enrollments remaining. Cleanup verified zero running Sandboxes and containers. The Sidecar termination returned exit 137 after the evidence was collected. The benchmark completed at 500 VUs.

### Round-trip and session assessment

The previous wire observation counted 23 exchanges for the successful enrollment transaction, 16 for withdrawal and six within the section-lock scope. HTTP-level counts were 30–33 and 26, including session and principal reads. Route optimization leaves those counts intact. One saved database exchange has a startup median on the order of 0.4 ms on this route. Combining audit and durable outcome writes could remove one exchange from the lock scope if a single SQL statement preserved their atomicity and exactly-once semantics. Avoiding a proven duplicate session lookup could remove a BEGIN, read and COMMIT, with the observed startup medians summing to about 1.18 ms of serial wire time. These are timing estimates, not end-to-end speedups. Any session change would require proving that principal validation, authorization, refresh and retry behavior remain correct. [Previous wire measurements](resource-scaling-modal.md#sql-round-trips-and-bottleneck).

## Cost and cleanup

The three small network probes metered USD 0.00032308. The two startup attempts metered USD 0.03989027 and USD 0.04283354. The 200-VU pass metered USD 0.33175023. The first and corrected 500-VU runs metered USD 0.54927841 and USD 0.30981605. The total from Modal receipts was USD 1.27389158 before credits, and the workspace billed amount was USD 0. All eight Apps were independently verified as stopped with zero active Sandboxes or containers. The prior A/B/C reports and artifacts matched their existing checksum manifest.

## Reproduction

Use the pinned Modal SDK and JDK 21. Review the budget with `plan` before selecting `run --confirm`. The runner checks idle workspace state, spend headroom and the 200-VU predecessor gate before any 500-VU run.

```sh
python infrastructure/modal/benchmark.py plan --profile mixed --vus 200 \
  --transport encrypted-tunnel --pool-size 16 --split-database --prefill-pool \
  --spread-start --ramp-seconds 60 --ramp-start-vus 1 \
  --resource-profile a --private-db-sidecar --max-usd .90
```
