"""Bounded post-phase HTTP recovery for incomplete synthetic registration journeys."""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import gzip
from http.client import HTTPException
import json
import re
import time
import urllib.error
import urllib.request
from uuid import uuid4

from bench_config import RECOVERY_SECONDS
from mixed_evidence import UUID, parse_client_ledger

MAX_JOURNEYS = 64
TECHNICAL_HTTP = {0, 408, 429, 500, 502, 503, 504}


def retryable(result):
    return result is None or (result["outcome"] != "gateway_limit" and
        (result["status"] in TECHNICAL_HTTP or result["code"] == "REQUEST_IN_PROGRESS"
         or result["outcome"] == "invalid_response"))


def pending_journeys(payload):
    starts, results = parse_client_ledger(payload)
    groups = defaultdict(list)
    for start in starts.values():
        groups[start["principal"], start["route"], start["key"]].append(start)
    withdrawn_targets = {row["target"] for row in starts.values() if row["route"] == "withdraw"}
    pending = []
    for group in groups.values():
        group.sort(key=lambda row: row["attempt"])
        last = group[-1]
        result = results.get(last["trace"])
        if retryable(result):
            pending.append({**last, "attempt": last["attempt"] + 1})
        elif last["route"] == "enroll" and result["outcome"] == "success" and result["enrollment"] not in withdrawn_targets:
            if result["enrollment"] == "-":
                raise ValueError("Acknowledged enrollment lacks a recovery target")
            pending.append({**last, "route": "withdraw", "target": result["enrollment"], "attempt": 1, "key": str(uuid4())})
    return sorted(pending, key=lambda row: row["started_ms"])


def recover(payload, actors, origin, headers, opener, phase, *, max_journeys=MAX_JOURNEYS, recovery_seconds=RECOVERY_SECONDS, workers=4, attempt_limit=4, withdrawals_first=False):
    if not 1 <= max_journeys <= 2500 or not 1 <= recovery_seconds <= 60:
        raise ValueError("Invalid bounded recovery policy")
    if not 1 <= workers <= 16 or not 4 <= attempt_limit <= 8:
        raise ValueError("Invalid recovery concurrency/retry limit")
    pending = pending_journeys(payload)
    if withdrawals_first: pending.sort(key=lambda row: (row["route"] != "withdraw", row["started_ms"]))
    if not pending:
        return None, {"pending": 0, "resolved": 0, "requests": 0, "replays": 0}
    by_principal = {"81" + actor["student"][2:]: actor for actor in actors}
    if any(row["principal"] not in by_principal for row in pending):
        raise ValueError("Recovery intent does not belong to a prepared synthetic session")
    deadline = time.monotonic() + recovery_seconds
    prefix = "30000000" if phase == "warmup" else "40000000"

    def journey(intent):
        # Do not construct TLS contexts for queued work after its deadline.
        if time.monotonic() >= deadline: return [], False, 0
        actor = by_principal[intent["principal"]]
        client = opener()
        lines = []
        replays = 0

        def attempt(route, target, key, number):
            nonlocal replays
            trace = prefix + str(uuid4())[8:]
            url = f"/api/v1/sections/{target}/enrollments" if route == "enroll" else f"/api/v1/enrollments/{target}"
            method = "POST" if route == "enroll" else "DELETE"
            request = urllib.request.Request(origin + url, method=method,
                headers={**headers, "Cookie": f"SESSION={actor['cookie']}", actor["header"]: actor["csrf"],
                         "Idempotency-Key": key, "X-Correlation-Id": trace})
            started = time.time_ns() // 1_000_000
            lines.append(f"SDU_START {trace} {route} {key} {number} {intent['principal']} {target} {started}")
            status, response_headers, raw, kind = 0, {}, b"", "transport"
            try:
                try:
                    response = client.open(request, timeout=min(3, max(.1, deadline - time.monotonic())))
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    status, response_headers, raw = response.code, response.headers, response.read(65_537)
            except (OSError, HTTPException) as error:
                if isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError):
                    kind = "timeout"
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeError):
                body = {}
            if not isinstance(body, dict):
                body = {}
            code = body.get("code")
            code = code if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z_]{1,63}", code) else "-"
            stored = body.get("correlationId")
            stored = stored if isinstance(stored, str) and UUID.fullmatch(stored) else "-"
            episode = body.get("enrollmentId")
            episode = episode if isinstance(episode, str) and UUID.fullmatch(episode) else "-"
            if status:
                if response_headers.get("X-Sdu-Gateway-Limit") == "1":
                    kind = "gateway_limit"
                elif route == "enroll" and status == 201 and code == "ENROLLED" and episode != "-" and body.get("sectionId") == target:
                    kind = "success"
                elif route == "withdraw" and status == 200 and code in ("WITHDRAWN", "ALREADY_WITHDRAWN") and episode == target:
                    kind = "success"
                elif status in (200, 201):
                    kind = "invalid_response"
                elif status in (403, 409) and stored != "-" and code in ("SECTION_FULL", "ALREADY_ENROLLED", "REGISTRATION_CLOSED",
                        "CREDIT_LIMIT_EXCEEDED", "SCHEDULE_CONFLICT", "PREREQUISITE_NOT_MET", "COHORT_INELIGIBLE", "ACADEMIC_INELIGIBLE"):
                    kind = "business_denial"
                else:
                    kind = "http_error"
            source = response_headers.get("X-Sdu-Benchmark-Hop", "unattributed")
            source = source if source in ("backend", "gateway-error") else "unattributed"
            if kind == "success" and response_headers.get("Idempotency-Replayed") == "true":
                replays += 1
            ended = time.time_ns() // 1_000_000
            lines.append(f"SDU_RESULT {trace} {route} {status} {kind} {source} {started} {ended} {code} {stored} {int(kind != 'success')} {episode if kind == 'success' else '-'}")
            return {"status": status, "outcome": kind, "code": code, "enrollment": episode}

        def resolve(route, target, key, number):
            result = None
            for attempt_number in range(number, min(number + 2, attempt_limit + 1)):
                if time.monotonic() >= deadline or not retryable(result):
                    break
                result = attempt(route, target, key, attempt_number)
            return result

        route = intent["route"]
        if route == "enroll" and deadline - time.monotonic() < 4:
            return lines, False, replays
        result = resolve(route, intent["target"], intent["key"], intent["attempt"])
        if route == "enroll" and result and result["outcome"] == "success":
            result = resolve("withdraw", result["enrollment"], str(uuid4()), 1)
        return lines, bool(result and (result["outcome"] == "success" or
            (route == "enroll" and result["outcome"] == "business_denial"))), replays

    with ThreadPoolExecutor(max_workers=workers) as executor:
        completed = list(executor.map(journey, pending[:max_journeys]))
    lines = [line for records, _, _ in completed for line in records]
    return gzip.compress("\n".join(lines).encode("ascii")), {
        "pending": len(pending), "resolved": sum(resolved for _, resolved, _ in completed), "requests": len(lines) // 2,
        "replays": sum(replays for _, _, replays in completed),
    }
