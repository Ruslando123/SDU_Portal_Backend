"""Isolate the edge and each Spring Boot instance for the resource comparison."""
from collections import Counter
import json
import re
from statistics import median
from urllib.parse import urlsplit

import modal

from bench_config import RESOURCE_EDGE_CPU, RESOURCE_EDGE_MIB
from bench_evidence import read_samples, resource_summary
from smoke import ASSETS, HERE, REGION, execute
from split_topology import read_binary_export, read_sample_snapshot
from worker import safe_log, validate_origin


def image(backend):
    return (backend.add_local_file(HERE / "resource-edge.sh", "/opt/sdu/resource-edge.sh")
            .add_local_file(HERE / "sample-http.py", "/opt/sdu/sample-http.py")
            .add_local_file(ASSETS / "haproxy-linux-amd64", "/opt/sdu/haproxy"))


def create_node(app, backend, case, run_id, index, env, credentials, node_key, *, edge_key=None):
    node_env = {**env, "SDU_RESOURCE_NODE": "primary" if index == 0 else "secondary",
                "SDU_BENCH_POOL": str((case.pool_size - (case.jdbc_security_pool or 0)) // case.instances),
                "SDU_NODE_HEAP_MIB": str(case.sandbox_memory // 2),
                "SDU_NODE_PROCESSORS": str(case.sandbox_cpu)}
    cpu = case.sidecar_cpu_limit if case.private_db_sidecar else case.sandbox_cpu
    memory = case.sidecar_memory_limit if case.private_db_sidecar else case.sandbox_memory
    gateway_keys = {"SDU_GATE_KEY": node_key}
    if case.private_loopback:
        if edge_key is None:
            raise ValueError("The loopback edge requires a separate ingress key")
        backend = image(backend)
        node_env.update(SDU_PRIVATE_HTTP_LOOPBACK="1", SDU_NODE_GATEWAY_LOG="node-gateway",
                        SDU_EDGE_DIAGNOSTICS="1", SDU_EDGE_HTTP_REUSE="always")
        if case.http_experiment:
            node_env["SDU_HTTP_EXPERIMENT"] = case.http_experiment
        gateway_keys = {"SDU_GATE_KEY": edge_key, "SDU_NODE_GATE_KEY": node_key}
    return modal.Sandbox.create("bash", "/opt/sdu/start.sh", app=app, name=f"sdu-resource-app-{index + 1}",
        image=backend, secrets=[*credentials, modal.Secret.from_dict(gateway_keys)],
        region=REGION, cpu=(cpu,) * 2, memory=(memory,) * 2,
        timeout=case.sandbox_seconds, volumes={}, encrypted_ports=[8080],
        env=node_env, tags={"sdu_run": run_id})


def node_host(node):
    return urlsplit(validate_origin(node.tunnels(timeout=15)[8080].url)).hostname


def create_edge(app, backend, case, run_id, hosts, gate_key, node_key, env):
    edge_secret = modal.Secret.from_dict({"SDU_GATE_KEY": gate_key, "SDU_NODE_GATE_KEY": node_key,
                                         "SDU_NODE_HOSTS": ",".join(hosts)})
    edge_env = {key: env[key] for key in ("SDU_BENCHMARK", "SDU_BENCH_REQUEST_LIMIT", "SDU_BENCH_RESPONSE_MIB")}
    if case.private_scaled:
        edge_env["SDU_EDGE_DIAGNOSTICS"] = "1"
        # The paired relay probe isolated connect timeouts on fresh upstream TLS sessions.
        edge_env["SDU_EDGE_HTTP_REUSE"] = "always"
    return modal.Sandbox.create("bash", "/opt/sdu/resource-edge.sh", app=app, name="sdu-resource-edge",
        image=image(backend), secrets=[edge_secret], region=REGION, cpu=(RESOURCE_EDGE_CPU,) * 2,
        memory=(RESOURCE_EDGE_MIB,) * 2, timeout=case.sandbox_seconds, volumes={},
        encrypted_ports=[8080], env=edge_env, tags={"sdu_run": run_id})


def verify_edge(edge, key, instance_count):
    status, region, _ = execute(edge, "bash", "-c", """
        for ((i=0;i<25;i++)); do
          [[ ! -f /tmp/sdu-cloud/failed ]] || exit 2
          if [[ -f /tmp/sdu-cloud/ready ]]; then printf '%s' "$MODAL_REGION"; exit; fi
          sleep 1
        done
        exit 1
    """, timeout=30)
    if status or region != REGION:
        raise RuntimeError("HAProxy edge did not start in the selected region")
    origin = validate_origin(edge.tunnels(timeout=15)[8080].url)
    status, raw, _ = execute(edge, "python3", "-c", """
import json, os, sys, time, urllib.error, urllib.request

def request(path, auth=True):
    headers = {"X-SDU-Benchmark-Key": os.environ["SDU_EDGE_VERIFY_KEY"]} if auth else {}
    url = "http://127.0.0.1:8080" + path
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=3) as response:
            return response.status, response.headers.get("X-SDU-Backend-Instance")
    except urllib.error.HTTPError as failure:
        return failure.code, None
    except OSError:
        return 0, None

expected = {f"app{index + 1}" for index in range(int(sys.argv[1]))}
seen, results = set(), []
for _ in range(8):
    status, backend = request("/api/v1/auth/csrf")
    results.append(status)
    if status == 200 and backend in expected:
        seen.add(backend)
    if seen == expected:
        break
    time.sleep(.5)
checks = {"without_gateway_key": request("/api/v1/auth/csrf", False)[0],
          "without_student_session": request("/api/v1/me")[0],
          "readiness_hidden": request("/actuator/health/readiness")[0],
          "metrics_hidden": request("/actuator/prometheus")[0],
          "administration_hidden": request("/api/v1/admin/benchmark-authorize")[0]}
print(json.dumps({"statuses": results, "observed_backends": sorted(seen), **checks}))
sys.exit(0 if seen == expected and list(checks.values()) == [403, 401, 404, 404, 404] else 1)
    """, str(instance_count), timeout=40, secret=modal.Secret.from_dict({"SDU_EDGE_VERIFY_KEY": key}))
    if status:
        raise RuntimeError(f"Benchmark gateway routing and authorization probe failed: {raw.strip()}")
    return origin, json.loads(raw)


def verify_loopback_tls(node, node_key):
    status, raw, _ = execute(node, "python3", "-c", """
import json, os, ssl, urllib.error, urllib.request
key = os.environ['SDU_NODE_VERIFY_KEY']
ca = '/tmp/sdu-cloud/http-ca.crt'
def request(host, context=None, authenticated=True, scheme='https'):
    headers = {'X-SDU-Benchmark-Key': key} if authenticated else {}
    request = urllib.request.Request(f'{scheme}://{host}:8443/api/v1/auth/csrf', headers=headers)
    try:
        with urllib.request.urlopen(request, context=context, timeout=4) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except urllib.error.URLError as error:
        return 'certificate_rejected' if isinstance(error.reason, ssl.SSLCertVerificationError) else 'connection_error'
trusted = ssl.create_default_context(cafile=ca)
print(json.dumps({'verified_tls': request('localhost', trusted), 'without_node_key': request('localhost', trusted, False),
    'wrong_hostname': request('127.0.0.1', trusted), 'untrusted_ca': request('localhost'),
    'plaintext': request('localhost', scheme='http')}))
    """, timeout=22, secret=modal.Secret.from_dict({"SDU_NODE_VERIFY_KEY": node_key}))
    checks = json.loads(raw) if status == 0 else {}
    if checks != {"verified_tls": 200, "without_node_key": 403, "wrong_hostname": "certificate_rejected",
                  "untrusted_ca": "certificate_rejected", "plaintext": 400} or set(node.tunnels()) != {8080}:
        raise RuntimeError("The private HAProxy upstream did not pass TLS and exposure checks")
    return {**checks, "forwarded_ports": [8080]}


def quota(sandbox):
    status, raw, _ = execute(sandbox, "bash", "-c", """
        cd /sys/fs/cgroup
        for file in cpu.max memory.max cpu.stat memory.peak cpu/cpu.cfs_quota_us cpu/cpu.cfs_period_us \
                    cpu/cpu.stat memory/memory.limit_in_bytes memory/memory.max_usage_in_bytes; do
          if [[ -r "$file" ]]; then while IFS= read -r line; do printf '%s\t%s\n' "$file" "$line"; done < "$file"; fi
        done
        printf 'visible_logical_cpus\t%s\n' "$(getconf _NPROCESSORS_ONLN)"
    """, timeout=7)
    if status:
        return None
    data = {}
    for line in raw.splitlines():
        key, _, value = line.partition("\t")
        if key.endswith("cpu.stat"):
            field, _, value = value.partition(" ")
            key += "/" + field
        if re.fullmatch(r"(?:-?\d+|max)(?: \d+)?", value):
            data[key] = int(value) if re.fullmatch(r"-?\d+", value) else value
    return data or None


def database_roundtrip(sandbox):
    status, raw, _ = execute(sandbox, "cat", "/tmp/sdu-cloud/db-roundtrip.log", timeout=7)
    values = [float(line.split()[1]) for line in raw.splitlines() if line.startswith("Time: ")] if status == 0 else []
    return {"samples": len(values), "median_ms": median(values) if values else None, "maximum_ms": max(values, default=None)}


def backend_samples(sandbox):
    return read_sample_snapshot(sandbox, "/tmp/sdu-cloud/samples.tsv",
                                "/tmp/sdu-cloud/backend-sample-export.tsv.gz", "Backend samples")


def node_measurements(nodes, path, window, first_samples=None):
    observed = []
    limits = {"window_started_ms": window["scenario_started_ms"],
              "window_ended_ms": window["observed_window_ended_ms"]}
    for index, sandbox in enumerate(nodes, 1):
        raw = first_samples if index == 1 else None
        if raw is None:
            status, raw, _ = execute(sandbox, "cat", "/tmp/sdu-cloud/samples.tsv", timeout=12)
            if status:
                raw = None
        if raw is None:
            observed.append(None)
            continue
        (path / f"samples-app{index}.tsv").write_text(raw)
        node = resource_summary(read_samples(raw), limits)
        status, settings, _ = execute(sandbox, "awk", "/Heap Max Capacity:|CPUs:/{print; if (++n==2) exit}",
                                      "/tmp/sdu-cloud/backend.log", timeout=5)
        node["jvm_startup"] = settings.splitlines() if status == 0 else None
        node["idle_database_roundtrip_median_ms"] = database_roundtrip(sandbox)["median_ms"]
        observed.append(node)
    return observed


def retain_logs(sandbox, path, role, redactions):
    for name in ("backend", "gateway", "haproxy", "db-bridge", "node-gateway", "edge-start", "redis"):
        status, raw, _ = execute(sandbox, "tail", "-c", "12000", f"/tmp/sdu-cloud/{name}.log", timeout=7)
        if status == 0:
            (path / f"{role}-{name}.log").write_text(safe_log(raw, *redactions))


def collect_jvm_evidence(sandbox, path, mode, name="jvm-evidence"):
    directory = f"/tmp/sdu-cloud/{name}"
    archive = f"/tmp/sdu-cloud/{name}.tar.gz"
    status, data, _ = execute(sandbox, "bash", "-ec", """
        umask 077
        touch "$1/finish"
        for ((i=0;i<12;i++)); do [[ ! -f "$1/complete" ]] || break; sleep 1; done
        [[ -f "$1/complete" ]]
        /tmp/sdu-cloud/jre/bin/jfr summary "$1/benchmark.jfr" > "$1/jfr-summary.txt"
        tail -1 "$1/requests.tsv"
        awk '$1 == "jdk.VirtualThreadPinned" {print $2}' "$1/jfr-summary.txt"
        if [[ -f "$1/pools.tsv" ]]; then tail -1 "$1/pools.tsv"; else printf '%s\n' '-'; fi
        if [[ -f "$1/saturation.tsv" ]]; then wc -l < "$1/saturation.tsv"; else printf '0\n'; fi
        (cd "$1"; shopt -s nullglob; tar -czf "$2" benchmark.jfr jfr-summary.txt requests.tsv threads-*.txt pool*.tsv saturation*.tsv)
        wc -c < "$2"
    """, "sdu-jvm-export", directory, archive, timeout=25)
    if status:
        raise RuntimeError("JVM evidence did not complete")
    sample, pinned, pool_sample, saturation_count, size = data.splitlines()
    _, _, peak, virtual, platform = map(int, sample.split())
    payload = read_binary_export(sandbox, archive, size, limit_mib=32, label="JVM evidence")
    (path / f"{name}.tar.gz").write_bytes(payload)
    expected, other = (virtual, platform) if mode == "virtual" else (platform, virtual)
    result = {"verified": expected > 0 and other == 0, "http_active_peak": peak, "virtual_requests": virtual,
              "platform_requests": platform, "pinned_events": int(pinned)}
    if pool_sample != "-":
        _, _, tomcat_max, _, hikari_max, _ = map(int, pool_sample.split())
        result.update(tomcat_max=tomcat_max, hikari_max=hikari_max, saturation_dumps=int(saturation_count))
    return result


def collect_http_samples(sandbox, path, *, compressed=False, window=None):
    source = "/tmp/sdu-cloud/http-samples.jsonl"
    if compressed:
        raw = read_sample_snapshot(sandbox, source, "/tmp/sdu-cloud/http-sample-export.jsonl.gz", "HTTP samples")
        status = 0
    else:
        status, raw, _ = execute(sandbox, "cat", source, timeout=15)
    samples = [json.loads(line) for line in raw.splitlines()] if status == 0 else []
    if samples:
        (path / "http-samples.jsonl").write_text(raw)
    status, raw, _ = execute(sandbox, "awk", '$3=="sdu_listener" && NF==10 {print}',
                             "/tmp/sdu-cloud/node-gateway.log", timeout=12)
    listeners = []
    if status == 0:
        fields = ("epoch_ms", "accepted", "closed", "client_hello", "tls_verified", "tls_resumed", "http_active_transitions")
        listeners = [dict(zip(fields, map(int, line.split()[3:]), strict=True)) for line in raw.splitlines()]
        (path / "tls-listener-samples.jsonl").write_text("".join(json.dumps(row) + "\n" for row in listeners))
    status, raw, _ = execute(sandbox, "awk", '$3=="sdu_upstream_pool" && NF==6 {print $4,$5,$6}',
                             "/tmp/sdu-cloud/node-gateway.log", timeout=10)
    upstream_pool = None
    if status == 0 and raw.strip():
        pool = [dict(zip(("epoch_ms", "fresh", "reused"), map(int, line.split()), strict=True))
                for line in raw.splitlines()]
        (path / "node-upstream-pool.jsonl").write_text("".join(json.dumps(row) + "\n" for row in pool))
        start, end = ((window or {}).get(key) for key in ("window_started_ms", "window_ended_ms"))
        selected = [row for row in pool if start is not None and end is not None and start <= row["epoch_ms"] <= end]
        if len(selected) >= 2:
            upstream_pool = {key: selected[-1][key] - selected[0][key] for key in ("fresh", "reused")}
            upstream_pool["sample_span_seconds"] = (selected[-1]["epoch_ms"] - selected[0]["epoch_ms"]) / 1000
    return {"system_samples": len(samples), "tls_listener_samples": len(listeners),
            "haproxy_samples": sum(bool(row["haproxy"]) for row in samples),
            "socket_samples": sum(bool(row["sockets"]) for row in samples), "node_upstream_pool": upstream_pool}


def http_hop_evidence(edge, node, failures, *, loopback=False, through_haproxy=True):
    # Include all HAProxy status counts and correlate a bounded sample of failed traces.
    traces = list(dict.fromkeys(row["trace"] for row in failures))[:500]
    statuses, raw, _ = execute(edge, "awk", "-v", "ids=" + ",".join(traces), """
      BEGIN {n=split(ids,a,","); for(i=1;i<=n;i++) wanted[a[i]]=1}
      $1=="sdu_haproxy" && NF==12 && $2 ~ /^[0-9a-f-]+$/ && length($2)==36 {
        counts[$3 " " $4]++; if ($2 in wanted) print
      }
      END {for(k in counts) print "count",k,counts[k]}
    """, "/tmp/sdu-cloud/haproxy.log", timeout=10) if through_haproxy else (0, "", "")
    if statuses:
        raise RuntimeError("HAProxy access evidence unavailable")
    counts, hops = {}, {}
    for line in raw.splitlines():
        fields = line.split()
        if fields[0] == "count":
            _, code, termination, count = fields
            counts[f"{code} {termination}"] = int(count)
        elif fields[0] == "sdu_haproxy":
            _, trace, code, termination, *timings = fields
            hops[trace] = {"status": int(code), "termination": termination,
                           **dict(zip(("request_ms", "queue_ms", "connect_ms", "response_ms", "total_ms",
                                       "retries", "server_queue", "backend_queue"), map(int, timings), strict=True))}
    node_events, node_upstream = {}, {}
    if traces:
        status, raw, _ = execute(node, "awk", "-v", "ids=" + ",".join(traces), """
          BEGIN {n=split(ids,a,","); for(i=1;i<=n;i++) wanted[a[i]]=1}
          $3=="trace" && NF==8 && ($4 in wanted) {print $4,$5,$6}
          $3=="sdu_upstream_failure" && NF==8 && ($4 in wanted) {print $4,$5,$6,$7,$8}
        """, "/tmp/sdu-cloud/node-gateway.log" if loopback else "/tmp/sdu-cloud/gateway.log", timeout=8)
        if status:
            raise RuntimeError("Backend gateway access evidence unavailable")
        for line in raw.splitlines():
            fields = line.split()
            if len(fields) == 3:
                trace, event, code = fields
                node_events.setdefault(trace, []).append({"event": event, "status": int(code)})
            else:
                trace, acquired, written, elapsed, reused = fields
                node_upstream[trace] = {"connection_us": int(acquired), "request_written_us": int(written),
                                        "elapsed_us": int(elapsed), "reused": reused == "true"}
    return {"status_termination_counts": counts, "traces_sampled": len(traces),
            "matched_haproxy": len(hops), "matched_node_gateway": len(node_events),
            "sampled_terminations": dict(Counter(hop["termination"] for hop in hops.values())),
            "haproxy": hops, "node_gateway": node_events, "node_upstream": node_upstream}
