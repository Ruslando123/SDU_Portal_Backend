# Phase 9 — PostgreSQL streaming replica

## Configuration and routing

PostgreSQL 18.6 primary and standby use a true physical base backup with streaming WAL, a private replication passfile and a separate read-only role. The primary retains WAL for bounded reconnects. The standby starts in recovery and uses hot standby. The design contains no promotion command or automatic failover. The experimental replica serves only published catalog metadata when `available=false`; authenticated catalog browsing with the seat filter, student/session data, mutations, replay, audit, registration rules and read-your-writes remain on primary. The reader has SELECT on a restricted view and a four-connection limit. It rejects connections to a server outside recovery. There is no fallback to primary for a failed replica query. [PostgreSQL streaming replication](https://www.postgresql.org/docs/18/warm-standby.html).

## Functional evidence

The local Docker verification used actual `pg_basebackup`, confirmed WAL streaming, paused replay to observe a stale read, resumed and verified catch-up. The restricted reader could neither update the view on standby nor read a private primary table. The standby resumed after a restart. During primary shutdown it retained readable data and remained in recovery. It caught up after the original primary restarted. No database port was published by this physical verification.

A separate isolated browser run used the real backend and standby. After standby shutdown, the catalog-only request returned 503, the available-seats query remained available, and enrollment, immediate read-your-writes, and withdrawal succeeded. Catalog reads resumed when the standby restarted. The JVM suite also verified that pointing the replica configuration at primary fails closed for the replica path while primary reads and writes remain available. Authentication remains a primary dependency. Primary failure is therefore expected to prevent authenticated portal requests even if the standby still has catalog data.

## Reproduction

The local isolated browser configuration uses `SDU_DEMO_CATALOG_REPLICA=1 bash infrastructure/student-demo.sh --with-portal`. For physical database verification, run `infrastructure/scaling/replication/verify.sh` inside the pinned PostgreSQL image with networking disabled and freshly generated credentials in the three environment variables shown in the script. No data or listener persists beyond the disposable container.

The cloud comparison uses one catalog-only read in four iterations and the same other three reads and mutation cadence for both sources. It records successful business throughput and SQL proof of which server actually executed the catalog query. Compare lag and resource use at the same aggregate Sandbox limits. Results from the earlier sections-read profile are a different workload.

## Mixed 200-VU results

Both cases used 1.5 physical cores and 2 GiB for the application and all database processes, with an application pool of 16 on primary and up to four additional read-only connections on the optional standby. The measurement was 120 seconds after the gradual warm-up. The application JAR, fixture and k6 workload were identical. The second run used a rebuilt gateway with additional local GC metric projections and an updated launcher with equivalent default JVM flags. Treat the latency difference as observational.

| Catalog source | Run ID | Gate | HTTP RPS | p95 / p99 ms | Successful enroll / withdraw per s | Warm-up / steady errors | Primary / standby catalog calls |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| Primary | `modal-20261108T111949Z-2a4651` | Failed | 210.59 | 1,607.27 / 3,109.11 | 34.53 / 34.41 | 229 / 319 | 4,068 / No Data |
| Standby | `modal-20261108T112526Z-2ae6dd` | Failed | 220.73 | 1,156.05 / 2,545.37 | 36.39 / 36.35 | 1 / 100 | 0 / 4,217 |

SQL call deltas cover the interval between the first and last in-window samples. On standby, the peak sampled replay distance was 12,576 bytes and the peak non-null replay lag was 53.81 ms. Peak Hikari pending was 64 / 65, peak primary runtime connections 16 / 16, sampled PostgreSQL lock waiters 3 / 2, and mean section-lock hold 30.82 / 27.98 ms. The report's PostgreSQL process CPU samples include any standby process in the same Sandbox. Its measured CPU usage was 54.72 / 59.54 CPU-s. Both runs passed the SQL invariants and reconciled durable operations and the final enrollment state. Both verified cleanup.

**Decision.** Keep the primary-only deployment. Retain the explicit stale catalog route as an opt-in experiment. Offload was verified and both HTTP gates failed. The result does not establish improved registration capacity.

Stage 3 metered USD 0.08983923 before credits across the two cloud runs.
