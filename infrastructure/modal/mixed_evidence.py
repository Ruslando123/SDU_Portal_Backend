"""Reconcile synthetic mutation attempts with committed idempotency and audit rows."""
from collections import Counter, defaultdict
import gzip
import re

from bench_evidence import TRACE_ID

UUID = re.compile(TRACE_ID)
MUTATIONS = {"enroll": "ENROLL", "withdraw": "WITHDRAW"}
TRACE_PHASES = {"10000000": "warmup", "20000000": "measure", "30000000": "warmup-recovery", "40000000": "measure-recovery"}
def database_ledger(row_limit):
    return f"""COPY (
    SELECT r.principal_id, r.operation_type, r.idempotency_key, r.target_id,
           r.operation_id, r.state, r.response_status, r.response_code,
           a.correlation_id, a.enrollment_id, e.status, a.xmin::text,
           floor(extract(epoch FROM pg_xact_commit_timestamp(a.xmin))*1000)::bigint,
           floor(extract(epoch FROM r.created_at)*1000)::bigint
    FROM operations.idempotency_records r
    LEFT JOIN audit.audit_events a ON a.operation_id=r.operation_id
    LEFT JOIN enrollment.enrollments e ON e.enrollment_id=a.enrollment_id
    ORDER BY r.operation_id, a.audit_id
    LIMIT {row_limit + 1}
) TO STDOUT"""


def parse_client_ledger(payload):
    starts, results = {}, {}
    for line in gzip.decompress(payload).decode("ascii").splitlines():
        prefix, trace, route, *fields = line.split()
        if not UUID.fullmatch(trace):
            raise ValueError("Invalid request correlation")
        if prefix == "SDU_START":
            key, attempt, principal, target, started = fields
            if route not in MUTATIONS or any(not UUID.fullmatch(value) for value in (key, principal, target)):
                raise ValueError("Invalid mutation identity")
            if trace in starts or attempt not in tuple(map(str, range(1, 9))):
                raise ValueError("Duplicate or invalid mutation attempt")
            starts[trace] = dict(trace=trace, route=route, key=key, attempt=int(attempt),
                                 principal=principal, target=target, started_ms=int(started))
        elif prefix == "SDU_RESULT":
            status, kind, source, started, ended, code, returned_trace, unexpected, *extra = fields
            enrollment = extra[0] if extra else "-"
            if trace in results or unexpected not in ("0", "1"):
                raise ValueError("Duplicate or invalid client result")
            if len(extra) > 1 or (enrollment != "-" and not UUID.fullmatch(enrollment)):
                raise ValueError("Invalid acknowledged enrollment identity")
            results[trace] = dict(trace=trace, route=route, status=int(status), outcome=kind, source=source,
                                  started_ms=int(started), ended_ms=int(ended), code=code,
                                  response_correlation=returned_trace, unexpected=unexpected == "1",
                                  enrollment=enrollment, phase=TRACE_PHASES.get(trace.split("-", 1)[0], "measure"),
                                  method={"enroll": "POST", "withdraw": "DELETE"}.get(route, "GET"))
        else:
            raise ValueError("Unknown ledger event")
    if any(row["route"] in MUTATIONS and trace not in starts for trace, row in results.items()):
        raise ValueError("Mutation result without an intent")
    for trace, start in starts.items():
        if trace in results and any(start[k] != results[trace][k] for k in ("started_ms", "route")):
            raise ValueError("Mutation intent and result disagree")
    return starts, results


def reconcile(payloads, database):
    starts, results = {}, {}
    for payload in payloads:
        phase_starts, phase_results = parse_client_ledger(payload)
        if starts.keys() & phase_starts.keys() or results.keys() & phase_results.keys():
            raise ValueError("Duplicate correlations between phases")
        starts.update(phase_starts)
        results.update(phase_results)
    operations = defaultdict(list)
    for line in database.splitlines():
        principal, operation, key, target, op_id, state, status, code, correlation, episode, final, xid, commit, *claim = line.split("\t")
        operations[principal, operation, key].append(dict(target=target, operation_id=op_id, state=state,
            status=None if status == r"\N" else int(status), code=code, correlation=correlation,
            enrollment=episode, final_enrollment_state=final, transaction_xid=None if xid == r"\N" else xid,
            commit_ms=None if commit == r"\N" else int(commit), claimed_ms=int(claim[0]) if claim else None))

    groups = defaultdict(list)
    for start in starts.values():
        groups[start["principal"], MUTATIONS[start["route"]], start["key"]].append(start)
    findings = []
    inconsistent = 0
    unresolved_timeouts = 0
    dispositions = Counter()
    timeout_boundaries = Counter()
    for identity, group in groups.items():
        group.sort(key=lambda row: row["attempt"])
        traces = {row["trace"] for row in group}
        rows = operations.get(identity, [])
        durable = rows[0] if len(rows) == 1 else None
        valid = len(rows) <= 1 and [row["attempt"] for row in group] == list(range(1, len(group) + 1))
        if durable:
            valid &= (durable["state"] == "COMPLETED" and durable["correlation"] in traces
                      and durable["commit_ms"] is not None and all(row["target"] == durable["target"] for row in group))
        attempts = []
        for start in group:
            result = results.get(start["trace"])
            boundary = None
            if result and result["outcome"] == "timeout":
                if durable is None:
                    boundary = "no_durable_commit"
                elif durable["correlation"] != start["trace"]:
                    boundary = "committed_by_other_attempt"
                else:
                    offset = durable["commit_ms"] - result["ended_ms"] if durable["commit_ms"] is not None else 0
                    # Host clock skew is not measured tightly enough to classify a near tie.
                    boundary = ("timeout_before_commit" if offset > 100 else "timeout_after_commit" if offset < -100
                                else "clock_boundary_uncertain")
                unresolved_timeouts += boundary == "clock_boundary_uncertain"
                timeout_boundaries[boundary] += 1
            if result and result["outcome"] in ("success", "business_denial"):
                valid &= bool(durable and durable["status"] == result["status"] and durable["code"] == result["code"]
                              and durable["correlation"] == result["response_correlation"]
                              and (result["enrollment"] == "-" or result["enrollment"] == durable["enrollment"]))
            attempts.append({**start, "response": result, "timeout_boundary": boundary,
                             "committed_this_attempt": bool(durable and durable["correlation"] == start["trace"])})
        inconsistent += not valid
        acknowledged = any(attempt["response"] and attempt["response"]["outcome"] in ("success", "business_denial")
                           for attempt in attempts)
        disposition = "no_durable_commit" if durable is None else "acknowledged" if acknowledged else "committed_without_ack"
        dispositions[disposition] += 1
        findings.append({"operation": identity[1], "attempts": attempts, "durable": durable,
                         "disposition": disposition, "consistent": bool(valid)})
    unexpected = [row for row in results.values() if row["unexpected"]]
    summary = {"intents": len(groups), "attempts": len(starts), "retries": sum(row["attempt"] == 2 for row in starts.values()),
               "unexpected": len(unexpected), "unexpected_by_phase": dict(Counter(row["phase"] for row in unexpected)),
               "durable_operations": sum(len(rows) for rows in operations.values()),
               "unobserved_durable": len(operations.keys() - groups.keys()), "inconsistent": inconsistent,
               "dispositions": dict(dispositions),
               "timeout_boundaries": dict(timeout_boundaries),
               "uncertain_commit_boundaries": unresolved_timeouts,
               "missing_mutation_responses": len(starts.keys() - results.keys())}
    return {"summary": summary, "unexpected": unexpected, "operations": findings}


