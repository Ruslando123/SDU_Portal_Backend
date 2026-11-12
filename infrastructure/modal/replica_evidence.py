"""Windowed replication and source-use evidence from isolated PostgreSQL."""
import json


def replica_summary(raw, start, end, use_replica):
    if start is None or end is None:
        return None
    samples = []
    for line in raw.splitlines():
        try:
            epoch, primary, standby = line.split("\t")
            if not start <= int(epoch) <= end:
                continue
            primary = json.loads(primary)
            standby = json.loads(standby)
            if not isinstance(primary, dict):
                continue
            if use_replica and (not isinstance(standby, dict) or not standby.get("recovery")
                                or (primary.get("stream") or {}).get("state") != "streaming"):
                continue
            samples.append((primary, standby))
        except (ValueError, TypeError):
            continue
    if not samples:
        return None

    def calls(index):
        if len(samples) < 2 or samples[0][index] is None:
            return None
        before = samples[0][index]["catalog_calls"]
        after = samples[-1][index]["catalog_calls"]
        return after - before if after >= before else None

    def peak(key):
        return max((p["stream"][key] for p, _ in samples if p.get("stream") and p["stream"].get(key) is not None), default=None)

    return {"samples": len(samples), "primary_catalog_calls": calls(0), "replica_catalog_calls": calls(1),
            "max_lag_bytes": peak("lag_bytes"), "max_replay_lag_ms": peak("replay_lag_ms")}
