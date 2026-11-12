"""Summarize timestamped private HTTP evidence after an experiment has stopped."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re

from mixed_evidence import _latency
from smoke import save_json


def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []


def numeric_summary(values):
    if not values:
        return None
    return {"samples": len(values), "max": max(values), **_latency(values)}


def delta(rows, key):
    values = [row[key] for row in rows if row and isinstance(row.get(key), (int, float))]
    return values[-1] - values[0] if len(values) >= 2 and values[-1] >= values[0] else None


def peak(rows, key):
    return max((row[key] for row in rows if row and isinstance(row.get(key), (int, float))), default=None)


def window_summary(samples, listeners, start, stop):
    selected = [row for row in samples if start <= row["epoch_ms"] < stop]
    listener = [row for row in listeners if start <= row["epoch_ms"] < stop]
    probes = [row["probe"] for row in selected]
    processes = {}
    for role in ("edge-gateway", "haproxy", "node-gateway", "java"):
        points = [(row, row["processes"].get(role)) for row in selected if row["processes"].get(role)]
        values = [point for _, point in points]
        ticks = delta(values, "cpu_ticks")
        interval = (points[-1][0]["epoch_ms"] - points[0][0]["epoch_ms"]) / 1000 if len(points) > 1 else None
        cores = ticks / points[0][0]["ticks_per_second"] / interval if ticks is not None and interval else None
        processes[role] = {"average_cpu_cores": cores, "fd_peak": peak(values, "fd"),
                           "nofile": next((point["nofile"] for point in values if point["nofile"]), None),
                           "scheduler_wait_ns": delta(values, "scheduler_wait_ns")}
    runtime = [row["haproxy"] for row in selected if row.get("haproxy")]
    server = [row.get("server") for row in runtime]
    frontend = [row.get("frontend") for row in runtime]
    proxy_info = [row.get("info") for row in runtime]
    sockets = {port: peak([(row.get("sockets") or {}).get(port) for row in selected], "listen_rx_queue")
               for port in ("8080", "8081", "8082", "8443")}
    tcp = [row.get("tcp_counters") for row in selected]
    cpu = [row.get("cpu_stat") for row in selected]
    return {"sample_count": len(selected), "listener_sample_count": len(listener),
            "tcp_connect_ms": numeric_summary([point["tcp_ms"] for point in probes if "tcp_ms" in point]),
            "tls_handshake_ms": numeric_summary([point["tls_ms"] for point in probes if "tls_ms" in point]),
            "probe_errors": dict(Counter(point[key] for point in probes for key in ("tcp_error", "tls_error") if key in point)),
            "listen_rx_queue_peaks": sockets,
            "tcp_deltas": {key: delta(tcp, key) for key in sorted({key for row in tcp if row for key in row})},
            "cgroup_deltas": {key: delta(cpu, key) for key in sorted({key for row in cpu if row for key in row})},
            "processes": processes,
            "tls_listener": {**{key: delta(listener, key) for key in ("accepted", "closed", "client_hello", "tls_verified",
                        "tls_resumed", "http_active_transitions")},
                        "open_peak": max((row["accepted"] - row["closed"] for row in listener), default=None)},
            "haproxy": {"samples": len(runtime), "new_connections": delta(server, "connect"), "reused": delta(server, "reuse"),
                        "connect_errors": delta(server, "econ"), "response_errors": delta(server, "eresp"),
                        "active_peak": peak(server, "scur"), "server_maxconn": peak(server, "slim"),
                        "frontend_active_peak": peak(frontend, "scur"), "frontend_maxconn": peak(frontend, "slim"),
                        "runtime_info_peaks": {key: peak(proxy_info, key) for key in sorted({key for row in proxy_info if row for key in row})},
                        **{f"{key}_peak": peak(server, key) for key in ("qcur", "qmax", "qtime", "ctime", "rtime", "used_conn_cur", "idle_conn_cur")}}}


def tomcat_samples(path, start, stop):
    if not path.is_file():
        return {}
    result = {}
    active = False
    for line in path.read_text().splitlines():
        kind, _, value = line.partition("\t")
        if kind == "T":
            active = start <= int(value) < stop
        if active and kind == "M" and value.startswith("tomcat_") and "8082" in value:
            match = re.fullmatch(r"([a-z_]+)\{.*\} (\S+)", value)
            if match:
                name, number = match.groups()
                result[name] = max(result.get(name, 0), float(number))
    return result


def summarize(path, report):
    samples = read_lines(path / "http-samples.jsonl")
    listener = read_lines(path / "tls-listener-samples.jsonl")
    warm = report.get("warmup_metrics") or {}
    measured = report.get("metrics") or {}
    start, end = warm.get("scenario_started_ms"), warm.get("observed_window_ended_ms")
    if start is None or end is None:
        return {}
    windows = {"warmup_all": (start, end), "target_hold": (warm.get("window_started_ms"), end)}
    if report["workload"].get("diagnostic_only"):
        windows.update(hold_1500=(start + 36000, min(start + 51000, end)),
                       hold_2000=(start + 63000, min(start + 78000, end)))
    if measured.get("window_started_ms") is not None:
        windows["measure"] = (measured["window_started_ms"], measured["observed_window_ended_ms"])
    return {label: {**window_summary(samples, listener, begin, stop),
                    "tomcat_peak": tomcat_samples(path / "samples.tsv", begin, stop)}
            for label, (begin, stop) in windows.items() if begin is not None and stop is not None and stop > begin}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    args = parser.parse_args()
    report_path = args.run_directory / "report.json"
    report = json.loads(report_path.read_text())
    if report.get("status") == "running" or not report["workload"].get("http_experiment"):
        parser.error("An ended HTTP route experiment is required")
    diagnostics = summarize(args.run_directory, report)
    save_json(args.run_directory / "http-diagnostics.json", diagnostics)
    report["http_diagnostics"] = diagnostics
    save_json(report_path, report)


if __name__ == "__main__":
    main()
