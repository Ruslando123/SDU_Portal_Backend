"""Attach Modal-reported metered usage to stopped runs by exact App ID and run tag."""
import argparse
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
import json
import re

from smoke import RUNS, cli_json, now, save_json, verify_cleanup

RUN_ID = re.compile(r"modal-\d{8}T\d{6}Z-[a-f0-9]{6}\Z")


def attributed(rows, report):
    app_ids = set()
    resources = defaultdict(Decimal)
    for row in rows:
        tags = row.get("tags", {})
        if isinstance(tags, str):
            tags = json.loads(tags)
        if not isinstance(tags, dict) or tags.get("sdu_run") != report["id"]:
            continue
        app_id = row.get("object_id", "")
        description = "sdu-memory-rabbit-load" if report.get("kind") == "memory-rabbit-load" else "sdu-memory-rabbit-smoke" if report.get("kind") == "memory-rabbit" else "sdu-phase8c-benchmark" if report.get("kind") == "phase8c" else "sdu-phase8b-smoke"
        if row.get("description") != description or not app_id.startswith("ap-"):
            return None
        if report.get("app_id") and app_id != report["app_id"]:
            return None
        cost = Decimal(str(row["cost"]))
        if not cost.is_finite() or cost < 0:
            return None
        resources[row["resource"]] += cost
        app_ids.add(app_id)
    if len(app_ids) != 1:
        return None
    return app_ids.pop(), dict(resources)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, required=True, help="UTC report date (YYYY-MM-DD)")
    parser.add_argument("--run-id", action="append", default=[], help="Limit updates to these exact run IDs")
    args = parser.parse_args()
    if any(not RUN_ID.fullmatch(run_id) for run_id in args.run_id):
        parser.error("Invalid Modal run ID")
    day = args.date
    candidates = []
    last_day = day
    for path in sorted(RUNS.glob("*/report.json")):
        if args.run_id and path.parent.name not in args.run_id:
            continue
        report = json.loads(path.read_text())
        if (not RUN_ID.fullmatch(report.get("id", "")) or not report["started_at"].startswith(day.isoformat())
                or (report["status"] == "running" and not report.get("finished_at"))):
            continue
        candidates.append((path, report))
        if report.get("finished_at"):
            last_day = max(last_day, date.fromisoformat(report["finished_at"][:10]))
    if not candidates:
        return
    rows = cli_json("billing", "report", "--start", day.isoformat(), "--end", (last_day + timedelta(days=1)).isoformat(),
                    "-r", "h", "--show-resources", "--tag-names", "sdu_run")
    for path, report in candidates:
        match = attributed(rows, report)
        if match is None:
            print(f"{report['id']} no attributable billing rows yet")
            continue
        app_id, resources = match
        total = sum(resources.values())
        serialized = {key: str(value) for key, value in resources.items()}
        cost = report["cost"]
        if (report.get("app_id") == app_id and (report.get("cleanup") or {}).get("verified")
                and cost.get("actual_source") == "modal-billing-report"
                and cost.get("actual_resources_usd") == serialized):
            print(f"{report['id']} metered USD {total} before credits")
            continue
        report["app_id"] = app_id
        if not (report.get("cleanup") or {}).get("verified"):
            report["cleanup"] = verify_cleanup(app_id)
            if report.get("evidence_volume_name") and report.get("evidence_volume_cleanup_verified") is not True:
                report["cleanup"]["verified"] = False
            if report["cleanup"]["verified"]:
                report.pop("cleanup_warning", None)
        cost.update(actual_usd=float(total), actual_source="modal-billing-report",
                    actual_recorded_at=now(), actual_resources_usd=serialized)
        save_json(path, report)
        print(f"{report['id']} metered USD {total} before credits")


if __name__ == "__main__":
    main()
