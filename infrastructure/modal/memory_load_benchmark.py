"""One bounded 2,500-VU RAM/RabbitMQ mixed experiment; measurement requires HTTP, queue and SQL gates."""
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
import gzip
import pickle
from dataclasses import asdict
from bench_config import Case
from benchmark_worker import warmup_gate
from bench_evidence import phase_metrics, read_samples, resource_summary
from benchmark import receive_worker_result, WORKER_RESULT_CHUNK, MAX_WORKER_RESULT
from memory_load_evidence import drain, sql_gate, measurement_gate, projection_stability
import mixed_evidence
import resource_topology
from evidence_transport import wait_for_worker, read_worker_artifact, read_worker_manifest, retry_read

CASE = Case('mixed', 2500, 30, 24, 'encrypted-tunnel', spread_start=True, ramp_seconds=60,
            prefill_pool=True, split_database=True, diagnose_mixed=True, ramp_start_vus=1,
            resource_profile='a', private_db_sidecar=True, http_experiment='direct', session_storage='redis',
            sidecar_cpu_cores=16, jvm_threads='platform', hardware='ha-one', tomcat_threads=80)

from prepare import ASSETS, JAR, sha
from smoke import (HERE, RUNS, REGION, PG_IMAGE, PY_IMAGE, VIOLATIONS, EXPECTED, RATE_KEYS, assets_valid,
                   cli_json, execute, query, now, save_json, metrics_from, collect_logs, verify_cleanup, images)
from worker import safe_log, validate_origin
import split_topology
import private_db

RABBIT_IMAGE = "rabbitmq:4.4.0@sha256:485319d42170eab53d8a030e1cc49d95e41f5d325479fa504736db8091981eb7"
CPU, MIB, SECONDS = 16, 17408, 1440
GEN_CPU, GEN_MIB, GEN_SECONDS = 4, 4096, 750
CAP = Decimal("2.50")


