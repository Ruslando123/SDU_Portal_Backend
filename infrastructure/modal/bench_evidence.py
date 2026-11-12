"""Interpret k6 and in-Sandbox samples without turning missing evidence into zero."""
import json
import math
import re

CATEGORIES = ("success", "business_denial", "rate_limited", "unavailable", "timeout", "transport",
              "http_error", "invalid_response", "gateway_limit")
PROM_LINE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{.*\})?\s+(\S+)(?:\s+\d+)?$")
TIMELINE_METRICS = {"sdu_java_cpu_ticks_total", "sdu_java_ticks_per_second", "sdu_java_rss_kib",
                    "jvm_memory_used_bytes", "hikaricp_connections_active", "hikaricp_connections_pending"}
TRACE_ID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
CLIENT_TRACE = re.compile(
    rf"unexpected route=(?P<route>[a-z_]+) method=(?P<method>GET|POST|DELETE) status=(?P<status>\d{{1,3}}) "
    rf"outcome=(?P<outcome>[a-z_]+) source=(?P<source>backend|gateway-error|unattributed) trace=(?P<trace>{TRACE_ID}) "
    r"started_ms=(?P<started_ms>\d{13}) ended_ms=(?P<ended_ms>\d{13}) "
    r"protocol=(?P<protocol>HTTP/1\.1|HTTP/2(?:\.0)?|unknown) server=(?P<server>\w+) "
    rf"signature=(?P<signature>\w+) echoed=(?P<echoed>true|false) provider=(?P<provider>{TRACE_ID}|-)", re.I)
GATEWAY_TRACE = re.compile(rf"\S+ \S+ trace ({TRACE_ID}) (ingress|backend|gateway) (\d{{1,3}}) ([a-z_]+) (\d{{13}})")


def client_failures(outcome):
    rows = []
    for phase in ("warmup", "measure"):
        for line in ((outcome.get(phase) or {}).get("log") or "").splitlines():
            if match := CLIENT_TRACE.search(line):
                values = match.groupdict()
                for key in ("status", "started_ms", "ended_ms"):
                    values[key] = int(values[key])
                rows.append({"phase": phase, **values})
    return rows[:250]


