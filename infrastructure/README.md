# Local infrastructure

The ordinary local environment uses `compose.yaml` with one digest-pinned PostgreSQL 18.6 service, loopback port, persistent named volume and healthcheck. The ordinary local Compose project starts PostgreSQL only.

From the project root, configure `.env` and run `docker compose --env-file .env -f infrastructure/compose.yaml up -d --wait postgres`. See the root [README](../README.md) for required environment variables, Windows/macOS instructions, migrations and safe shutdown. PostgreSQL 18 mounts data at `/var/lib/postgresql`. All application schemas are created by Flyway, with no entrypoint schema-init script.

Testcontainers creates separate disposable databases/containers for verification; it does not use this volume. Phase 6 uses a separate [benchmark/monitoring stack](monitoring/README.md), [k6 scripts](k6/README.md) and a guarded synthetic dataset. It does not touch this development volume. The [measured report](../docs/phase6-performance-report.md) distinguishes the executed small Mac workloads from larger prepared workloads for another machine.
