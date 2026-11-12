# Phase 6: measured HTTP performance baseline, observability and reliability

Executed November 6–7, 2026 (Asia/Almaty). Phase 6 is ready for review. The user chose to run higher load separately on another computer; this shared Mac was limited to **10 VUs**. This report does not claim support for 5,000 students, a capacity limit, or a validated production SLO.

The baseline exercises real HTTP, PostgreSQL-backed sessions, CSRF, section listing, own enrollment history/schedule, enrollment/withdrawal and durable replay. It uses a deterministic synthetic fixture (5,000 students, 250 courses, 600 sections, 1,200 required meetings). The benchmark stack, volumes and credentials are separate from the ordinary development database. No real SDU account, frontend, Redis, broker, Kubernetes, paid service, GitHub push or Phase 7 work was introduced. V1–V7 remain byte identical and database durability remains enabled.

## Measured small workloads

Percentiles below are end-to-end HTTP request duration in milliseconds, with response cohorts preserved in the raw evidence. RPS uses actual k6 duration, not wrapper/sampler duration. These profiles have different request mixes; the table is not a capacity curve.

| Run | HTTP requests | k6 seconds | Achieved RPS | p50 ms | p95 ms | p99 ms | New enrollments | Business denials |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| auth5-final | 25 | 1.01 | 24.63 | 125.61 | 633.58 | 660.39 | 0 | 0 |
| smoke10-verified | 60 | 1.12 | 53.70 | 119.72 | 519.23 | 519.89 | 10 | 0 |
| controlled-normal10-r1 | 357 | 21.25 | 16.80 | 9.64 | 30.02 | 154.44 | 31 | 0 |
| controlled-normal10-r2 | 353 | 20.98 | 16.83 | 9.87 | 30.05 | 195.76 | 30 | 0 |
| arrival10-r2 | 200 | 20.00 | 10.00 | 10.75 | 18.08 | 42.32 | 0 | 0 |
| arrival10-refreshed | 201 | 20.01 | 10.04 | 11.37 | 17.00 | 46.34 | 0 | 0 |
| spike10-awake | 645 | 32.03 | 20.13 | 7.46 | 19.93 | 24.62 | 215 | 0 |
| late-hot100-r1 | 100 | 4.78 | 20.92 | 25.32 | 80.40 | 163.92 | 30 | 70 |
| early-hot100-r1 | 100 | 4.78 | 20.92 | 28.25 | 132.27 | 172.79 | 30 | 70 |
| late-hot100-r2 | 100 | 4.80 | 20.81 | 26.71 | 114.46 | 209.67 | 30 | 70 |
| early-hot100-r2 | 100 | 4.73 | 21.12 | 25.46 | 98.15 | 199.25 | 30 | 70 |
| ladder10-syntax | 233 | 20.97 | 11.11 | 8.43 | 25.87 | 43.20 | 26 | 0 |

For the valid runs above, unexpected responses, network errors, timeouts, 429/503 and dropped iterations were **zero**. The named SECTION_FULL outcomes are business denials. The cold smoke confirmed 10 enrollments, 10 withdrawals and 10 historical replays with identical original bodies. The separate auth5 profile passed all five real login/CSRF/session/logout journeys.

The controlled normal repeats used the same academic fixture, a reset ledger, restarted identical backend and a recorded 10-student smoke warmup. At 10 VUs they delivered **16.80–16.83 HTTP RPS**, **30.02–30.05 ms overall p95**, **154.44–195.76 ms overall p99**, and **94.74–127.15 ms p95** for the successful enrollment cohort (31 and 30 samples). Three earlier continuously warmed repeats, with growing retained history, delivered 16.98–17.03 RPS, p95 18.66–21.69 ms and p99 35.39–53.46 ms. They are supplementary, not substitutes for reset-controlled repeats. Cold smoke is a short burst after startup, not an equivalent warmed mixed workload.

Both valid constant-arrival repeats offered 10 read iterations/s with exactly one HTTP request per iteration. The 200- and 201-request runs achieved 10.00 and 10.04 RPS, respectively. The small spike offered five registration journeys/s, ramped to ten for ten seconds, then returned to five. A journey normally contains three HTTP requests. It produced 645 requests and 215 successful enroll/withdraw journeys, 20.13 RPS overall and approximately **30 HTTP RPS** in the ten-second peak. Windowed p95 was 23.42 ms before, 16.12 ms at peak and 20.86 ms in the first five seconds after the step down. No sustained latency rise, sampled pool backlog or recovery delay was observed at this resolution. This is not an overload/spike capacity limit.

The observed small normal/read/hot/spike samples met the provisional enrollment-success p95 ≤1000 ms, main endpoint p99 ≤2000 ms and unexpected rate <0.5% hypotheses. Endpoint/cohort samples are in classified-results.json; short samples are descriptive, without a capacity confidence interval. The 10-VU ramping-VU smoke validates the ladder executor only. The 50/200/500/1000/2500/5000-VU ladder, 100–1000 RPS probes and 2,000-attempt HTTP hotspot were **not run on this Mac**.

