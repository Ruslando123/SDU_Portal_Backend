# Mixed 2,500 VUs pool diagnosis

The series uses one application JVM on Java 21 platform threads, Redis sessions, a PostgreSQL Sidecar, the existing authenticated TLS route with loopback HAProxy, 16 CPU and 17 GiB. The generator, dataset, gradual ramp, 30-second target hold and 120-second measurement are fixed. The durable outcome ledger and SQL checks run for each attempt. The [stable 2,000-VU baseline](../../infrastructure/.local/modal/runs/modal-20261108T190216Z-f6ee77/report.json) and the [Phase 12 platform control](../../infrastructure/.local/modal/runs/modal-20261108T204513Z-7f401d/report.json) are retained. They used a direct upstream path, so the HAProxy control starts a new matched series.

| Arm | Tomcat workers | HikariCP maximum and prefilled connections |
| --- | ---: | ---: |
| HAProxy control | 80 | 24 |
| JDBC pool comparison | 80 | 32 |
| Servlet pool comparison | 120 | The same pool as its qualified 80-worker comparison |

The benchmark-only sampler checks Tomcat's actual connector executor and HikariCP's active and waiting counts every 250 ms. If Tomcat is full, all JDBC connections are borrowed and at least one request is waiting, it takes a HotSpot thread dump and records `sdu.PoolSaturation` in the ongoing JFR. Up to eight captures are spaced at least 20 seconds apart. The artifact also contains 1-second pool samples and periodic thread dumps. JFR includes socket waits, parked threads, monitor waits, execution samples, GC and JVM CPU load. [JDK diagnostic MXBean](https://docs.oracle.com/en/java/javase/21/docs/api/jdk.management/com/sun/management/HotSpotDiagnosticMXBean.html)

Join the dump timestamp to the nearest PostgreSQL and HTTP sampler timestamps and report the offset. A dump can contain workers that became idle between the trigger and capture. PostgreSQL samples expose wait events, blocking steps and transaction age. HAProxy reports its queue and connection counts. CPU pressure and throttling fields remain absent when the Sandbox does not expose them. The 120-second counters determine throughput, percentiles and SQL correctness separately from individual snapshots.

The command below plans the control from the environment set up in the [Modal deployment guide](../modal-deployment.md). Use `run` with `--confirm` after a successful workspace budget check. Change only `--pool-size 24` to `--pool-size 32` for the second arm. Once that comparison is recorded, change only `--tomcat-threads 80` to `--tomcat-threads 120` from the safe JDBC control. The gate requires a completed 120-second control, matching artifacts, verified SQL state for the selected pool and cleanup. A failed HTTP gate remains visible as a failed run.

For future runs in this pool diagnosis, a warm-up with 90% to under 95% of scheduled iterations can open the diagnostic window only if every other warm-up check passes. The failed warm-up still disqualifies the result. The archived control below ran before this diagnostic policy was added.

```bash
python infrastructure/modal/benchmark.py plan --profile mixed --vus 2500 \
  --resource-profile a --private-db-sidecar --http-experiment direct \
  --transport encrypted-tunnel --pool-size 24 --prefill-pool --split-database \
  --ramp-seconds 60 --ramp-start-vus 1 --spread-start --repeat 1 \
  --session-storage redis --sidecar-cpu-cores 16 --jvm-threads platform \
  --hardware ha-one --tomcat-threads 80 --max-usd 2.10
```

## Results from November 9, 2026

The HAProxy control reached 2,500 VUs for the entire 30-second warm-up hold. It started 56,855 of 60,000 scheduled iterations (94.76%), 145 short of the 95% threshold. The runner stopped before the 120-second window, and the report remains failed. The two comparison arms have no measurements. The next full attempt was estimated at $1.90 before credits. At the final budget check, the workspace had $22.11 metered and $1.88 reserved for unsettled usage. Adding another attempt would put the conservative projection at $25.89 against a $25.00 limit. [Control report](../../infrastructure/.local/modal/runs/modal-20261109T080219Z-38cceb/report.json)

| Measurement | 2,500-VU warm-up hold, 80 Tomcat / 24 HikariCP |
| --- | ---: |
| HTTP requests per second | 2,776.37 |
| HTTP p95 / p99 | 756.59 / 860.23 ms |
| Successful enrollments per second | 459.93 |
| Successful withdrawals per second | 450.33 |
| Unexpected responses / client timeouts / interrupted iterations | 0 / 0 / 0 |
| Peak sampled HikariCP pending during the hold | 50 |
| Reconciled durable outcomes, entire attempt | 58,634 |
| Structural SQL invariant violations / active enrollments after recovery | 0 / 0 |

| UTC dump trigger | Tomcat busy | JDBC active | HikariCP pending at trigger | Tomcat stacks waiting for checkout | `Accounts.valid` | Transaction begin | Nearest PG sample after trigger |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 08:06:56.264 | 80 | 24 | 48 | 44 | 35 | 9 | 268 ms |
| 08:07:16.762 | 80 | 24 | 40 | 37 | 30 | 7 | 362 ms |

The JFR markers and dumps show that checkout queued most often in the account validation path. PostgreSQL spent 0.010 ms on average executing `account_validation` over 180,467 calls. During ramp and hold, mean measured checkout was 2.89 ms and mean connection usage was 2.55 ms. Mean transaction duration was 11.76 ms for enrollment and 8.21 ms for withdrawal, and mean JDBC commit took 0.63 ms. Section lock wait and hold averaged 0.93 ms and 3.67 ms respectively. The nearest PostgreSQL snapshots captured no blocked statements. A separate sample at 08:06:59.699 UTC recorded `section_lock` waiting 283.87 ms behind a transaction in `audit_write` with a transaction age of about 290.34 ms. The sampler observed three `Lock:transactionid` waits and one `IO:WalWrite` wait overall. The sampling interval leaves shorter waits between snapshots unobserved. [JFR and dumps](../../infrastructure/.local/modal/runs/modal-20261109T080219Z-38cceb/jvm-evidence-1.tar.gz), [PostgreSQL samples](../../infrastructure/.local/modal/runs/modal-20261109T080219Z-38cceb/pg-samples.tsv), [Statement timings](../../infrastructure/.local/modal/runs/modal-20261109T080219Z-38cceb/pg-stat-statements.csv)

HAProxy reported no server queue at either nearest sample. Java used up to 3.68 sampled CPU cores. JFR recorded 617 ms of GC pauses across warm-up, including a 263.2 ms pause beginning 988 ms after the second saturation trigger. CPU pressure and cgroup throttling were unavailable. The evidence locates a queue in JDBC checkout and identifies its most common caller, but does not establish which change would raise sustainable throughput. The first application experiment should combine the account validity check with an existing database read for one high-traffic endpoint while retaining the authoritative enabled and authentication-version checks on every request. The experiment should measure checkout count, connection hold time and successful business throughput on the same 80/24 configuration. [HTTP samples](../../infrastructure/.local/modal/runs/modal-20261109T080219Z-38cceb/http-samples.jsonl)

Modal attributed $0.48150451 before credits to the attempt. The benchmark App stopped, no active Sandbox or container remained, and the temporary evidence Volume was removed. [Billing and cleanup](../../infrastructure/.local/modal/runs/modal-20261109T080219Z-38cceb/report.json)