def join_traces(failures, text):
    counts, events = {}, {}
    for line in text.splitlines():
        if match := GATEWAY_TRACE.fullmatch(line):
            key, event, status, reason, epoch = match.groups()
            events.setdefault(key, []).append({"event": event, "status": int(status), "reason": reason, "epoch_ms": int(epoch)})
        elif match := re.fullmatch(r"count (ingress|backend|gateway) (\d+)", line):
            counts[match[1]] = int(match[2])
    return {"gateway_events": counts, "failures": [
        {**row, "local_events": events.get(row["trace"], [])} for row in failures
    ]}


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def phase_metrics(phase, seconds, offset_seconds=0):
    summary = (phase or {}).get("summary") or {}
    metrics = summary.get("metrics", {})
    def value(metric, statistic):
        return number(metrics.get(metric, {}).get("values", {}).get(statistic))

    duration = number(summary.get("state", {}).get("testRunDurationMs"))
    runtime = duration / 1000 if duration is not None else None
    requests = value("cloud_window_requests", "count")
    latency_samples = value("cloud_window_latency", "count")
    counts = {key: value(f"cloud_outcomes{{outcome:{key}}}", "count") for key in CATEGORIES}
    classified = requests is not None and sum(value or 0 for value in counts.values()) == requests
    if classified:
        counts = {key: value or 0 for key, value in counts.items()}
    unexpected = value("cloud_window_unexpected", "count") if classified else None
    started = value("cloud_iterations_started", "count")
    completed = value("iterations", "count")
    scenario_start = value("cloud_scenario_start_ms", "value")
    start_ms = scenario_start + offset_seconds * 1000 if scenario_start is not None else None
    window_runtime = max(0, runtime - offset_seconds) if runtime is not None else None
    window_seconds = seconds if seconds is not None else runtime
    valid_window = bool(window_seconds and window_runtime is not None and (seconds is None or window_runtime >= seconds - .1))
    timeline = []
    if valid_window and seconds and "cloud_window_requests{bucket:0}" in metrics:
        for bucket in range(seconds // 5):
            count = value(f"cloud_window_requests{{bucket:{bucket}}}", "count")
            errors = value(f"cloud_window_unexpected{{bucket:{bucket}}}", "count")
            trend = f"cloud_window_latency{{bucket:{bucket}}}"
            points = value(trend, "count")
            timeline.append({"t": (bucket + 1) * 5, "requests": count or 0, "errors": errors or 0,
                             "rps": (count or 0) / 5, **{key: value(trend, stat) if points else None
                             for key, stat in (("p50", "p(50)"), ("p95", "p(95)"), ("p99", "p(99)"))}})
        if sum(bucket["requests"] for bucket in timeline) != requests or sum(bucket["errors"] for bucket in timeline) != unexpected:
            timeline = []
    return {"duration_seconds": window_seconds if valid_window else None, "k6_runtime_seconds": runtime,
            "scenario_started_ms": scenario_start,
            "window_started_ms": start_ms,
            "window_ended_ms": start_ms + window_seconds * 1000 if start_ms is not None and valid_window else None,
            "observed_window_ended_ms": scenario_start + min(offset_seconds + window_seconds, runtime) * 1000
            if scenario_start is not None and runtime is not None and window_seconds else None,
            "requests": requests, "rps": requests / window_seconds if valid_window and requests is not None else None,
            "successful_reads": {route: {"count": value(metric, "count"),
                                  "per_second": value(metric, "count") / window_seconds if valid_window else None,
                                  **{key: value(metric, stat) for key, stat in (("p95_ms", "p(95)"), ("p99_ms", "p(99)"))}}
                                 for route in ("catalog", "sections", "schedule", "own_enrollments", "me")
                                 if value(metric := f"cloud_successful_read{{route:{route}}}", "count")},
            **{key: value("cloud_window_latency", stat) if latency_samples else None
               for key, stat in (("p50_ms", "p(50)"), ("p95_ms", "p(95)"), ("p99_ms", "p(99)"))},
            "p95_blocked_ms": value("http_req_blocked", "p(95)"),
            "p95_connecting_ms": value("http_req_connecting", "p(95)"),
            "p95_waiting_ms": value("http_req_waiting", "p(95)"),
            "errors": unexpected, "error_rate": unexpected / requests if unexpected is not None and requests else None,
            "outcomes": counts, "timeline": timeline, "total_requests": value("http_reqs", "count"),
            "started_iterations": started, "offered_iterations": value("cloud_window_iterations_started", "count"),
            "completed_journeys": completed, "observed_vus_peak": value("vus", "max"),
            "interrupted_iterations": max(started - completed, 0) if started is not None and completed is not None else None,
            "dropped_iterations": value("dropped_iterations", "count"),
            "all_unexpected": (value("cloud_all_unexpected", "count") or 0) if summary and started else None,
            "enrollments": value("cloud_enrollments", "count"), "withdrawals": value("cloud_withdrawals", "count"),
            "section_full": value("cloud_section_full", "count"), "login_successes": value("cloud_logins", "count")}


def chart_samples(text):
    return "".join(line + "\n" for line in text.splitlines() if line.startswith(("T\t", "P\t"))
                   or (line.startswith("M\t") and (match := PROM_LINE.fullmatch(line[2:]))
                       and match[1] in TIMELINE_METRICS))


def read_samples(text):
    frames = []
    for line in text.splitlines():
        kind, _, value = line.partition("\t")
        if kind == "T" and value.isdecimal():
            frames.append({"epoch_ms": int(value), "prom": {}, "pg": None})
        elif frames and kind == "M" and (match := PROM_LINE.fullmatch(value)):
            try:
                val = number(float(match[2]))
            except ValueError:
                continue
            if val is not None:
                metrics = frames[-1]["prom"]
                name = match[1]
                if name.startswith("hikaricp_connections"):
                    pool = re.search(r'\bpool="(sdu-runtime|sdu-security)"', value)
                    if pool:
                        pool_metric = f"{name}:{pool[1]}"
                        metrics[pool_metric] = metrics.get(pool_metric, 0) + val
                if name in ("session_jdbc_seconds_count", "session_jdbc_seconds_sum"):
                    operation = re.search(r'\boperation="(read|write)"', value)
                    stage = re.search(r'\bstage="(acquire|hold|total)"', value)
                    if not operation or not stage:
                        continue
                    name = f"session_jdbc_{operation[1]}_{stage[1]}_seconds_{name.rsplit('_', 1)[1]}"
                if name.startswith("session_redis_"):
                    operation = re.search(r'\boperation="(read|write|delete)"', value)
                    if not operation:
                        continue
                    name = name.replace("session_redis_", f"session_redis_{operation[1]}_", 1)
                if name.endswith("_bucket"):
                    bound = re.search(r'\ble="([0-9.eE+-]+)"', value)
                    if not bound:
                        continue
                    name += ":" + bound[1]
                metrics[name] = metrics.get(name, 0) + val
        elif frames and kind == "P":
            try:
                pg = json.loads(value)
                if (isinstance(pg, dict) and isinstance(pg.get("waits"), dict)
                        and all(number(pg.get(key)) is not None for key in ("connections", "active"))
                        and all(number(count) is not None for count in pg["waits"].values())):
                    frames[-1]["pg"] = pg
            except ValueError:
                continue
    return frames


def resource_summary(frames, metrics):
    start, end = metrics.get("window_started_ms"), metrics.get("window_ended_ms")
    measured = [frame for frame in frames if start <= frame["epoch_ms"] <= end] if start is not None and end is not None else []
    prom = [frame["prom"] for frame in measured if frame["prom"]]
    pg = [frame["pg"] for frame in measured if frame["pg"] is not None]

    def values(key):
        return [sample[key] for sample in prom if key in sample]

    def peak(key, scale=1):
        points = values(key)
        return max(points) * scale if points else None

    def timer_delta(metric, suffix=""):
        count_key, sum_key = metric + "_count" + suffix, metric + "_sum" + suffix
        pairs = [(sample[count_key], sample[sum_key]) for sample in prom if count_key in sample and sum_key in sample]
        if len(pairs) < 2 or any(c2 < c1 or s2 < s1 for (c1, s1), (c2, s2) in zip(pairs, pairs[1:])):
            return None
        count = pairs[-1][0] - pairs[0][0]
        total_ms = (pairs[-1][1] - pairs[0][1]) * 1000
        return {"count": int(count), "total_ms": total_ms, "mean_ms": total_ms / count} if count > 0 else None

    def mean_delta(metric):
        return (timer_delta(metric) or {}).get("mean_ms")

    def counter_delta(metric):
        points = values(metric)
        return points[-1] - points[0] if len(points) >= 2 and all(a <= b for a, b in zip(points, points[1:])) else None

    stages = ("idempotency_claim", "student_lock_wait", "academic_checks", "section_lock_wait",
              "decision_clock", "decision_window", "withdraw_recheck", "seat_write", "episode_write",
              "audit_write", "idempotency_complete", "commit", "section_lock_hold", "transaction", "admission_wait",
              "connection_begin_enroll", "connection_begin_withdraw", "connection_transaction_enroll", "connection_transaction_withdraw")
    breakdown = {stage: timer_delta(f"enrollment_{stage}_seconds") for stage in stages}

    waits = {}
    for snapshot in pg:
        for label, count in snapshot["waits"].items():
            waits[label] = max(waits.get(label, 0), count)
    lock_counts = [sum(count for label, count in snapshot["waits"].items() if label.startswith("Lock:")) for snapshot in pg]
    java_cpu = []
    for left, right in zip(measured, measured[1:]):
        a, b = left["prom"], right["prom"]
        frequency = a.get("sdu_java_ticks_per_second")
        if (frequency and frequency == b.get("sdu_java_ticks_per_second") and right["epoch_ms"] > left["epoch_ms"]
                and "sdu_java_cpu_ticks_total" in a and "sdu_java_cpu_ticks_total" in b
                and b["sdu_java_cpu_ticks_total"] >= a["sdu_java_cpu_ticks_total"]):
            java_cpu.append((b["sdu_java_cpu_ticks_total"] - a["sdu_java_cpu_ticks_total"]) / frequency
                            * 1000 / (right["epoch_ms"] - left["epoch_ms"]))
    throttled_us = counter_delta("sdu_cgroup_throttled_usec")
    session_jdbc = {operation: {stage: timer_delta(f"session_jdbc_{operation}_{stage}_seconds")
                               for stage in ("acquire", "hold", "total")} for operation in ("read", "write")}
    def histogram(metric):
        buckets = sorted((float(key.partition(":")[2]), counter_delta(key))
                         for key in set().union(*prom) if key.startswith(metric + "_bucket:"))
        if not buckets or any(count is None for _, count in buckets) or not buckets[-1][1]:
            return {}
        total = counter_delta(metric + "_count") or buckets[-1][1]
        return {f"p{int(q * 100)}_ms": next((upper * 1000 for upper, count in buckets if count >= total * q), None)
                for q in (.50, .95, .99)}

    redis = None
    if values("sdu_redis_used_memory"):
        redis = {key: peak("sdu_redis_" + key, scale) for key, scale in (
            ("instantaneous_ops_per_sec", 1), ("connected_clients", 1), ("used_memory", 1), ("used_memory_rss", 1))}
        redis.update({key: counter_delta("sdu_redis_" + key) for key in (
            "keyspace_hits", "keyspace_misses", "evicted_keys", "expired_keys", "rejected_connections",
            "total_error_replies", "total_commands_processed", "used_cpu_user", "used_cpu_sys")})
        redis["operations"] = {operation: {**(timer_delta(f"session_redis_{operation}_seconds") or {}),
                                           **histogram(f"session_redis_{operation}_seconds"),
                                           "errors": counter_delta(f"session_redis_{operation}_errors_total")}
                               for operation in ("read", "write", "delete")}
        redis["commands"] = histogram("lettuce_command_completion_seconds")
    hikari_acquisition = timer_delta("hikaricp_connections_acquire_seconds")
    hikari_usage = timer_delta("hikaricp_connections_usage_seconds")
    hikari_pools = {pool: {"max_connections": peak(f"hikaricp_connections_max:{pool}"),
                           "active_peak": peak(f"hikaricp_connections_active:{pool}"),
                           "pending_peak": peak(f"hikaricp_connections_pending:{pool}"),
                           "acquisition": timer_delta("hikaricp_connections_acquire_seconds", f":{pool}"),
                           "usage": timer_delta("hikaricp_connections_usage_seconds", f":{pool}"),
                           "timeouts": counter_delta(f"hikaricp_connections_timeout_total:{pool}")}
                    for pool in ("sdu-runtime", "sdu-security") if values(f"hikaricp_connections_max:{pool}")}
    return {"prometheus_samples": len(prom), "postgres_samples": len(pg),
            "redis": redis,
            "tomcat_busy_peak": peak("tomcat_threads_busy_threads"),
            "tomcat_threads_max": peak("tomcat_threads_config_max_threads"),
            "hikari_idle_min": min(values("hikaricp_connections_idle"), default=None),
            "session_jdbc": session_jdbc if any(any(timers.values()) for timers in session_jdbc.values()) else None,
            "jvm_gc_pauses": counter_delta("jvm_gc_pause_seconds_count"),
            "jvm_gc_pause_seconds": counter_delta("jvm_gc_pause_seconds_sum"),
            "cpu_throttled_seconds": throttled_us / 1e6 if throttled_us is not None else None,
            "transaction_breakdown": breakdown if any(breakdown.values()) else None,
            "sample_span_seconds": (measured[-1]["epoch_ms"] - measured[0]["epoch_ms"]) / 1000 if len(measured) >= 2 else None,
            "spring_cpu_peak_percent": peak("process_cpu_usage", 100),
            "spring_cpu_samples_ge_80_percent": sum(value >= .8 for value in values("process_cpu_usage")) if values("process_cpu_usage") else None,
            "jvm_heap_peak_mib": peak("jvm_memory_used_bytes", 1 / 2**20),
            "java_rss_peak_mib": peak("sdu_java_rss_kib", 1 / 1024),
            "java_cpu_peak_cores": max(java_cpu, default=None),
            "backend_instances_observed_min": min(values("sdu_backend_instances_up"), default=None),
            "hikari_active_peak": peak("hikaricp_connections_active"),
            "hikari_pending_peak": peak("hikaricp_connections_pending"),
            "hikari_acquisition": hikari_acquisition,
            "hikari_usage": hikari_usage,
            "hikari_pools": hikari_pools or None,
            "hikari_acquisition_mean_ms": (hikari_acquisition or {}).get("mean_ms"),
            "hikari_usage_mean_ms": (hikari_usage or {}).get("mean_ms"),
            "section_lock_wait_mean_ms": mean_delta("enrollment_section_lock_wait_seconds"),
            "section_lock_hold_mean_ms": mean_delta("enrollment_section_lock_hold_seconds"),
            "student_lock_wait_mean_ms": mean_delta("enrollment_student_lock_wait_seconds"),
            "commit_mean_ms": mean_delta("enrollment_commit_seconds"),
            "transaction_mean_ms": mean_delta("enrollment_transaction_seconds"),
            "postgres_connections_peak": max((snapshot["connections"] for snapshot in pg), default=None),
            "postgres_active_peak": max((snapshot["active"] for snapshot in pg), default=None),
            "postgres_lock_waits_peak": max(lock_counts, default=None), "postgres_wait_events": waits if pg else None}


def checkouts_per_api_request(frames, window, request_samples):
    """Align scrape counter deltas with the nearest per-second API request counters."""
    first, last = window.get("window_started_ms"), window.get("window_ended_ms")
    if first is None or last is None or not request_samples:
        return None
    key = "hikaricp_connections_acquire_seconds_count"
    points = [(frame["epoch_ms"], frame["prom"][key]) for frame in frames
              if first <= frame["epoch_ms"] <= last and key in frame["prom"]]
    if len(points) < 2 or any(a[1] > b[1] for a, b in zip(points, points[1:])):
        return None
    left, right = (min(request_samples, key=lambda sample: abs(sample[0] - point[0]))
                   for point in (points[0], points[-1]))
    if max(abs(left[0] - points[0][0]), abs(right[0] - points[-1][0])) > 1500 or right[1] <= left[1]:
        return None
    return (points[-1][1] - points[0][1]) / (right[1] - left[1])