[CSV](phase6-assets/baseline.csv), [derived JSON](phase6-assets/baseline.json) and [six-panel figure](phase6-assets/baseline.png) are portable. Raw evidence is local under `infrastructure/k6/results/`; it includes metrics, metadata and script revisions. The chart compares observed profiles with different mixes and must not be used to extrapolate capacity.

## Runs that did not qualify as healthy baseline

| Run | Actual finding | Treatment |
| --- | --- | --- |
| smoke10-first | k6 init failed on a misnamed maxVUs binding; no measured workload | Fixed the binding, retained the log, repeated successfully |
| auth10 | 8 successful logins and 2 HTTP 429 responses at the existing eight-verification concurrency gate; 2/44 unexpected requests (4.55% overall) | Counted as real rejections in the separate login profile; auth5 passed |
| arrival10-r1 | 197 HTTP requests, four dropped iterations; k6 wall duration 141.22 s for a 20 s schedule; resource sample gap 125.39 s | Host timing invalid; preserved and excluded from capacity/SLO conclusions |
| spike10 | 645 requests but 2,891.08 s wall duration for 32 s schedule; largest sampled gap 974.72 s | Host timing invalid; repeated with process-scoped idle-sleep prevention |
| ramp20 | 377 completions and no drops/errors, but 31.22 s wall duration for a planned 25 s read ramp | Exploratory ramp executor evidence, excluded from clean baseline |
| arrival10-awake | All 201 requests were HTTP 401 after the previously prepared sessions expired during a long idle period | Retained as a setup failure; added 25-minute freshness preflight and validated/reauthenticated sessions before arrival10-refreshed |