def cost_plan(rates):
    values = {key: Decimal(str(rates[key])) for key in RATE_KEYS}
    if any(not v.is_finite() or v <= 0 for v in values.values()):
        raise ValueError("Invalid official pricing response")
    backend = (CPU * values['cpu_hour_cost_sandbox'] + Decimal(MIB) / 1024 * values['mem_gib_hour_cost_sandbox']) * SECONDS / 3600
    generator = (GEN_CPU * values['cpu_hour_cost'] + Decimal(GEN_MIB) / 1024 * values['mem_gib_hour_cost']) * (4 * (GEN_SECONDS + 45 + 5)) / 3600
    estimate = (backend + generator) * Decimal('1.75') + 4 * values['egress_gib_cost'] + Decimal('0.08')
    if estimate > CAP:
        raise ValueError("Load experiment allocation exceeds USD 2.50")
    return dict(rates={k: str(v) for k, v in values.items()}, regional_multiplier=1.75,
                compute_usd=float((backend + generator) * Decimal('1.75')), build_reserve_usd=.08,
                egress_allowance_usd=float(4 * values['egress_gib_cost']), estimate_usd=float(estimate), actual_usd=None)


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
    backend = backend.add_local_file(HERE/'bench-seed.sql', '/opt/sdu/bench-seed.sql')
    backend = backend.add_local_file(HERE/'sample-sandbox.sh', '/opt/sdu/sample-sandbox.sh')
    for source, target in [('benchmark_worker.py','benchmark_worker.py'), ('bench_config.py','bench_config.py'),
                           ('bench_evidence.py','bench_evidence.py'), ('mixed_recovery.py','mixed_recovery.py'), ('memory_recovery.py','memory_recovery.py'),
                           ('mixed_evidence.py','mixed_evidence.py'), ('evidence_transport.py','evidence_transport.py'), ('cloud-benchmark.js','benchmark.js')]:
        generator = generator.add_local_file(HERE/source, '/opt/sdu/'+target)
    for name, target in (('start-redis-session.sh','start-redis-session.sh'),('start-memory-backend.sh','start-balanced.sh')):
        backend = backend.add_local_file(HERE/name, '/opt/sdu/'+target)
    backend = backend.add_local_file(ASSETS/'haproxy-linux-amd64','/opt/sdu/haproxy')
    generator = generator.add_local_file(HERE/'memory_worker.py','/opt/sdu/memory_worker.py').add_local_file(HERE/'memory-smoke.js','/opt/sdu/memory-smoke.js')
    database_image = split_topology.image(base, baked=True)
    rabbit_image = modal.Image.from_registry(RABBIT_IMAGE).add_local_file(HERE/'start-rabbit.sh','/opt/sdu/start-rabbit.sh',copy=True)
    app = modal.App('sdu-memory-rabbit-load', tags={'sdu_run':run_id}, include_source=False)
    volume_name = 'sdu-evidence-' + run_id
    evidence_volume = modal.Volume.from_name(volume_name, create_if_missing=True, version=2)
    @app.function(image=generator, secrets=[worker_secret], region=REGION, routing_region=REGION,
                  cpu=(GEN_CPU,GEN_CPU), memory=(GEN_MIB,GEN_MIB), timeout=GEN_SECONDS, startup_timeout=45,
                  min_containers=0,max_containers=1,buffer_containers=0,scaledown_window=5,retries=0,
                  restrict_modal_access=True,single_use_containers=True,serialized=True,include_source=False,volumes={"/sdu-evidence":evidence_volume})
    def generate(origin, phase, actors, recovery_ledger=None):
        import sys, pickle
        sys.path.insert(0, '/opt/sdu')
        from benchmark_worker import run_benchmark
        from pathlib import Path
        from evidence_transport import checkpoint_worker
        def workload():
            if phase in ('warmup-recovery','measure-recovery'):
                import os, gzip, json
                from memory_recovery import recover_memory, recovery_opener
                def checkpoint(combined,state):
                    (directory/'recovery-progress.log.gz').write_bytes(combined)
                    (directory/'recovery-progress.json').write_text(json.dumps(state))
                    evidence_volume.commit()
                recovered,state=recover_memory(recovery_ledger,actors,origin,
                    {'X-SDU-Benchmark-Key':os.environ['SDU_GATE_KEY']},recovery_opener(),phase.removesuffix('-recovery'),checkpoint)
                return pickle.dumps({'recovery_ledger':recovered,'recovery':state},protocol=pickle.HIGHEST_PROTOCOL)
            if phase not in ('warmup','measure'): raise ValueError('Unapproved phase')
            outcome = run_benchmark(origin, '', 'mixed', 2500, 30, pool_size=24, transport='encrypted-tunnel',
            spread_start=True, ramp_seconds=60, prefill_pool=True, reconcile=True, ramp_start_vus=1,
            resource_profile='a', private_db_sidecar=True, http_experiment='direct', session_storage='redis',
            sidecar_cpu_cores=16, jvm_threads='platform', hardware='ha-one', tomcat_threads=80,
            single_phase=phase, prepared_actors=actors, defer_recovery=True)
            return pickle.dumps(outcome, protocol=pickle.HIGHEST_PROTOCOL)
        directory = Path('/sdu-evidence') / phase
        directory.mkdir(exist_ok=True)
        manifest = checkpoint_worker(evidence_volume,directory,workload)
        return manifest
    report = dict(version=2,kind='memory-rabbit-load',id=run_id,status='running',stage='creating-app',started_at=now(),
        experiment='ordered-sql-projector-v1',region=REGION,vus=2500,evidence_volume_name=volume_name,cost=cost,available_credit_reported_usd=float(available),cleanup=None,metrics=None,
        workload=CASE.report(),
        memory_journal=dict(authority='rabbitmq-confirmed',projection='postgresql-async',single_owner=True,
                            broker_replicas=1,storage='ephemeral',host_loss_tolerant=False,confirmed=None,projected=None,reconciled=None, max_pending=10000,max_decisions=1000000,projector='sql',recovery='drain-first-resumable-v1'),
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
                        'SDU_DB_PRIVATE_SIDECAR':'1','PORTAL_SESSION_STORE':'redis','SDU_BACKEND_INSTANCES':'1','SDU_BACKEND_HEAP_BUDGET':'4096',
                        'SDU_BENCHMARK':'1','SDU_BENCH_STUDENTS':'5000','SDU_BENCH_SECTION_CAPACITY':'252',
                        'SDU_BENCH_POOL':'24','SDU_BENCH_PREFILL_POOL':'1','SDU_MIXED_DIAGNOSTICS':'1',
                        'SDU_BENCH_REQUEST_LIMIT':str(CASE.request_limit),'SDU_BENCH_RESPONSE_MIB':str(CASE.response_mib),
                        'PORTAL_WRITE_BEHIND_MAX_DECISIONS':'1000000','PORTAL_WRITE_BEHIND_PROJECTOR_MODE':'sql',
                        'PORTAL_WRITE_BEHIND_ENABLED':'true','PORTAL_WRITE_BEHIND_SYNTHETIC_ONLY':'true','PORTAL_WRITE_BEHIND_HOST':'broker',
                        'PORTAL_WRITE_BEHIND_MAX_RUNTIME_SECONDS':'1350'})
                report['sandbox_id'] = sandbox.object_id
                database = private_db.create(sandbox,built_db,database_secret)
                broker = sandbox._experimental_sidecars.create('bash','-c','exec bash /opt/sdu/start-rabbit.sh >/tmp/sdu-rabbit-startup.log 2>&1',name='broker',
                    image=built_rabbit,secrets=[rabbit_secret],outbound_cidr_allowlist=[])
                private_db.wait_ready(database)
                code,_,_ = execute(broker,'bash','-c','for ((i=0;i<30;i++)); do gosu rabbitmq rabbitmq-diagnostics -q check_running >/dev/null 2>&1 && exit 0; sleep 1; done; exit 1',timeout=65)
                if code: raise RuntimeError('RabbitMQ readiness failed')
                execute(sandbox,'touch','/tmp/sdu-memory-sidecars-ready')
                report['stage']='readiness';save_json(path,report)
                code,_,_ = execute(sandbox,'bash','-c','for ((i=0;i<180;i++)); do [[ ! -f /tmp/sdu-cloud/failed ]] || exit 2; [[ ! -f /tmp/sdu-cloud/ready ]] || exec /tmp/sdu-cloud/gateway probe; sleep 1; done; exit 1',timeout=190)
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
                report['fixture'] = private_db.verify_fixture(database, database_secret, CASE)
                report['resources']['quotas_before_load'] = {name:resource_topology.quota(host)
                    for name,host in (('app',sandbox),('postgres',database),('rabbit',broker))}
                private_db.verify_quotas(report['resources']['quotas_before_load'],
                    {name:(CPU,MIB) for name in ('app','postgres','rabbit')})
                code,_,_=execute(sandbox,'test','-f','/tmp/sdu-cloud/samples.tsv')
                if code: raise RuntimeError('Supervised resource sampler did not start')
                from memory_load_evidence import metric_values
                code,prom,_=execute(sandbox,'/tmp/sdu-cloud/gateway','metrics')
                if code or metric_values(prom).get('portal_memory_projector_sql') != 1:
                    raise RuntimeError('SQL projector mode did not activate')
                ledgers, actors, outcomes = [], None, {}
                watch = dict(snapshots=0, missed_snapshots=0, stop_reason=None)
                report['invariant_watch'] = watch
                with private_db.invariant_watch(database, sandbox, database_secret, watch):
                    for phase in ('warmup', 'measure'):
                        report['stage'] = phase; save_json(path, report)
                        print(json.dumps({'id':run_id,'stage':phase}), flush=True)
                        invocation = generate.spawn(origin, phase, actors)
                        report[phase+'_function_call_id'] = invocation.object_id; save_json(path,report)
                        try:
                            manifest = wait_for_worker(invocation,GEN_SECONDS+55)
                        except Exception as error:
                            # Recover the saved invocation's immutable artifact; never launch it again.
                            manifest = read_worker_manifest(evidence_volume,prefix=phase+'/')
                            report[phase+'_control_plane_recovered'] = type(error).__name__
                        outcome = pickle.loads(read_worker_artifact(evidence_volume,manifest,prefix=phase+'/'))
                        actors = outcome.pop('_prepared_actors',None)
                        report['security'] = outcome.get('security')
                        report['preparation'] = outcome.get('preparation')
                        report['resources'].update(outcome.get('resources') or {})
                        payload = outcome.get(phase)
                        if not payload:
                            report.update(status='failed',stage=outcome.get('stage','generator'))
                            save_json(directory/f'{phase}-worker.json',outcome)
                            break
                        outcomes[phase] = payload
                        # Preserve original HTTP evidence before any recovery. The original failures never become successes.
                        if payload.get('ledger') is not None:
                            (directory/f'{phase}-original-ledger.log.gz').write_bytes(payload['ledger'])
                        # Persist measured results before recovery can fail or its container can stop.
                        save_json(directory/f'{phase}-summary.json',payload.get('summary'))
                        (directory/f'{phase}-console.log').write_text(payload.get('log',''))
                        report['warmup_metrics' if phase=='warmup' else 'metrics']=phase_metrics(payload,30 if phase=='warmup' else 120,CASE.ramp_elapsed if phase=='warmup' else 0)
                        save_json(directory/f'{phase}-worker.json', {key:value for key,value in outcome.items() if key != phase})
                        save_json(path,report)
                        pre_recovery=drain(sandbox,broker)
                        save_json(directory/f'{phase}-pre-recovery-drain.json',pre_recovery)
                        from mixed_recovery import pending_journeys
                        pending=pending_journeys(payload['ledger']) if payload.get('ledger') is not None else []
                        if pending and pre_recovery['passed']:
                            recovery_phase=phase+'-recovery'
                            invocation=generate.spawn(origin,recovery_phase,actors,payload['ledger'])
                            report[recovery_phase+'_function_call_id']=invocation.object_id;save_json(path,report)
                            try: manifest=wait_for_worker(invocation,150)
                            except Exception as error:
                                manifest=read_worker_manifest(evidence_volume,prefix=recovery_phase+'/')
                                report[recovery_phase+'_control_plane_recovered']=type(error).__name__
                            recovered=pickle.loads(read_worker_artifact(evidence_volume,manifest,prefix=recovery_phase+'/'))
                            payload.update(recovered)
                        else:
                            payload['recovery']={'pending':len(pending),'resolved':0,'complete':not pending,
                                                 'requests':0,'replays':0,'projection_drained_before_start':pre_recovery['passed']}
                        report.setdefault('recovery',{})[phase]=payload['recovery']
                        for label in ('ledger','recovery_ledger'):
                            if payload.get(label) is not None:
                                blob = payload[label]; ledgers.append(blob)
                                (directory/f'{phase}-{label}.log.gz').write_bytes(blob)
                        save_json(directory/f'{phase}-summary.json',payload.get('summary'))
                        save_json(directory/f'{phase}-recovery.json',payload.get('recovery'))
                        (directory/f'{phase}-console.log').write_text(payload.get('log',''))
                        save_json(directory/f'{phase}-worker.json', {key:value for key,value in outcome.items() if key != phase})
                        duration, offset = (30, CASE.ramp_elapsed) if phase == 'warmup' else (120, 0)
                        metrics = phase_metrics(payload,duration,offset)
                        report['warmup_metrics' if phase=='warmup' else 'metrics'] = metrics
                        http = warmup_gate(CASE,payload) if phase=='warmup' else measurement_gate(CASE,payload)
                        queue = drain(sandbox,broker)
                        checks, evidence = sql_gate(database,database_secret,ledgers,directory,phase,queue)
                        report['sql_invariants_passed'] = checks['passed']
                        report['sql_structural_passed'] = not any(checks['violations'].values())
                        save_json(directory/'invariants.json',dict(passed=checks['passed'],violations=checks['violations'],
                            observed={'active':checks['counts']['active']},expected={'active':0}))
                        report['memory_journal'].update(confirmed=queue['counters'].get('portal_memory_confirmed_total'),
                            projected=checks['counts']['durable'],reconciled=checks['reconciliation']['inconsistent']==0 and checks['reconciliation']['unobserved_durable']==0 and queue['passed'],active_final=checks['counts']['active'])
                        code,samples,_ = execute(sandbox,'cat','/tmp/sdu-cloud/samples.tsv',timeout=15)
                        if code:
                            report['metrics_collection_error']='No Data'
                            samples=''
                        (directory/'samples.tsv').write_text(samples)
                        report[phase+'_resources'] = resource_summary(read_samples(samples),metrics)
                        stability = projection_stability(samples,metrics)
                        report[phase+'_projection'] = stability
                        report['resource_window'] = phase
                        report[phase+'_gate'] = dict(http=http,sql=checks['passed'],projection=stability,
                            passed=http['passed'] and checks['passed'] and not watch['stop_reason']
                            and stability['passed'])
                        report['business_results'] = mixed_evidence.business_results(evidence,report.get('warmup_metrics',{}),report.get('metrics') or {})
                        report['operation_reconciliation'] = evidence['summary']
                        save_json(path,report)
                        print(json.dumps({'id':run_id,'phase':phase,'http':http['passed'],'sql':checks['passed'],
                                          'metrics':metrics,'projection':stability}),flush=True)
                        if not report[phase+'_gate']['passed']:
                            report.update(status='failed',stage=phase+'-gate');break
                        if phase=='measure': report.update(status='passed',stage='complete')
            finally:
                if sandbox:
                    try:
                        code,text,_=execute(sandbox,'cat','/tmp/sdu-cloud/samples.tsv',timeout=10)
                        if code==0: (directory/'samples.tsv').write_text(text)
                        code,text,_=execute(sandbox,'/tmp/sdu-cloud/gateway','metrics')
                        if code==0: (directory/'metrics.prom').write_text(text)
                    except Exception as error: report['metrics_collection_error']=type(error).__name__
                if database:
                    try:
                        data=split_topology.export_operations(database,database_secret,mixed_evidence.database_ledger(500000),limit_mib=64)
                        (directory/'final-database.tsv.gz').write_bytes(gzip.compress(data.encode(),compresslevel=3))
                        rows=query(database,['-f','/opt/sdu/invariants.sql'],database_secret).splitlines()
                        save_json(directory/'final-structural-invariants.json',dict(zip(VIOLATIONS,map(int,rows[:len(VIOLATIONS)]),strict=True)))
                    except Exception as error: report['final_sql_export_error']=type(error).__name__
                if database:
                    try:
                        status,size,_=execute(database,'bash','-c','gzip -c /tmp/sdu-cloud/pg-samples.tsv > /tmp/sdu-cloud/pg-samples.tsv.gz; wc -c < /tmp/sdu-cloud/pg-samples.tsv.gz')
                        if status==0:
                            blob=split_topology.read_binary_export(database,'/tmp/sdu-cloud/pg-samples.tsv.gz',size,limit_mib=16,label='PostgreSQL samples')
                            (directory/'postgres-samples.tsv.gz').write_bytes(blob)
                    except Exception as error: report['pg_evidence_error']=type(error).__name__
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
        try:
            retry_read(lambda: modal.Volume.objects.delete(volume_name,allow_missing=True))
            report['evidence_volume_cleanup_verified'] = not any(row.get('name') == volume_name for row in cli_json('volume','list'))
        except Exception as error:
            report['evidence_volume_cleanup_verified'] = False
            report['volume_cleanup_error'] = type(error).__name__
        if app.app_id:
            try: app.stop()
            except Exception as error: report['app_stop_error']=type(error).__name__
            report['cleanup']=verify_cleanup(app.app_id)
            if not report['cleanup']['verified'] or not report.get('evidence_volume_cleanup_verified'): report['status']='failed'
        report['finished_at']=now();save_json(path,report)
    print(json.dumps({'id':run_id,'status':report['status'],'estimate_usd':cost['estimate_usd'],'cleanup':report['cleanup'],'report':str(path)}))
    return report['status']=='passed'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--available-credit-usd',type=Decimal,required=True)
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    if not args.available_credit_usd.is_finite() or args.available_credit_usd < CAP:
        parser.error('At least USD 2.50 confirmed available credits required')
    import signal
    def interrupt(_signal, _frame): raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupt)
    rates=modal.Workspace.from_context().billing.rates()
    cost=cost_plan(rates)
    if not args.execute:
        print(json.dumps({'plan':cost,'resources':{'cpu':CPU,'mib':MIB,'timeout':SECONDS,'k6_vus':2500},'launch':False},indent=2));return
    RUNS.mkdir(parents=True,exist_ok=True)
    with (RUNS/'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if any(row.get('state') != 'stopped' for row in cli_json('app','list')) or cli_json('container','list'):
            raise RuntimeError('Modal environment must be idle')
        if not run(cost,args.available_credit_usd): raise SystemExit(1)

if __name__=='__main__': main()
