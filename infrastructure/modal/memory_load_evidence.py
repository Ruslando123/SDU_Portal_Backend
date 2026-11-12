"""Strict HTTP, broker and SQL gates for the isolated RAM/RabbitMQ load experiment."""
import gzip
import json
import time
from pathlib import Path
from smoke import execute, query, save_json, VIOLATIONS
from bench_evidence import read_samples, phase_metrics
from benchmark_worker import warmup_gate
import mixed_evidence
import split_topology


def metric_values(text):
    values = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 2:
            name = fields[0].split('{', 1)[0]
            try: values[name] = values.get(name, 0) + float(fields[1])
            except ValueError: pass
    return values


def drain(sandbox, broker):
    deadline = time.monotonic() + 90
    observed = {}
    quiet_since = None
    while time.monotonic() < deadline:
        code, text, _ = execute(sandbox, '/tmp/sdu-cloud/gateway', 'metrics')
        observed = metric_values(text)
        if code == 0 and observed.get('portal_memory_pending') == 0 and observed.get('portal_memory_http_active') == 0:
            quiet_since = quiet_since or time.monotonic()
            if time.monotonic() - quiet_since >= 5: break
        else:
            quiet_since = None
        if observed.get('portal_memory_ready') == 0:
            break
        time.sleep(2)
    code, text, _ = execute(broker, 'gosu', 'rabbitmq', 'rabbitmqctl', '-q', 'list_queues', '-p', '/portal',
                           'name', 'messages_ready', 'messages_unacknowledged', 'durable', 'type', timeout=20)
    rows = [line.split('\t') for line in text.splitlines() if line.startswith('portal.decisions.v1\t')]
    return dict(passed=code == 0 and rows == [['portal.decisions.v1','0','0','true','quorum']]
                and observed.get('portal_memory_pending') == 0 and observed.get('portal_memory_ready') == 1
                and observed.get('portal_memory_http_active') == 0 and quiet_since is not None and time.monotonic()-quiet_since >= 5,
                queue=rows, counters=observed)


def sql_gate(database, secret, ledgers, directory, phase, queue):
    rows = query(database, ['-f','/opt/sdu/invariants.sql'], secret).splitlines()
    violations = dict(zip(VIOLATIONS, map(int, rows[:len(VIOLATIONS)]), strict=True))
    counts = json.loads(query(database, ['-c', """SELECT json_build_object(
      'active',(SELECT count(*) FROM enrollment.enrollments WHERE status='ACTIVE'),
      'durable',(SELECT count(*) FROM operations.idempotency_records),
      'audit',(SELECT count(*) FROM audit.audit_events),
      'ledger',(SELECT sum(enrolled_count) FROM sections.course_sections),
      'durability',current_setting('fsync')='on' AND current_setting('synchronous_commit')='on' AND current_setting('full_page_writes')='on')"""], secret))
    if counts['durable'] > 500000: raise RuntimeError('Unexpected decision count')
    data = split_topology.export_operations(database, secret, mixed_evidence.database_ledger(500000), limit_mib=64)
    (directory/f'{phase}-database.tsv.gz').write_bytes(gzip.compress(data.encode(), compresslevel=3))
    evidence = mixed_evidence.reconcile(ledgers, data)
    with gzip.open(directory/f'{phase}-reconciliation.json.gz', 'wt') as output:
        json.dump(evidence, output)
    summary = evidence['summary']
    checks = dict(passed=bool(ledgers) and not any(violations.values()) and counts['active'] == counts['ledger'] == 0
                  and counts['durability'] and counts['durable'] == counts['audit'] == summary['durable_operations']
                  and summary['inconsistent'] == summary['unobserved_durable'] == 0
                  and queue['passed'] and queue['counters'].get('portal_memory_confirmed_total') == counts['durable'],
                  violations=violations, counts=counts, reconciliation=summary, broker=queue)
    save_json(directory/f'{phase}-sql-gate.json', checks)
    return checks, evidence


def measurement_gate(case, payload):
    # Reuse the unchanged 30-second gate with an explicit 120-second measurement case proxy.
    class Window:
        warmup_seconds = 120
        ramp_elapsed = 0
        vus = case.vus
        diagnostic_only = False
        tomcat_threads = None
    return warmup_gate(Window(), payload)


def projection_stability(samples, metrics):
    start, end = metrics.get('window_started_ms'), metrics.get('window_ended_ms')
    keys = ('portal_memory_pending', 'portal_memory_confirmed_total', 'portal_memory_projected_total')
    frames = [f for f in read_samples(samples) if start is not None and end is not None
              and start <= f['epoch_ms'] < end and all(k in f['prom'] for k in keys)]
    if len(frames) < 3:
        return dict(passed=False, reason='No Data', samples=len(frames))
    first, last = frames[0], frames[-1]
    seconds = (last['epoch_ms'] - first['epoch_ms']) / 1000
    accepted = last['prom'][keys[1]] - first['prom'][keys[1]]
    projected = last['prom'][keys[2]] - first['prom'][keys[2]]
    growth = last['prom'][keys[0]] - first['prom'][keys[0]]
    return dict(passed=seconds >= .8 * metrics['duration_seconds'] and accepted > 0
                and projected >= .98 * accepted and growth <= 100,
                samples=len(frames), sampled_seconds=seconds, confirmed_per_second=accepted/seconds,
                projected_per_second=projected/seconds, backlog_growth=growth,
                peak_pending=max(f['prom'][keys[0]] for f in frames))