Swap was already about 5.5 GiB before preparation and reached about 7.6 GiB during tool/image setup. The user limited load to the small baseline. Shared host scheduling/suspend gaps and heavy pre-existing swap limit inference; these are not evidence of backend saturation. Valid small profiles did not trigger the per-run swap/RSS stop. The guarded runner records sampling gaps, holds a short macOS idle-sleep assertion while k6 runs, and stops on swap growth over 256 MiB or generator RSS budget exhaustion. At the valid 10-VU controlled normal profiles, sampled native k6 RSS was approximately 53–61 MiB and sampled backend CPU peaked at 33.21–47.64% of one core (Docker's 100% = one core). The backend container was limited to two cores. No sustained generator/pool/CPU bottleneck was observed within this deliberately small range; a separate generator is preferable for larger experiments.

## Hot section and AD-13

Each HTTP comparison used 100 distinct eligible students, five workers, 0.2-second pacing, a fresh empty 30-seat hotspot, identical backend image, the same five-user smoke warmup and independently checked SQL invariants. All four runs committed **30 ENROLLED, 70 SECTION_FULL, zero technical outcomes**, and had 30 ACTIVE rows with `enrolled_count=30`. The larger 2,000-HTTP-attempt script is prepared but not an executed result here.

| Run | Mean section hold ms | Mean section lock query ms | Mean academic preparation ms | ENROLLED p95 ms | SECTION_FULL p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| late-hot100-r1 | 4.924 | 7.836 | 3.794 | 163.05 | 36.23 |
| early-hot100-r1 | 6.180 | 9.196 | 3.171 | 169.59 | 37.94 |
| late-hot100-r2 | 4.427 | 5.717 | 5.155 | 207.84 | 45.77 |
| early-hot100-r2 | 8.008 | 8.925 | 4.606 | 198.10 | 30.66 |

Mean measured section hold was **4.68 ms late vs 7.09 ms early**, approximately **34% shorter** across these equal-sized repetitions. Section-lock query duration also fell in both pairs. The timer approximates hold from SQL return through transaction completion; it is not a server-side trace of the exact physical lock interval. Successful HTTP p95 did not consistently improve across both pairs, and pacing constrained throughput. This supports AD-13's intended reduction in time spent under section protection in this fixture, not a general latency/throughput gain or a reason to change the default writer protocol.

## Observability and SQL

Prometheus scraped the protected Micrometer endpoint and the PostgreSQL exporter; both targets were up and pg_exporter_last_scrape_error was zero after adapting pg_stat_statements read/write-time columns to PostgreSQL 18. Grafana provisioned the 19-panel dashboard and its datasource health API successfully queried Prometheus. The management listener returned 401 without its separate credential; the same credential cannot authenticate the business listener. Grafana requires login and all published ports are loopback only. No metric labels contain student/session/key/token/UUID values or raw resource URLs.

The dashboard includes HTTP/JVM/GC, enrollment outcomes, pool active/idle/pending/acquisition, lock query/hold, root attempts and PostgreSQL active connections, wait types, I/O and slow statement families. Fresh before/after snapshots, captured range series, Docker/k6 resource samples and normalized runtime SQL are preserved per run. At normal load, Hikari pending was zero in the 5-second samples; mean acquisition was approximately 0.158–0.262 ms. The forced exhaustion snapshot showed active=2/max=2/pending=1. Short transients can be missed by a periodic sampler.

Seven actual EXPLAIN ANALYZE/BUFFERS plans and [summary](phase6-assets/query-plans-summary.json) were captured on the isolated fixture; analyzed execution ranged from 0.120 to 0.752 ms, with zero shared reads in those warm probes. Details, statement-family costs, N+1 assessment and prioritized recommendations are in [bottleneck analysis](bottleneck-analysis.md).

## Real failure and recovery results

| Controlled fault | Observed behavior |
| --- | --- |
| Backend SIGKILL during audit INSERT, before COMMIT was sent | Client disconnected; zero completed outcomes after the database transaction ended. Restart + same key executed a new 201 once. |
| Backend SIGKILL while PostgreSQL was already executing COMMIT | Client disconnected; one completed outcome remained after the server finished. Same key replayed 201, proving process death is not evidence of rollback after COMMIT submission. |
| Lost HTTP response with backend alive | Proxy dropped the socket after receiving upstream 201. Same key replayed byte-identical body; no second allocation. |
| Backend SIGKILL after committed upstream response, before delivery | Proxy withheld/dropped the response. Restart + original session/key returned byte-identical replay. |
| Real clean PostgreSQL stop and restart | During outage, HTTP 500 from the session boundary. After DB restart, same key committed once; another retry replayed. |
| Actual Hikari pool exhaustion | Pool limited to two, acquisition timeout 500 ms; two roots waited on a held section lock, a third request received HTTP 500. Releasing the lock let both original requests complete. |

Controlled PostgreSQL sleep triggers make the pre-commit/commit points observable; process termination and HTTP/DB failures are real. All six cases checked DB state and invariants after recovery. The `COMMIT` case deliberately shows an ambiguous acknowledgement path, not an arbitrary assertion that the transaction must commit whenever a client disconnects. A first setup attempt retained earlier hotspot memberships; it was identified as a fixture precondition error, retained in artifacts and corrected by resetting the marked isolated ledger before running the final six cases. Full structured [reliability evidence](phase6-assets/reliability.json) is available.

The observed pre-controller HTTP 500s are a remaining reliability issue: Session JDBC can fail before the account/domain handlers that map dependency errors to typed 503. These failures are counted, did not mutate domain state incorrectly and recovered without changing keys. See the bottleneck report for a targeted later fix; this baseline did not rewrite the filter/session architecture.

## Environment and reproducibility

| Component | Observed configuration |
| --- | --- |
| Host | Apple M1, MacBookAir10,1, 8 logical CPUs, 8 GiB RAM, macOS 26.6.2 (25G83), arm64 |
| Docker | Docker Desktop, Engine 29.1.2, Compose 2.40.3; Linux arm64 VM, 8 visible CPUs, 4,109,217,792 bytes VM memory |
| Backend | Eclipse Temurin 21.0.13+9, Kotlin 2.4.21, Spring Boot 4.1.2, Security 7.1.2, Session JDBC 4.1.1 |
| JDBC / database | HikariCP 7.0.2, PGJDBC 42.7.14, PostgreSQL 18.6, Flyway 12.4.0 |
| Backend limits | 2 CPUs, 640 MiB, 128/256 MiB initial/max heap, 80 Tomcat threads, Hikari max 10/min 2, 3 s acquisition |
| PostgreSQL limits | 1.5 CPUs, 384 MiB, 96 MiB shared_buffers, 60 max connections; fsync/synchronous_commit/full_page_writes on |
| Monitoring | Prometheus 3.16.0, Grafana 13.3.0, postgres_exporter 0.20.1; 5-second scrape interval |
| Generator | Native k6 2.3.0 arm64 macOS; archive SHA-256 b2417a3038edc5fe81dc178a889237724b595c5c9cfed875822008e46e862c7d |
| Fixture | Seed 20261106; separate role/database/project/volume; synthetic BCrypt cost 10 |
| Source identity | No Git baseline commit; each run records SHA-256 source inventory, later runs record actual image ID/jar hash, and exact workload source is archived |

Use [load-testing methodology](load-testing-methodology.md), [Compose monitoring instructions](../infrastructure/monitoring/README.md) and [workload definitions](../infrastructure/k6/README.md). Prometheus/DB metrics and raw k6 results were retained locally. Stop/start instructions preserve volumes. The dedicated benchmark stack was stopped after verification; the existing development database was untouched.

## Regression and handoff

Gradle build and explicit correctnessStress passed: **83 regular tests plus one service correctness case**, zero failures, errors or skips. All previous 82 cases were retained; the service-only 2,000-student fixture used four workers on this Mac and again produced 30 ENROLLED, 1,970 SECTION_FULL, zero technical outcomes and zero invariant violations. This remains separate from the unrun 2,000-attempt HTTP workload. The new tests verify protected management authentication and early-lock benchmark semantics. Seven Python reset-safety tests also passed. Flyway histories and original migration bytes were verified.

For the other computer: preserve this baseline's source/configuration, prepare fresh sessions, separate generator and backend when possible, record Windows/WSL/Linux resources, repeat a warm small baseline, then advance one level at a time. Measure an actual enrollment-bearing workload, per-endpoint/cohort tails, failures, drops, locks/pool saturation and SQL invariants before claiming any 5,000-VU result. Phase 6 stops for review; Phase 7 has not started.
