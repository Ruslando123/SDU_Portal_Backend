# HTTP workloads

Phase 6 uses native k6 **2.3.0**, verified against its official SHA-256. The Mac run is intentionally small (at most 10 VUs); the user will run larger levels on another computer. No 5,000-VU result is claimed.

Use `../benchmark/install-k6.py` and the guarded runner; see [methodology](../../docs/load-testing-methodology.md). Scripts use real HTTP, server sessions and CSRF. Ordinary workloads reuse independently authenticated sessions; only `auth` intentionally benchmarks login. `smoke` covers listing, own history, enrollment, schedule, withdrawal and historical replay. Identical intentions reuse their UUID; new intentions use new random UUIDs.

| MODE | Executor | Scope |
| --- | --- | --- |
| auth | per-vu-iterations | CSRF → login → fresh CSRF → me → logout; bounded login controls remain active |
| smoke | per-vu-iterations | Complete student journey and exact historical replay |
| normal | constant-vus | Closed model: weighted hot/warm/cold catalog reads, own reads, enrollment/withdrawal, 0.5–1.5 second think times |
| catalog-read | constant-vus | Four out of five iterations browse published courses and one lists sections. No mutations. |
| browse-read | constant-vus | Catalog, instructor details and a schedule with course titles in each iteration. No mutations. |
| arrival | constant-arrival-rate | Open model; exactly one read request per iteration, so configured rate equals offered HTTP RPS |
| ramp | ramping-arrival-rate | Open model with gradual read arrival changes |
| hot | shared-iterations | Distinct students; one attempt each against a 30-seat section; separate success/SECTION_FULL cohorts |
| spike | ramping-arrival-rate | Sudden registration journey arrival; each iteration normally has three HTTP requests, so iteration rate is not HTTP RPS |
| ladder | ramping-vus | One explicit selected VU level; repeat 50 → 200 → 500 → 1000 → 2500 → 5000 only with resource checks between runs |
| warmup | constant-vus | Paced reads; recorded separately and excluded from target workload summaries |

Prepared session material and random fixture credentials are ignored local files. They are never logged or exported as metric tags. `name` and custom endpoint/result/code tags are bounded; raw URL, resource/student/session/key IDs and VU/iteration IDs are excluded from system tags. Large fixture arrays use SharedArray instead of retaining a complete 5,000-student JSON object in every VU.

`portal_unexpected` includes network failures, coordination failures, 429, 503, other 5xx and unplanned responses. Named stable academic business denials are counted separately. Built-in `http_req_failed` also treats HTTP 409 as failed transport status and is **not** the project's technical-error SLO. Never interpret a blanket 409 exclusion or a zero `http_req_failed` count as correctness evidence.

Results contain the original k6 summary and gzipped raw metric points, classified latency samples by endpoint/result, resource samples, fresh Prometheus snapshots, Prometheus time series, normalized PostgreSQL statements, settings and independent SQL invariant checks. Failed/setup/host-pause runs remain visible. The runner stops on resource-budget violations and refuses a follow-up load run while RESOURCE-STOP exists.
