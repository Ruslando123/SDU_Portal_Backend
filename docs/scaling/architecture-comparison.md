# Phase 9 — Architecture comparison and release boundary

The pre-experiment source is preserved in local Git tag `sdu-rc-phase8` at `d1879b3f7cb9ec6872284bb0f7a78d7a0bd53bb6`. Its release JAR is preserved locally as `infrastructure/.local/scaling/phase8-rc.jar` with SHA-256 `13067c44b7037e77170123c30efa36941516a49c6f757f8749431f5f4f5195aa`. The working tree contains the optional Phase 9 configurations. GitHub was not used. Restore a source copy for review with `git worktree add --detach /path/to/sdu-phase8-rc sdu-rc-phase8`.

| Experiment | Comparison | Evidence and decision |
| --- | --- | --- |
| PgBouncer | Direct vs session vs transaction, same fixed Sandbox budget | [PgBouncer results](pgbouncer-results.md). Keep direct as default. Transaction pooling is compatible with the current runtime SQL and remains an opt-in investigation. |
| HAProxy | One vs two processes with fixed aggregate budget, separate scale-out and pooler comparison | Keep one backend by default. The fixed two-backend HTTP gate failed. The larger-budget 200-VU pass was a separate result. [Load balancing results](load-balancing-results.md). |
| Streaming replica | Primary-only catalog vs the same catalog workload on a physical standby | Keep primary routing as default. The standby handled the intended reads. Both HTTP gates failed. [Replication results](replication-results.md). |
| Private database route | Historical profile A vs PostgreSQL on a same-host private Modal Sidecar | [Private PostgreSQL route and workload evidence](private-postgres-route.md). |
| Kubernetes | Non-production overlays with fixed pod counts and separate HPA option | [Kubernetes deployment](kubernetes-deployment.md). Static verification. Runtime and capacity Not Tested. |
| Capacity | Closed-model ladder and separately offered open-model load | [5,000-user report](5000-users-capacity-report.md). Mixed 500 remains failed. Higher tiers remain Not Tested. |

Use Performance Lab's Phase 9 table to inspect the measured architecture, actual VUs, HTTP gate, business acknowledgements, pool and replication samples, SQL, cost and cleanup. Shared-Sandbox CPU and memory are aggregate limits. Process CPU metrics and reservations are reported separately. A changed workload or resource budget has its own label. The 200-VU controls establish qualification only at that load.

The Phase 9 shared-topology experiments metered USD 0.52687720 before credits and verified cleanup for all 16 Modal runs. Kubernetes checks and local tests added no Modal usage. The follow-up with separate allocations has its own budget and results. [Resource scaling report](resource-scaling-modal.md).
