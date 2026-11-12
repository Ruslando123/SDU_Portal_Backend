# Mixed 2,500-VU Sidecar benchmark on Modal

Run `modal-20261108T134622Z-454052` follows the [qualified 1,000-VU Sidecar benchmark](mixed-1000-http-503-followup.md). The runner required that run's post-warm-up gate, same HAProxy route and backend artifact, SQL reconciliation, final state and verified cleanup before creating the new App.

## Configuration

The experiment used one Spring Boot backend with a private PostgreSQL 18.6 Sidecar sharing 8 CPU / 16 GiB, a separate HAProxy edge with 1 CPU / 1 GiB, and one k6 Function in `us-east`. It retained HAProxy upstream TLS connection reuse, verified JDBC TLS, SCRAM, PostgreSQL durability and a prefilled HikariCP pool of 16. The backend artifact, k6 workload and fixture were unchanged. The fixture contained 5,000 synthetic accounts and 20 distributed sections with 252 seats each. The run assigned 125 students per section and started with zero active enrollments.

The k6 allocation increased from 2 CPU / 2 GiB to 4 CPU / 4 GiB. The request ceiling increased to 712,500, the cumulative response ceiling to 2,560 MiB, and the compressed PostgreSQL evidence ceiling to 24 MiB and 240,000 rows. The Sandbox and generator timeouts were 1,028 and 838 seconds. The conservative estimate, including the reserve, was $1.40257. [Modal pricing](https://modal.com/pricing)

The workload ramps from one VU over 60 seconds, holds 2,500 VUs for a 30-second warm-up, then starts a separate 120-second measurement only if the warm-up passes. The gate checks the complete target window, pacing, zero unexpected outcomes, zero interrupted iterations, p95 below one second, p99 at or below two seconds and accounted-for recovery. Qualification also requires SQL reconciliation and cleanup.

## Outcome

The run failed at the warm-up gate. Preparation authenticated all 2,500 accounts in 296.62 seconds with zero reported errors. The k6 safety threshold stopped the ramp after 36.94 seconds. The observed peak was 1,534 VUs, with 24,142 HTTP attempts, 1,898 unexpected outcomes in k6 and 1,502 interrupted iterations. The 2,500-VU hold and 120-second measurement never started, so qualified RPS and latency percentiles are unavailable. The previous complete Sidecar result remains 1,000 VUs, 1,196.13 RPS, HTTP p95/p99 of 170.40/554.13 ms and zero unexpected outcomes.

## Failure attribution

The saved trace ledger recorded 1,730 client-observed HTTP 503s, 177 client timeouts and 14 other transport interruptions. Its count of 1,921 failures exceeded the k6 counter by 23 at shutdown. The first traced 503 started 29.15 seconds into the ramp. HAProxy recorded 3,317 `503 sC--` log entries. Each of 500 sampled failure IDs matched a HAProxy entry and zero matched the backend gateway. HAProxy's sampled connection and response timings were both `-1`, and total time was 1,999–2,010 ms. The existing upstream TLS reuse fix was active. The bottleneck at this load was the HAProxy connection phase to the backend's Modal ingress. The evidence does not identify the internal Modal component delaying the connection. [HAProxy connection termination flags](https://docs.haproxy.org/3.2/configuration.html#8.5)

At startup, private JDBC `SELECT 1` p50/p95/p99 was 0.428/0.540/0.599 ms. During the aborted ramp HikariCP peaked at two active connections and zero pending, and PostgreSQL had 16 connections and one sampled lock waiter, with a maximum observed wait of 1.85 ms. Mean section lock hold was 3.021 ms and mean COMMIT was 0.618 ms. Java peaked at 1.54 cores and 786.45 MiB RSS. k6 averaged 0.876 of its four CPU cores and peaked at 1,305.17 MiB RSS. The sampled resources did not show generator or database saturation during this run.

## Reconciliation and cleanup

All 7,304 durable outcomes matched the database ledger and the same number of audit entries, with zero inconsistent or unmatched durable records. Of these, 53 committed without an HTTP acknowledgement. The reconciler found 281 intents without a durable commit and 234 individual mutation attempts without matching responses. The bounded recovery resolved 64 of 398 pending journeys. The final snapshot retained 164 active enrollments. All eight structural SQL invariants passed and the exact final-state gate failed. The App stopped and Modal reported zero active Sandboxes and containers.

Modal attributed $0.35903797 before credits to the run, with $0.27008417 CPU, $0.08170564 memory and $0.00724816 network egress. The workspace summary showed $0 billed at verification time.

The new run and its failed gate are available in Performance Lab. The run-local `report.json`, `http-trace.json`, `http-hop-evidence.json`, `operation-reconciliation.json` and `invariants.json` are retained under `infrastructure/.local/modal/runs/modal-20261108T134622Z-454052/`.
