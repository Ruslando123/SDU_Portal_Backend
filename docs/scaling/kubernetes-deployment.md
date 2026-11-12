# Phase 9 — Kubernetes deployment

**Status: statically rendered. Deployment, ingress, rollout, HPA and Kubernetes load benchmark are Not Tested.** There was no configured Kubernetes context for the experiment. Modal workloads are separate Sandbox and Function resources. No managed Kubernetes cluster was created and there was no cluster spend.

The base uses a Deployment with a rolling update, a ClusterIP Service with no session affinity, independent startup/readiness/liveness probes, resource requests and limits, a ConfigMap, Secrets references and a NetworkPolicy. The management listener has no Service or Ingress route. The Ingress sends only `/api/v1` through a preinstalled Traefik class with TLS for the synthetic `.test` host. Spring still validates the session and CSRF token. A five-second preStop window and Spring graceful shutdown fit inside the 35-second termination period. Test propagation and draining on the actual ingress controller before relying on these settings. [Kubernetes probes](https://kubernetes.io/docs/concepts/workloads/pods/probes/) · [Deployment rollout](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/).

## Prerequisites

Use an explicitly selected non-production context, a CNI that enforces NetworkPolicies, a Traefik controller in namespace `traefik` with pod label `app.kubernetes.io/name=traefik`, and an isolated PostgreSQL primary in namespace `sdu-data` with pod label `app.kubernetes.io/name=postgresql`. Adapt the DNS and peer selectors for the installed cluster. Provide matching primary DNS, TLS and private Secrets. The overlay intentionally provisions neither a database nor a certificate authority.

Build the backend image from the repository root and load it into the selected test cluster or update the overlay to a pinned registry digest.

```sh
(cd backend && ./gradlew bootJar)
docker build -f infrastructure/monitoring/Dockerfile.backend -t sdu-backend:phase9-local backend
kubectl kustomize infrastructure/kubernetes/overlays/dev
kubectl kustomize infrastructure/kubernetes/overlays/benchmark
kubectl kustomize infrastructure/kubernetes/overlays/benchmark-hpa
```

## Deploy and stop

Create the synthetic namespace first. Populate the following env files outside the repository with mode 0600. The migration file contains the direct primary `DB_URL`, `DB_MIGRATION_USERNAME`, `DB_MIGRATION_PASSWORD` and `DB_RUNTIME_PASSWORD`. The runtime file contains only the primary `DB_URL` and `DB_RUNTIME_PASSWORD`. The database URL and PostgreSQL network policy must refer to the same test endpoint. Create the TLS secret with a certificate for the chosen host. The migration Job runs once before the Deployment so runtime Pods never receive migrator credentials. Confirm any cluster-wide default-deny policy permits that Job to contact the isolated primary.

```sh
ns=sdu-dev
kubectl apply -f infrastructure/kubernetes/overlays/dev/namespace.yaml
kubectl -n "$ns" create secret generic sdu-backend-migration --from-env-file=/private/migration.env
kubectl -n "$ns" create secret generic sdu-backend-database --from-env-file=/private/runtime.env
kubectl -n "$ns" create secret tls sdu-test-tls --cert=/private/test.crt --key=/private/test.key
kubectl -n "$ns" apply -f infrastructure/kubernetes/examples/migrate-job.yaml
kubectl -n "$ns" wait --for=condition=complete job/sdu-flyway --timeout=120s
kubectl apply -k infrastructure/kubernetes/overlays/dev
kubectl -n "$ns" rollout status deployment/sdu-backend --timeout=180s
```

Seed only the synthetic fixture using the existing operator path. Keep the PostgreSQL listener off public routes. The dev overlay runs one replica. The benchmark overlay runs two. The HPA overlay is separate and requires a working resource metrics API. With four JDBC connections per Pod, the three-Pod maximum and one surge Pod reserve up to 16 runtime connections. Budget the migration connection, observation and other database clients separately. A higher HPA maximum requires revisiting that budget. [Kubernetes HPA](https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/).

To stop an isolated namespace owned by this experiment, delete it with `kubectl delete namespace sdu-dev`. Use `sdu-benchmark` for the benchmark overlays. Confirm that the selected context and namespace are the experiment's before removal.
