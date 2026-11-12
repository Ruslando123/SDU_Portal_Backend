"""Bounded private HTTP path sampler. Records only numerical runtime evidence."""

import csv
import json
import os
from pathlib import Path
import socket
import ssl
import time

WORK = Path("/tmp/sdu-cloud")
PORTS = {8080, 8081, 8082, 8083, 8443}
TCP_COUNTERS = {"ListenOverflows", "ListenDrops", "TCPBacklogDrop", "TCPTimeouts", "TCPAbortOnMemory",
                "TCPReqQFullDoCookies", "TCPReqQFullDrop", "RetransSegs", "AttemptFails", "EstabResets"}
HA_FIELDS = {"type", "qcur", "qmax", "qlimit", "scur", "smax", "slim", "stot", "econ", "eresp", "connect",
             "reuse", "qtime", "ctime", "rtime", "ttime", "srv_icur", "idle_conn_cur", "safe_conn_cur", "used_conn_cur"}
HA_INFO = {"Maxsock", "Maxconn", "Ulimit-n", "CurrConns", "CumConns", "SslBackendKeyRate", "SslBackendMaxKeyRate",
           "Idle_pct", "Run_queue", "PoolAlloc_MB", "PoolUsed_MB"}


def text(path):
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def pid_sample(role):
    raw = text(WORK / f"{role}.pid").strip()
    if not raw.isdecimal():
        return None
    path = Path("/proc") / raw
    stat = text(path / "stat").rpartition(") ")[2].split()
    sched = text(path / "schedstat").split()
    nofile = next((line.split()[3:5] for line in text(path / "limits").splitlines() if line.startswith("Max open files")), None)
    try:
        descriptors = len(list((path / "fd").iterdir()))
    except OSError:
        descriptors = None
    return {"cpu_ticks": sum(int(value) for value in stat[11:13]) if len(stat) >= 13 else None,
            "scheduler_run_ns": int(sched[0]) if len(sched) == 3 else None,
            "scheduler_wait_ns": int(sched[1]) if len(sched) == 3 else None,
            "fd": descriptors, "nofile": nofile}


def runtime(command):
    try:
        with socket.socket(socket.AF_UNIX) as conn:
            conn.settimeout(.2)
            conn.connect(str(WORK / "edge-runtime/haproxy.sock"))
            conn.sendall(command.encode() + b"\n")
            conn.shutdown(socket.SHUT_WR)
            return conn.makefile().read(32768)
    except OSError:
        return ""


def haproxy_stats():
    raw = runtime("show stat")
    result = {}
    for row in csv.DictReader(raw.lstrip("# ").splitlines()):
        role = {"FRONTEND": "frontend", "BACKEND": "backend", "localhost": "server", "app1": "server"}.get(row.get("svname"))
        if role:
            result[role] = {key: int(row[key]) for key in HA_FIELDS if (row.get(key) or "").isdecimal()}
    if result:
        info = (line.split(": ", 1) for line in runtime("show info").splitlines() if ": " in line)
        result["info"] = {key: int(value) for key, value in info if key in HA_INFO and value.isdecimal()}
    return result or None


def tcp_stats():
    counters, sockets = {}, {}
    for path in ("/proc/net/netstat", "/proc/net/snmp"):
        lines = text(path).splitlines()
        for columns, values in zip(lines[::2], lines[1::2]):
            counters.update({key: int(value) for key, value in zip(columns.split(), values.split())
                             if key in TCP_COUNTERS and value.isdecimal()})
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        for line in text(path).splitlines()[1:]:
            fields = line.split()
            if len(fields) < 5 or ":" not in fields[1]:
                continue
            port = int(fields[1].split(":")[-1], 16)
            if port not in PORTS:
                continue
            state = fields[3]
            entry = sockets.setdefault(str(port), {"states": {}, "listen_rx_queue": None})
            entry["states"][state] = entry["states"].get(state, 0) + 1
            if state == "0A":
                entry["listen_rx_queue"] = int(fields[4].split(":")[1], 16)
    return counters or None, sockets or None


def active_probe(context):
    start = time.monotonic()
    try:
        with socket.create_connection(("127.0.0.1", 8443), timeout=.8) as raw:
            connected = time.monotonic()
            try:
                with context.wrap_socket(raw, server_hostname="localhost"):
                    return {"tcp_ms": (connected - start) * 1000, "tls_ms": (time.monotonic() - connected) * 1000}
            except OSError as failure:
                return {"tcp_ms": (connected - start) * 1000, "tls_error": type(failure).__name__}
    except OSError as failure:
        return {"tcp_error": type(failure).__name__}


def main():
    context = ssl.create_default_context(cafile=str(WORK / "http-ca.crt"))
    for _ in range(1150):
        start = time.monotonic()
        epoch = time.time_ns() // 1_000_000
        counters, sockets = tcp_stats()
        throttle = text("/sys/fs/cgroup/cpu.stat") or text("/sys/fs/cgroup/cpu/cpu.stat")
        row = {"epoch_ms": epoch, "probe": active_probe(context), "haproxy": haproxy_stats(),
               "tcp_counters": counters, "sockets": sockets, "ticks_per_second": os.sysconf("SC_CLK_TCK"),
               "cpu_stat": {key: int(value) for key, value in (line.split() for line in throttle.splitlines())},
               "processes": {role: pid_sample(role) for role in ("edge-gateway", "haproxy", "node-gateway", "java")}}
        if os.environ.get("SDU_DIAGNOSTIC_TOMCAT_THREADS"):
            row["cpu_pressure"] = {
                "cgroup": text("/sys/fs/cgroup/cpu.pressure").strip() or None,
                "proc": text("/proc/pressure/cpu").strip() or None,
            }
        print(json.dumps(row, separators=(",", ":")), flush=True)
        time.sleep(max(0, 1 - (time.monotonic() - start)))


if __name__ == "__main__":
    main()
