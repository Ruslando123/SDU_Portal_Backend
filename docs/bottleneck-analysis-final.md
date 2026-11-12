# Release candidate bottleneck analysis

## Historical HTTP 502 inventory

The retained artifacts contain eight confirmed HTTP 502 responses. A separate earlier mixed run records three generic HTTP errors without per-request status or timestamps. The analysis preserves that distinction.

| Evidence | Phase and UTC time | Requests | Closest sampled Hikari pending / PostgreSQL active | Attribution evidence |
| --- | --- | --- | --- | --- |
| [Initial mixed 50](../infrastructure/.local/modal/runs/modal-20261107T133402Z-f2243d/report.json) | Measurement, exact time unavailable | Three generic HTTP errors | No per-request match possible | Status, method and request IDs were not retained |
| [Mixed 50 diagnostic](../infrastructure/.local/modal/runs/modal-20261107T133910Z-0d1a70/measure-k6.log) | Measurement, 13:41:16 | GET me, GET sections ×2 | 0 / 0 at +248 ms | Gateway response marker was not yet implemented |
| [Read 200, pool 10](../infrastructure/.local/modal/runs/modal-20261107T135802Z-cb6fdf/measure-k6.log) | Measurement, 13:59:45 | GET me, GET sections, GET own enrollments | 0 / 0 at +1,325 ms | No Spring response or SDU gateway error marker |
| [Read 200, pool 16](../infrastructure/.local/modal/runs/modal-20261107T140255Z-6298d3/warmup-k6.log) | Warm-up, 14:03:57 | GET me ×2 | 0 / 0 at −63 ms | No Spring response or SDU gateway error marker |

All four runs reached the generator stage after Sandbox readiness, completed SQL verification, and recorded verified cleanup. The archived CPU and whole-container memory samples were unavailable. The 200-VU pool-10 run logged gateway transport failures and Spring transaction acquisition exceptions around 13:58:45, one minute before the measurement 502s. The pool-16 run logged a gateway transport failure at 14:03:41, sixteen seconds before its two 502s. Those incidents belong to separate cohorts. A near-zero five-second snapshot does not exclude a short pool queue between samples.

## Correlation and reproduction

Each k6 attempt now sends a run-local UUID as `X-Correlation-Id`. The SDU gateway records authenticated ingress, upstream HTTP status and completed body or local transport failure. Spring uses the same ID in its existing correlation filter. The client records failed-attempt method, route label, timestamps, protocol, a small response-signature allowlist and an optional UUID-shaped provider request identifier. Raw URLs, account IDs, cookies, authorization, arbitrary headers and response bodies are excluded from the new trace. The runner joins client failures to gateway events in the private `http-trace.json` artifact. Performance Lab projects counts only.

Go regression tests reproduce an upstream application 502 and a gateway-generated 502 caused by upstream EOF. Each produces a distinct marker and correlated trace. These are diagnostic controls.

Both [first](../infrastructure/.local/modal/runs/modal-20261107T142900Z-db6316/http-trace.json) and [second](../infrastructure/.local/modal/runs/modal-20261107T143232Z-b5105b/http-trace.json) mixed 50-VU runs passed. Each recorded 9,000 ingress, 9,000 Spring response and 9,000 gateway completion events across warm-up and measurement, with no unexpected HTTP responses. The 200-VU diagnostic uses the same authenticated Connect path and original benchmark backend artifact.

## Reproduced HTTP 502

The [instrumented pool-16 Connect run](../infrastructure/.local/modal/runs/modal-20261107T144203Z-766210/http-trace.json) reproduced a measurement-window HTTP 502 on `GET sections` at 14:43:59.531 UTC. The client used HTTP/1.1 and received the response in 25 ms. The response had no SDU hop marker, echoed correlation ID, accepted provider request ID or server header. The request has no authenticated ingress or backend event. The generator recorded 23,907 HTTP attempts across both windows and drain. The gateway recorded 23,906 authenticated ingress events. This is one missing ingress for one unmarked 502.

The closest resource sample arrived 2.375 seconds after the response. Hikari had 14 active and 20 pending connections, PostgreSQL had 16 connections, one active query and no sampled lock wait. The sample cannot describe the exact 25 ms event. The Java process, Spring metrics and database continued to be sampled and the Sandbox verified SQL and cleanup. The retained Modal system stream shows the later planned App stop and no event at the failed request. Host kernel and provider ingress logs for the individual response were unavailable.

The same run also captured a separate warm-up cohort of 13 client timeouts and two Spring HTTP 503 responses. Ten timeouts matched a canceled SDU gateway request, and three matched a completed gateway body. These are distinguishable from the pre-ingress 502. Neither larger Hikari sizing nor classifying local dependency failure as HTTP 503 explains the pre-ingress response.

The encrypted-tunnel experiment forwards only port 8080 to the same TLS-protected gateway. An independent per-run 256-bit gateway key is required and Spring still requires a student session. Before generating load the Function checks rejection without the gateway key, rejection without a student session and hidden management and administration paths. This mode is limited to closed CLI diagnostics. The regular runner and admin UI retain Sandbox Connect authentication.

### Alternate route result

The [200-VU read](../infrastructure/.local/modal/runs/modal-20261107T144711Z-bb3a74/report.json) and [200-VU mixed](../infrastructure/.local/modal/runs/modal-20261107T145122Z-01aece/report.json) runs over the encrypted tunnel passed with zero unexpected outcomes in both windows. Both verified SQL, access restrictions and cleanup. The read case held pool size and compute limits fixed against the failing Connect pool-16 diagnostic. This verifies a bounded workaround on the benchmark path. It leaves the exact Connect ingress component and behavior under repetition unresolved.

## Interpretation and operational boundary

A missing response marker alone cannot locate a historical response. A newly observed 502 with no matching authenticated ingress, healthy neighboring requests and complete trace coverage excludes an observed Spring response or a locally generated SDU gateway response for that request. Provider-internal proxy, tunnel and routing logs are outside the available evidence. The [official Modal networking guide](https://modal.com/docs/guide/sandbox-networking) documents the Connect path and its access controls.

Hikari queueing is an independent, observed resource constraint. In the original 100-VU mixed run, pending reached 55 in one sample, and PostgreSQL had up to four sampled lock waiters. In the prior 200-VU pool-size comparison, pool 16 reduced warm-up errors from 19 to 4, and the overall gate still failed. Raising the pool does not resolve unmarked 502 responses. The API maps confirmed transient transaction acquisition failure to retryable 503. Business rules, Flyway and the durable enrollment design retain their existing contracts.
