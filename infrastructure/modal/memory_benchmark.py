"""One bounded 10-VU correctness smoke for RAM decisions and RabbitMQ projection. No capacity claim."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import fcntl
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import time

import modal
from prepare import ASSETS, JAR, sha
from smoke import (HERE, RUNS, REGION, PG_IMAGE, PY_IMAGE, VIOLATIONS, EXPECTED, RATE_KEYS, assets_valid,
                   cli_json, execute, query, now, save_json, metrics_from, collect_logs, verify_cleanup, images)
from worker import safe_log, validate_origin
import split_topology
import private_db

RABBIT_IMAGE = "rabbitmq:4.4.0@sha256:485319d42170eab53d8a030e1cc49d95e41f5d325479fa504736db8091981eb7"
CPU, MIB, SECONDS = 4, 8192, 600
GEN_CPU, GEN_MIB, GEN_SECONDS = 1, 1024, 75
CAP = Decimal("0.50")


def cost_plan(rates):
    values = {key: Decimal(str(rates[key])) for key in RATE_KEYS}
    if any(not v.is_finite() or v <= 0 for v in values.values()):
        raise ValueError("Invalid official pricing response")
    backend = (CPU * values['cpu_hour_cost_sandbox'] + Decimal(MIB) / 1024 * values['mem_gib_hour_cost_sandbox']) * SECONDS / 3600
    generator = (GEN_CPU * values['cpu_hour_cost'] + Decimal(GEN_MIB) / 1024 * values['mem_gib_hour_cost']) * (GEN_SECONDS + 45 + 5) / 3600
    estimate = (backend + generator) * Decimal('1.75') + values['egress_gib_cost'] + Decimal('0.08')
    if estimate > CAP:
        raise ValueError("Smoke worst-case allocation exceeds USD 0.50")
    return dict(rates={k: str(v) for k, v in values.items()}, regional_multiplier=1.75,
                compute_usd=float((backend + generator) * Decimal('1.75')), build_reserve_usd=.08,
                egress_allowance_usd=float(values['egress_gib_cost']), estimate_usd=float(estimate), actual_usd=None)


def certificate(host):
    with tempfile.TemporaryDirectory(prefix='sdu-memory-pki-') as temp:
        path = Path(temp)
        (path/'extensions').write_text(f'subjectAltName=DNS:{host}\nextendedKeyUsage=serverAuth\n')
        for args in (
            ('req','-x509','-newkey','rsa:2048','-nodes','-subj','/CN=SDU Memory Smoke CA','-days','1','-keyout','ca.key','-out','ca.crt'),
            ('req','-new','-newkey','rsa:2048','-nodes','-subj',f'/CN={host}','-keyout','server.key','-out','server.csr'),
            ('x509','-req','-in','server.csr','-CA','ca.crt','-CAkey','ca.key','-set_serial','1','-days','1','-extfile','extensions','-out','server.crt'),
        ):
            subprocess.run(['openssl',*args],cwd=path,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=15)
        return tuple((path/name).read_text() for name in ('ca.crt','server.crt','server.key'))


def reconcile(database, secret, operations):
    rows = query(database, ['-f','/opt/sdu/invariants.sql'], secret).splitlines()
    violations = dict(zip(VIOLATIONS, map(int, rows[:len(VIOLATIONS)]), strict=True))
    durable = json.loads(query(database, ['-c', """SELECT coalesce(json_agg(json_build_object(
        'key', idempotency_key, 'principal', principal_id, 'type', operation_type, 'target', target_id,
        'status', response_status, 'body', response_body)), '[]'::json) FROM operations.idempotency_records"""], secret))
    indexed = {str(row['key']): row for row in durable}
    mismatches = []
    for op in operations:
        row = indexed.get(op['key'])
        principal = f"81000000-0000-4000-8000-{op['vu']:012d}"
        if (not row or row['principal'] != principal or row['status'] != op['httpStatus'] or row['body'] != op['body']
                or row['type'] != ('ENROLL' if op['method'] == 'POST' else 'WITHDRAW')
                or row['target'] != op['path'].split('/')[-2 if op['method'] == 'POST' else -1]):
            mismatches.append(op['key'])
    counts = json.loads(query(database, ['-c', """SELECT json_build_object(
      'active',(SELECT count(*) FROM enrollment.enrollments WHERE status='ACTIVE'),
      'withdrawn',(SELECT count(*) FROM enrollment.enrollments WHERE status='WITHDRAWN'),
      'audit',(SELECT count(*) FROM audit.audit_events),'durable',(SELECT count(*) FROM operations.idempotency_records),
      'ledger',(SELECT sum(enrolled_count) FROM sections.course_sections),
      'fsync',current_setting('fsync'),'sync',current_setting('synchronous_commit'),'full_page_writes',current_setting('full_page_writes'))"""], secret))
    passed = (not any(violations.values()) and not mismatches and len(operations) == 30 and len(indexed) == 20
              and sum(not op['replayed'] for op in operations) == 20 and counts['active'] == counts['ledger'] == 0
              and counts['withdrawn'] == 10 and counts['audit'] == counts['durable'] == 20
              and all(counts[key] == 'on' for key in ('fsync','sync','full_page_writes')))
    return dict(passed=passed, violations=violations, observed=counts, reconciled_http_responses=len(operations),
                unique_durable_operations=len(indexed), mismatches=mismatches, active_final=counts['active'])


def run(cost, available):
    assets_valid()
    RUNS.mkdir(parents=True, exist_ok=True)
    run_id = 'modal-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(3)
    directory = RUNS / run_id
    directory.mkdir(mode=0o700)
    values = {key: secrets.token_urlsafe(32) for key in ('DB_MIGRATION_PASSWORD','DB_RUNTIME_PASSWORD','PORTAL_METRICS_PASSWORD',
              'SDU_GATE_KEY','SDU_STUDENT_PASSWORD','PORTAL_SESSION_REDIS_PASSWORD','SDU_REDIS_OBSERVER_PASSWORD')}
    db_ca, db_cert, db_key = split_topology.certificate(sidecar=True)
    rabbit_ca, rabbit_cert, rabbit_key = certificate('broker')
    http_ca, http_cert, http_key = certificate('localhost')
    rabbit_password = secrets.token_urlsafe(32)
    database_secret = modal.Secret.from_dict({key:values[key] for key in ('DB_MIGRATION_PASSWORD','DB_RUNTIME_PASSWORD')} | {'SDU_DB_TLS_CERT': db_cert, 'SDU_DB_TLS_KEY': db_key})
    app_secret = modal.Secret.from_dict({**values, 'SDU_DB_TLS_CA': db_ca, 'SDU_RABBIT_TLS_CA': rabbit_ca,
        'PORTAL_WRITE_BEHIND_PASSWORD': rabbit_password, 'SDU_HTTP_TLS_CA': http_ca, 'SDU_HTTP_TLS_CERT': http_cert, 'SDU_HTTP_TLS_KEY': http_key})
    rabbit_secret = modal.Secret.from_dict({'SDU_RABBIT_PASSWORD':rabbit_password, 'SDU_RABBIT_TLS_CA':rabbit_ca,
                                           'SDU_RABBIT_TLS_CERT':rabbit_cert, 'SDU_RABBIT_TLS_KEY':rabbit_key})
    worker_secret = modal.Secret.from_dict({key:values[key] for key in ('SDU_GATE_KEY','SDU_STUDENT_PASSWORD')})
    redactions = [*values.values(), rabbit_password, db_key, rabbit_key, http_key]
    base = modal.Image.from_registry(PG_IMAGE).run_commands(
        "timeout 180s sh -ec 'apt-get update && apt-get install -y --no-install-recommends python3 redis-server openssl && rm -rf /var/lib/apt/lists/*'")
    backend, generator = images(base)
    for name, target in (('start-redis-session.sh','start-redis-session.sh'),('start-memory-backend.sh','start-balanced.sh')):
        backend = backend.add_local_file(HERE/name, '/opt/sdu/'+target)
    backend = backend.add_local_file(ASSETS/'haproxy-linux-amd64','/opt/sdu/haproxy')
    generator = generator.add_local_file(HERE/'memory_worker.py','/opt/sdu/memory_worker.py').add_local_file(HERE/'memory-smoke.js','/opt/sdu/memory-smoke.js')
    database_image = split_topology.image(base, baked=True)
    rabbit_image = modal.Image.from_registry(RABBIT_IMAGE).add_local_file(HERE/'start-rabbit.sh','/opt/sdu/start-rabbit.sh',copy=True)
    app = modal.App('sdu-memory-rabbit-smoke', tags={'sdu_run':run_id}, include_source=False)
    @app.function(image=generator, secrets=[worker_secret], region=REGION, routing_region=REGION,
                  cpu=(GEN_CPU,GEN_CPU), memory=(GEN_MIB,GEN_MIB), timeout=GEN_SECONDS, startup_timeout=45,
                  min_containers=0,max_containers=1,buffer_containers=0,scaledown_window=5,retries=0,
                  restrict_modal_access=True,single_use_containers=True,serialized=True,include_source=False)
    def generate(origin):
        import runpy
        return runpy.run_path('/opt/sdu/memory_worker.py')['run_smoke'](origin)
    report = dict(version=1,kind='memory-rabbit',id=run_id,status='running',stage='creating-app',started_at=now(),
        region=REGION,vus=10,cost=cost,available_credit_reported_usd=float(available),cleanup=None,metrics=None,
        workload=dict(executor='per-vu-iterations',transport='encrypted-tunnel',topology='private-db-sidecar'),
        memory_journal=dict(authority='rabbitmq-confirmed',projection='postgresql-async',single_owner=True,
                            broker_replicas=1,storage='ephemeral',host_loss_tolerant=False,confirmed=None,projected=None,reconciled=None),
        resources=dict(sandbox_cpu_limit_cores=CPU,sandbox_memory_limit_mib=MIB,generator_cpu_limit_cores=GEN_CPU,generator_memory_limit_mib=GEN_MIB),
        artifact=dict(jar_sha256=sha(JAR),postgres_image=PG_IMAGE,rabbitmq_image=RABBIT_IMAGE),
        architecture=dict(phase=9,database_path='direct',backend_instances=1,database_instances=1,load_balancer='HAProxy',resource_profile='private-sidecar'))
    path = directory/'report.json'
    save_json(path,report)
    sandbox = database = broker = None
    try:
        with app.run():
            report.update(app_id=app.app_id,stage='creating-sandbox'); save_json(path,report)
            try:
                # Force bounded image builds before starting the billed Sandbox timer.
                built_db = database_image.build(app)
                built_rabbit = rabbit_image.build(app)
                sandbox = modal.Sandbox.create('bash','-c',
                    'for ((i=0;i<120;i++)); do [[ ! -f /tmp/sdu-memory-sidecars-ready ]] || exec bash /opt/sdu/start.sh; sleep 1; done; exit 1',
                    app=app,image=backend,secrets=[app_secret],region=REGION,cpu=(CPU,CPU),memory=(MIB,MIB),timeout=SECONDS,
                    encrypted_ports=[8080],volumes={},tags={'sdu_run':run_id},env={
                        'SDU_DB_PRIVATE_SIDECAR':'1','PORTAL_SESSION_STORE':'redis','SDU_BACKEND_INSTANCES':'1','SDU_BACKEND_HEAP_BUDGET':'512',
                        'PORTAL_WRITE_BEHIND_ENABLED':'true','PORTAL_WRITE_BEHIND_SYNTHETIC_ONLY':'true','PORTAL_WRITE_BEHIND_HOST':'broker',
                        'PORTAL_WRITE_BEHIND_MAX_RUNTIME_SECONDS':'480'})
                report['sandbox_id'] = sandbox.object_id
                database = private_db.create(sandbox,built_db,database_secret)
                broker = sandbox._experimental_sidecars.create('bash','-c','exec bash /opt/sdu/start-rabbit.sh >/tmp/sdu-rabbit-startup.log 2>&1',name='broker',
                    image=built_rabbit,secrets=[rabbit_secret],outbound_cidr_allowlist=[])
                private_db.wait_ready(database)
                code,_,_ = execute(broker,'bash','-c','for ((i=0;i<30;i++)); do gosu rabbitmq rabbitmq-diagnostics -q check_running >/dev/null 2>&1 && exit 0; sleep 1; done; exit 1',timeout=65)
                if code: raise RuntimeError('RabbitMQ readiness failed')
                execute(sandbox,'touch','/tmp/sdu-memory-sidecars-ready')
                report['stage']='readiness';save_json(path,report)
                code,_,_ = execute(sandbox,'bash','-c','for ((i=0;i<100;i++)); do [[ ! -f /tmp/sdu-cloud/failed ]] || exit 2; [[ ! -f /tmp/sdu-cloud/ready ]] || exec /tmp/sdu-cloud/gateway probe; sleep 1; done; exit 1',timeout=110)
                if code: raise RuntimeError('Backend readiness failed')
                code,check,_ = execute(sandbox,'bash','-c',r"""