def _latency(values):
    samples = sorted(values)

    def quantile(p):
        index = (len(samples) - 1) * p
        low = int(index)
        return samples[low] + (samples[min(low + 1, len(samples) - 1)] - samples[low]) * (index - low)

    return {f"p{p}_ms": quantile(p / 100) if samples else None for p in (50, 95, 99)}


def business_results(evidence, warmup, measure, business_phase="measure"):
    """Aggregate unique acknowledged business outcomes and keep recovery outside timed throughput."""
    first, last, seconds = (measure.get(k) for k in ("window_started_ms", "window_ended_ms", "duration_seconds"))
    if first is None or last is None:
        seconds = None
    groups = {operation: [] for operation in MUTATIONS.values()}
    claim_spans = {operation: [] for operation in groups}
    boundaries = {}
    for row in evidence["operations"]:
        durable = row["durable"]
        for attempt in row["attempts"]:
            if attempt["response"] and attempt["response"]["unexpected"]:
                boundaries[attempt["trace"]] = (attempt["timeout_boundary"] or
                    ("committed_same_attempt" if attempt["committed_this_attempt"] else
                     "committed_by_other_attempt" if durable else "no_durable_commit"))
        if not row["consistent"] or not durable or not seconds:
            continue
        operation = row["operation"]
        accepted = next((attempt["response"] for attempt in row["attempts"]
                         if attempt["response"] and attempt["response"]["outcome"] == "success"
                         and attempt["response"]["phase"] == business_phase
                         and first <= attempt["response"]["ended_ms"] < last), None)
        if accepted:
            groups[operation].append(accepted["ended_ms"] - row["attempts"][0]["started_ms"])
        claim, commit = durable.get("claimed_ms"), durable["commit_ms"]
        if claim is not None and commit is not None and first <= claim <= commit < last:
            claim_spans[operation].append(commit - claim)

    failures = Counter()
    for row in evidence["unexpected"]:
        phase = row["phase"]
        if phase == "warmup" and warmup.get("window_started_ms") is not None and row["ended_ms"] < warmup["window_started_ms"]:
            phase = "ramp"
        elif phase == "measure" and last is not None and row["ended_ms"] >= last:
            phase = "measure-drain"
        failures[phase, row["route"], row["status"], row["outcome"], row["code"], boundaries.get(row["trace"], "read_or_unknown")] += 1

    return {
        "mutations": {operation.lower(): {
            "acknowledged": len(samples) if seconds else None,
            "per_second": len(samples) / seconds if seconds else None,
            **_latency(samples), "claim_to_commit": {"count": len(claim_spans[operation]), **_latency(claim_spans[operation])},
        } for operation, samples in groups.items()},
        "failures": [dict(zip(("phase", "route", "status", "outcome", "code", "transaction_evidence", "count"), (*key, count)))
                     for key, count in sorted(failures.items())],
    }


def historical_summary(report, traces):
    traces = [{**row, "failure_boundary": failure_boundary(row)} for row in traces]
    groups = Counter((row["phase"], row["status"], row["route"], row["source"]) for row in traces)
    count = sum((report.get(key) or {}).get("all_unexpected") or 0 for key in ("warmup_metrics", "metrics"))
    return {"id": report["id"], "unexpected_all_phases": count, "traced": len(traces),
            "missing_trace_at_least": max(0, count - len(traces)), "counter_trace_difference": count - len(traces),
            "groups": [{"phase": phase, "status": status, "route": route, "source": source, "count": n}
                       for (phase, status, route, source), n in sorted(groups.items())], "traces": traces}


def failure_boundary(row):
    if row.get("source") == "backend":
        return "backend_response"
    if row.get("source") == "gateway-error":
        return "gateway_reported_upstream_failure"
    if any(event["event"] == "ingress" for event in row.get("local_events", [])):
        return "unattributed_after_gateway_ingress"
    return "no_matching_gateway_ingress"
