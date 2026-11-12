"""Summarize only valid PgBouncer samples from the selected measurement window."""
import csv

DATABASE = "portal_cloud_smoke"


def pooler_summary(raw, start, end):
    if start is None or end is None:
        return None
    frames = []
    for line in raw.splitlines():
        kind, _, value = line.partition("\t")
        if kind == "T" and value.isdecimal():
            frames.append({"time": int(value), "P": [], "S": [], "M": {}})
        elif frames and kind in ("P", "S"):
            frames[-1][kind].append(value)
        elif frames and kind == "M":
            key, _, number = value.partition("\t")
            if number.isdecimal():
                frames[-1]["M"][key] = int(number)
    samples = []
    for frame in frames:
        if not start <= frame["time"] <= end:
            continue
        pools = [row for row in csv.DictReader(frame["P"]) if row.get("database") == DATABASE]
        stats = next((row for row in csv.DictReader(frame["S"]) if row.get("database") == DATABASE), None)
        if len(pools) == 1 and stats:
            samples.append((pools[0], stats, frame["M"]))
    if not samples:
        return None

    def maximum(column):
        return max((int(pool[column]) for pool, _, _ in samples if (pool.get(column) or "").isdecimal()), default=None)

    def delta(column):
        before, after = (samples[index][1].get(column) or "" for index in (0, -1))
        if len(samples) >= 2 and before.isdecimal() and after.isdecimal() and int(after) >= int(before):
            return int(after) - int(before)
        return None

    first, last = samples[0][2], samples[-1][2]
    ticks = first.get("ticks_per_second")
    cpu_seconds = ((last["cpu_ticks"] - first["cpu_ticks"]) / ticks if ticks and len(samples) >= 2
                   and "cpu_ticks" in first and "cpu_ticks" in last else None)
    rss = max((row["rss_kib"] for _, _, row in samples if "rss_kib" in row), default=None)
    return {"samples": len(samples), "clients_active_peak": maximum("cl_active"), "clients_waiting_peak": maximum("cl_waiting"),
            "servers_active_peak": maximum("sv_active"), "servers_idle_peak": maximum("sv_idle"),
            "total_transactions": delta("total_xact_count"), "total_queries": delta("total_query_count"),
            "total_wait_us": delta("total_wait_time"), "client_parses": delta("total_client_parse_count"),
            "server_parses": delta("total_server_parse_count"), "binds": delta("total_bind_count"),
            "cpu_seconds": cpu_seconds, "rss_peak_mib": rss / 1024 if rss is not None else None}