set -euo pipefail
export PGHOST=db PGUSER=portal_runtime PGDATABASE=portal_cloud_smoke PGPASSWORD="$DB_RUNTIME_PASSWORD"
export PGSSLMODE=verify-full PGSSLROOTCERT=/tmp/sdu-cloud/db-ca.crt PGCONNECT_TIMEOUT=3
[[ "$(psql -XqAt -c 'SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()')" == t ]]
if PGSSLMODE=disable psql -XqAt -c 'SELECT 1' >/dev/null 2>&1; then exit 2; fi
if PGPASSWORD=invalid psql -XqAt -c 'SELECT 1' >/dev/null 2>&1; then exit 3; fi
openssl s_client -connect broker:5671 -servername broker -verify_hostname broker -verify_return_error -CAfile /tmp/sdu-cloud/rabbit-ca.crt </dev/null >/dev/null 2>&1
if openssl s_client -connect broker:5671 -servername wrong -verify_hostname wrong -verify_return_error -CAfile /tmp/sdu-cloud/rabbit-ca.crt </dev/null >/dev/null 2>&1; then exit 4; fi
printf verified
""",timeout=15,secret=app_secret)
                if code or check.strip()!='verified': raise RuntimeError('TLS/authentication negative probes failed')
                report['private_security']={'postgres_tls':True,'plaintext_postgres_rejected':True,'bad_password_rejected':True,'broker_hostname_verified':True,'wrong_broker_hostname_rejected':True}
                origin = validate_origin(sandbox.tunnels(timeout=15)[8080].url)
                redactions.append(origin)
                if set(sandbox.tunnels()) != {8080}: raise RuntimeError('Unexpected published port')
                report['stage']='generator';save_json(path,report)
                outcome = generate.remote(origin)
                report.update(stage=outcome.get('stage','generator'),security=outcome.get('security'),metrics=metrics_from(outcome),k6_exit=outcome.get('k6_exit'))
                for name,key in (('raw.json.gz','raw'),('k6-console.log','k6_log')):
                    if outcome.get(key) is not None:
                        payload=outcome[key];(directory/name).write_bytes(payload if isinstance(payload,bytes) else payload.encode())
                if outcome.get('summary'): save_json(directory/'k6-summary.json',outcome['summary'])
                operations=outcome.get('journal_operations',[]);save_json(directory/'http-operations.json',operations)
                report['resources'].update(k6_cpu_seconds=outcome.get('k6_cpu_seconds'),k6_peak_rss_mib=outcome.get('k6_peak_rss_mib'))
                # Allow projection to drain, not a mutation cleanup: every accepted enrollment must have its own withdrawal.
                for _ in range(20):
                    if query(database,['-c','SELECT count(*) FROM operations.idempotency_records'],database_secret)=='20': break
                    time.sleep(.5)
                queue_rows=[]
                for _ in range(5):
                    code,output,_=execute(broker,'gosu','rabbitmq','rabbitmqctl','-q','list_queues','-p','/portal',
                        'name','messages_ready','messages_unacknowledged','durable','type',timeout=15)
                    queue_rows=[line.split('\t') for line in output.splitlines() if line.startswith('portal.decisions.v1\t')]
                    if code==0 and queue_rows==[['portal.decisions.v1','0','0','true','quorum']]: break
                    time.sleep(.2)
                queue_drained=queue_rows==[['portal.decisions.v1','0','0','true','quorum']]
                checks=reconcile(database,database_secret,operations)
                checks.update(broker_queue_drained=queue_drained,passed=checks['passed'] and queue_drained)
                save_json(directory/'invariants.json',checks)
                code,prometheus,_=execute(sandbox,'/tmp/sdu-cloud/gateway','metrics',secret=app_secret)
                if code==0: (directory/'metrics.prom').write_text(prometheus)
                report['metrics']['observed_vus']=len({op['vu'] for op in operations})

                report['sql_invariants_passed']=checks['passed']
                report['memory_journal'].update(confirmed=sum(not op['replayed'] for op in operations),projected=checks['unique_durable_operations'],
                                                reconciled=checks['passed'],active_final=checks['active_final'])
                metrics=report['metrics']
                passed=outcome.get('k6_exit')==0 and checks['passed'] and metrics['requests']==50 and metrics['errors']==0 and metrics['completed_journeys']==10
                report.update(status='passed' if passed else 'failed',stage='complete' if passed else report['stage'])
            finally:
                if sandbox:
                    report['resources'].update(collect_logs(sandbox,directory,redactions))
                    for target,name,remote in ((broker,'rabbit.log','/tmp/sdu-rabbit-startup.log'),(database,'postgres.log','/tmp/sdu-cloud/pg/server.log')):
                        if target:
                            try:
                                text=execute(target,'tail','-c','20000',remote)[1]
                                (directory/name).write_text(safe_log(text,*redactions))
                            except Exception: pass
                    try: sandbox.terminate(wait=True)
                    except Exception as error: report['termination_error']=type(error).__name__
    except BaseException as error:
        report.update(status='failed',failure=type(error).__name__)
        (directory/'operator-private.log').write_text(safe_log(str(error),*redactions))
    finally:
        if app.app_id:
            try: app.stop()
            except Exception as error: report['app_stop_error']=type(error).__name__
            report['cleanup']=verify_cleanup(app.app_id)
            if not report['cleanup']['verified']: report['status']='failed'
        report['finished_at']=now();save_json(path,report)
    print(json.dumps({'id':run_id,'status':report['status'],'estimate_usd':cost['estimate_usd'],'cleanup':report['cleanup'],'report':str(path)}))
    return report['status']=='passed'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--available-credit-usd',type=Decimal,required=True)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    if not args.available_credit_usd.is_finite() or args.available_credit_usd < CAP:
        parser.error('At least USD 0.50 confirmed available credits required')
    import signal
    def interrupt(_signal, _frame): raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupt)
    rates=modal.Workspace.from_context().billing.rates()
    cost=cost_plan(rates)
    if not args.execute:
        print(json.dumps({'plan':cost,'resources':{'cpu':CPU,'mib':MIB,'timeout':SECONDS,'k6_vus':10},'launch':False},indent=2));return
    RUNS.mkdir(parents=True,exist_ok=True)
    with (RUNS/'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if any(row.get('state') != 'stopped' for row in cli_json('app','list')) or cli_json('container','list'):
            raise RuntimeError('Modal environment must be idle')
        if not run(cost,args.available_credit_usd): raise SystemExit(1)

if __name__=='__main__': main()
