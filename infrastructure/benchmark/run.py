"""Bounded native k6 runner with raw evidence, PostgreSQL checks and host resource stops."""
import argparse
import base64
import datetime as dt
import gzip
import json
import os
from pathlib import Path
import platform
import re
import signal
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from common import ROOT, LOCAL, RESULTS, compose, guard, revision, settings, sql


def system(*command):
    return subprocess.check_output(command, text=True, timeout=8).strip()


def host():
    result={'os':platform.platform(),'machine':platform.machine()}
    if sys.platform=='darwin':
        result.update(model=system('sysctl','-n','hw.model'),memory_bytes=int(system('sysctl','-n','hw.memsize')),
                      logical_cpus=int(system('sysctl','-n','hw.logicalcpu')))
    else:
        result.update(memory_bytes=os.sysconf('SC_PAGE_SIZE')*os.sysconf('SC_PHYS_PAGES'),logical_cpus=os.cpu_count())
    return result


def sample(process=None):
    result={'timestamp':time.time()}
    if sys.platform=='darwin':
        swap=system('sysctl','vm.swapusage')
        result['swap_used_mb']=float(re.search(r'used\s*=\s*([\d.]+)M',swap).group(1))
    elif sys.platform.startswith('linux'):
        fields={line.split(':',1)[0]:int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines() if len(line.split())>=2}
        result['swap_used_mb']=(fields.get('SwapTotal',0)-fields.get('SwapFree',0))/1024
        result['available_ram_mb']=fields.get('MemAvailable',0)/1024
    if process and process.poll() is None:
        try:
            raw=system('ps','-o','rss=,%cpu=','-p',str(process.pid)).split()
            result.update(k6_rss_kb=int(raw[0]),k6_cpu_percent=float(raw[1]))
        except (subprocess.CalledProcessError, IndexError):
            if process.poll() is None: raise
        try:
            with urllib.request.urlopen('http://127.0.0.1:19091/actuator/health/liveness',timeout=2) as response:
                result['backend_live']=json.load(response).get('status')=='UP'
        except (OSError, ValueError):
            result['backend_live']=False
    result['containers']=[json.loads(line) for line in system('docker','stats','--no-stream','--format','{{json .}}').splitlines() if 'sdu-registration-benchmark' in line]
    return result


def request_json(path):
    with urllib.request.urlopen('http://127.0.0.1:19090'+path,timeout=5) as response:return json.load(response)


def metrics_snapshot(path):
    value = base64.b64encode(('metrics:' + settings()['BENCH_METRICS_PASSWORD']).encode()).decode()
    request = urllib.request.Request('http://127.0.0.1:19091/actuator/prometheus', headers={'Authorization':'Basic ' + value})
    with urllib.request.urlopen(request,timeout=10) as response: path.write_bytes(response.read())


def diagnostics(destination):
    statement="""SELECT coalesce(json_agg(x),'[]') FROM (
      SELECT query,calls,total_exec_time,mean_exec_time,max_exec_time,rows,shared_blks_hit,shared_blks_read,shared_blk_read_time,shared_blk_write_time,local_blk_read_time,local_blk_write_time,temp_blk_read_time,temp_blk_write_time
      FROM pg_stat_statements WHERE userid=(SELECT oid FROM pg_roles WHERE rolname='portal_runtime') AND dbid=(SELECT oid FROM pg_database WHERE datname=current_database())
      ORDER BY total_exec_time DESC LIMIT 30) x;"""
    (destination/'sql-statements.json').write_text(sql(statement))
    (destination/'database-settings.json').write_text(sql("SELECT json_build_object('server_version',current_setting('server_version'),'fsync',current_setting('fsync'),'synchronous_commit',current_setting('synchronous_commit'),'full_page_writes',current_setting('full_page_writes'),'shared_buffers',current_setting('shared_buffers'),'max_connections',current_setting('max_connections'),'track_io_timing',current_setting('track_io_timing'))"))


def invariants(destination):
    check=ROOT/'backend/verification/phase5-invariants.sql'
    result=sql(check.read_text())
    (destination/'invariants.txt').write_text(result+'\n')
    # Eight named violation counts are returned before the grouped outcome rows in unaligned output.
    counts=result.splitlines()[:8]
    if len(counts)!=8 or any(line!='0' for line in counts):raise RuntimeError('Database invariant failure; see retained SQL evidence')
    dataset=json.loads((LOCAL/'dataset.json').read_text())
    hot=dataset['hot']['id']
    (destination/'hot-state.json').write_text(sql("SELECT json_build_object('active',(SELECT count(*) FROM enrollment.enrollments WHERE section_id='"+hot+"' AND status='ACTIVE'),'count',enrolled_count,'capacity',capacity) FROM sections.course_sections WHERE section_id='"+hot+"';"))


def aggregate(raw, destination):
    points={};counters={};counts={}
    with gzip.open(raw,'rt') as file:
        for line in file:
            item=json.loads(line)
            if item['type']!='Point':continue
            metric=item['metric'];data=item['data'];tags=data.get('tags',{})
            if metric in {'portal_api_latency','portal_enrollment_latency'}:
                group=(metric,tags.get('endpoint','enroll'),tags.get('result','all'))
                points.setdefault(group,[]).append(data['value'])
            if metric in {'portal_outcomes','portal_requests','portal_enrollment_successes','portal_business_denials','portal_network_errors','portal_timeouts','dropped_iterations','iterations','http_reqs'}:
                group=(metric,tags.get('endpoint','all'),tags.get('result',tags.get('code','all')))
                counters[group]=counters.get(group,0)+data['value']
    def percentile(values,p):
        ordered=sorted(values);position=(len(ordered)-1)*p;a=int(position);b=min(a+1,len(ordered)-1)
        return ordered[a]+(ordered[b]-ordered[a])*(position-a)
    output={'latencies':[],'counters':[]}
    for (metric,endpoint,result),values in points.items():
        output['latencies'].append({'metric':metric,'endpoint':endpoint,'result':result,'count':len(values),
            'p50_ms':percentile(values,.50),'p95_ms':percentile(values,.95),'p99_ms':percentile(values,.99),'max_ms':max(values)})
    for (metric,endpoint,result),value in counters.items():output['counters'].append({'metric':metric,'endpoint':endpoint,'result':result,'count':value})
    (destination/'classified-results.json').write_text(json.dumps(output,indent=2))
    return counters


def allocation_totals():
    active = json.loads(sql("""
        SELECT json_build_object('active',count(*),'sections',count(DISTINCT section_id),
            'distinctStudents',count(DISTINCT student_id))
        FROM enrollment.enrollments WHERE status='ACTIVE';
    """))
    durable = json.loads(sql("""
        SELECT json_build_object('enrolled',count(*) FILTER (WHERE response_code='ENROLLED'),
            'full',count(*) FILTER (WHERE response_code='SECTION_FULL'),'operations',count(*),
            'enrollmentAudit', (SELECT count(*) FROM audit.audit_events WHERE event_type='ENROLLED'))
        FROM operations.idempotency_records;
    """))
    return active, durable


def metric_count(counters, metric, endpoint=None, result=None):
    return sum(n for (m,e,r),n in counters.items() if m==metric and
               (endpoint is None or e==endpoint) and (result is None or r==result))


def check_allocation(destination, counters, mode, attempts, baseline):
    expected_success = min(30, attempts) if mode == 'hot' else attempts
    expected_full = attempts - expected_success
    active, durable = allocation_totals()
    hot = json.loads((destination/'hot-state.json').read_text())
    durable_delta = {name: value - baseline[name] for name, value in durable.items()}
    http = {'attempts':metric_count(counters,'portal_requests','enroll'),
            'enrolled':metric_count(counters,'portal_enrollment_successes'),
            'full':metric_count(counters,'portal_business_denials',result='SECTION_FULL')}
    passed = (http == {'attempts':attempts,'enrolled':expected_success,'full':expected_full}
              and durable_delta == {'enrolled':expected_success,'full':expected_full,'operations':attempts,
                                    'enrollmentAudit':expected_success}
              and active == {'active':expected_success,'distinctStudents':expected_success,
                             'sections':1 if mode=='hot' else min(100,attempts)}
              and (hot == {'active':expected_success,'count':expected_success,'capacity':30}
                   if mode=='hot' else hot['active']==0))
    (destination/'allocation.json').write_text(json.dumps({
        'passed':passed,'http':http,'active':active,'hot':hot,'durableDelta':durable_delta,
        'expectedAttempts':attempts,'expectedSuccess':expected_success,'expectedFull':expected_full,
    },indent=2))
    if not passed: raise RuntimeError('Incomplete or inconsistent allocation; see allocation.json')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=['auth','smoke','warmup','normal','catalog-read','browse-read','arrival','ramp','hot','spike','ladder','distributed'],required=True)
    parser.add_argument('--vus',type=int,default=10);parser.add_argument('--max-vus',type=int,default=50);parser.add_argument('--rate',type=int,default=100)
    parser.add_argument('--hot-pause',type=float,default=.2);parser.add_argument('--resource-budget-vus',type=int,default=50);parser.add_argument('--generator-memory-mb',type=int,default=768);parser.add_argument('--duration',default='20s');parser.add_argument('--iterations',type=int,default=2000);parser.add_argument('--name',required=True)
    parser.add_argument('--generator-cpu-percent',type=float,help='CPU budget in percent of one core (100 per core)')
    parser.add_argument('--minimum-free-ram-mb',type=int,default=256)
    parser.add_argument('--wall-clock-seconds',type=int,default=300)
    args=parser.parse_args();guard()
    if (LOCAL/'RESOURCE-STOP').exists(): raise RuntimeError('A previous run tripped the host resource stop; inspect the evidence before explicitly removing RESOURCE-STOP')
    hardware=host()
    safe_vus=min(args.resource_budget_vus,50 if sys.platform=='darwin' or hardware['memory_bytes']<=8*1024**3 else 5000)
    if not 1 <= args.vus <= args.max_vus or not 1 <= args.iterations <= 5000 or min(
            args.rate,args.wall_clock_seconds,args.minimum_free_ram_mb,args.generator_memory_mb) <= 0:
        parser.error('Require positive limits, 1 <= VUS <= MAX_VUS, and 1..5000 attempts')
    cpu_budget = args.generator_cpu_percent or 80 * hardware['logical_cpus']
    if cpu_budget <= 0: parser.error('CPU budget must be positive')
    if args.vus>safe_vus or args.max_vus>safe_vus:
        raise RuntimeError(f'Host resource policy caps this shared machine at {safe_vus} VUs; larger levels require a separately sized generator/host')
    if not re.fullmatch(r'[A-Za-z0-9_-]+',args.name):raise ValueError('Use a safe run name')
    baseline = None
    if args.mode in {'hot','distributed'}:
        active, baseline = allocation_totals()
        if active['active'] != 0:
            raise RuntimeError('Reset the dedicated run ledger before exact-allocation workloads')
    needed=args.iterations if args.mode=='hot' else args.max_vus if args.mode in {'arrival','ramp','spike'} else args.vus
    if args.mode!='auth':
        prepared=LOCAL/'sessions.json'
        identities=json.loads(prepared.read_text()) if prepared.exists() else []
        cohort=identities[:needed]
        if (len({entry['student'] for entry in cohort})!=needed or len({entry['cookie'] for entry in cohort})!=needed
            or any(time.time()-entry.get('verified_at',0)>25*60 for entry in cohort)):
            raise RuntimeError(f'Prepare or refresh {needed} distinct HTTP sessions with dataset.py sessions before running; preflight freshness budget is 25 minutes')
    destination=RESULTS/args.name
    destination.mkdir(parents=True,exist_ok=False)
    shutil.copytree(ROOT/'infrastructure/k6/lib',destination/'lib')
    shutil.copy2(ROOT/'infrastructure/k6/workloads.js',destination/'workloads.js')
    source = revision()
    (destination/'source-revision.json').write_text(json.dumps(source,indent=2))
    dataset=json.loads((LOCAL/'dataset.json').read_text())
    variant=dataset.get('variant','phase6-v1')
    manifest=LOCAL/f'manifest-{variant}.json'
    if manifest.exists():shutil.copy2(manifest,destination/'dataset-manifest.json')
    exe=os.environ.get('K6_BIN') or str(LOCAL/'tools/k6/k6')
    env=dict(os.environ,MODE=args.mode,VUS=str(args.vus),MAX_VUS=str(args.max_vus),RATE=str(args.rate),DURATION=args.duration,
             HOT_PAUSE=str(args.hot_pause),ITERATIONS=str(args.iterations),DATASET_META=str(LOCAL/'dataset-meta.json'),DATASET=str(LOCAL/'dataset.json'),SESSIONS=str(LOCAL/'sessions.json'),SUMMARY=str(destination/'summary.json'),
             BASE_URL='http://127.0.0.1:18080',BENCH_STUDENT_PASSWORD=settings()['BENCH_STUDENT_PASSWORD'])
    command=[exe,'run','--out','json='+str(destination/'raw.json.gz'),str(ROOT/'infrastructure/k6/workloads.js')]
    deployed=compose('ps','-q','backend',capture_output=True).stdout.strip()
    metadata={'profile':vars(args),'host':hardware,'dataset_seed':dataset['seed'],'dataset_created_at':dataset['createdAt'],
              'dataset_variant':variant,'k6_version':system(exe,'version'),'source_revision':source['sha256'],
              'warmup':'Separate warmup run only when explicitly named in report; this runner never silently warms a profile',
              'pool':dict(zip(('max','min_idle','acquire_timeout_ms','validation_timeout_ms'),
                   system('docker','exec',deployed,'printenv','DB_POOL_MAX_SIZE','DB_POOL_MIN_IDLE',
                          'DB_CONNECTION_TIMEOUT_MS','SPRING_DATASOURCE_HIKARI_VALIDATION_TIMEOUT').splitlines(),strict=True)),
              'effective_generator_cpu_percent':cpu_budget,'shared_host':True,'aborted':False,
              'backend_image_id':system('docker','inspect','--format','{{.Image}}',deployed),
              'backend_jar_sha256':system('docker','exec',deployed,'sha256sum','/app/backend.jar').split()[0]}
    metadata['docker_resources']=system('docker','info','--format','{{.OSType}} {{.Architecture}} {{.NCPU}} {{.MemTotal}}')
    metadata['containers']={}
    for service in ('backend','postgres','prometheus','grafana','postgres-exporter'):
        container=compose('ps','-q',service,capture_output=True).stdout.strip()
        if container:
            metadata['containers'][service]=json.loads(system('docker','inspect','--format',
                '{"image":{{json .Image}},"cpu_nanoseconds":{{.HostConfig.NanoCpus}},"memory_bytes":{{.HostConfig.Memory}}}',container))
    metrics_snapshot(destination/'prometheus-before.txt')
    initial=sample();samples=[initial]
    metadata['started_at']=time.time()
    with (destination/'console.log').open('w') as log:
        process=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT,cwd=ROOT)
        assertion=subprocess.Popen(['caffeinate','-i','-w',str(process.pid)]) if sys.platform=='darwin' else None
        cpu_streak=live_failures=0
        wall_end=time.monotonic()+args.wall_clock_seconds
        reason=None
        try:
            while process.poll() is None:
                time.sleep(2)
                current=sample(process)
                cpu_streak=cpu_streak+1 if current.get('k6_cpu_percent',0)>cpu_budget else 0
                live_failures=live_failures+1 if current.get('backend_live') is False else 0
                if current['timestamp']-samples[-1]['timestamp']>15:
                    reason='Host scheduling or suspend gap exceeded 15 seconds'
                samples.append(current)
                if (current.get('swap_used_mb',0)-initial.get('swap_used_mb',0)>256
                    or current.get('k6_rss_kb',0)>args.generator_memory_mb*1024
                    or current.get('available_ram_mb',float('inf'))<args.minimum_free_ram_mb):
                    reason='Swap growth, available RAM, or generator RSS exceeded its budget'
                if cpu_streak>=3:reason='Generator exceeded CPU budget in three samples'
                if live_failures>=3:reason='Backend liveness failed in three samples'
                if time.monotonic()>wall_end:reason='Run exceeded its wall-clock budget'
                if reason:break
        except (subprocess.SubprocessError, RuntimeError):
            reason='Resource sampler failed or became unresponsive'
        except KeyboardInterrupt:
            reason='Operator interrupted the run'
        if reason:
            metadata.update(aborted=True,stop_reason=reason)
            if process.poll() is None:process.send_signal(signal.SIGINT)
        try:status=process.wait(timeout=25)
        except subprocess.TimeoutExpired:
            process.kill()
            status=process.wait()
            metadata.update(aborted=True,stop_reason=reason or 'Generator did not stop within grace period',forced_kill=True)
    metadata.update(finished_at=time.time(),exit_code=status)
    if assertion and assertion.poll() is None:assertion.terminate()
    if metadata['aborted'] and metadata.get('stop_reason') != 'Operator interrupted the run':
        (LOCAL/'RESOURCE-STOP').write_text(json.dumps(metadata,indent=2))
    (destination/'metadata.json').write_text(json.dumps(metadata,indent=2));(destination/'resources.json').write_text(json.dumps(samples,indent=2))
    start=metadata['started_at'];end=metadata['finished_at']
    queries={'targets':'up','hikari_pending':'hikaricp_connections_pending','hikari_active':'hikaricp_connections_active',
             'pool_acquire_p95':'histogram_quantile(0.95,sum by(le)(rate(hikaricp_connections_acquire_seconds_bucket[15s])))',
             'section_wait_p95':'histogram_quantile(0.95,sum by(le)(rate(enrollment_section_lock_wait_seconds_bucket[15s])))',
             'section_hold_mean':'rate(enrollment_section_lock_hold_seconds_sum[15s])/rate(enrollment_section_lock_hold_seconds_count[15s])',
             'academic_mean':'rate(enrollment_academic_checks_seconds_sum[15s])/rate(enrollment_academic_checks_seconds_count[15s])',
             'jvm_cpu':'process_cpu_usage','jvm_heap':'jvm_memory_used_bytes{area="heap"}',
             'jvm_gc':'rate(jvm_gc_pause_seconds_sum[15s])','pg_wait':'portal_pg_wait_connections','pg_connections':'portal_pg_activity_connections',
             'transaction_p95':'histogram_quantile(0.95,sum by(le)(rate(enrollment_transaction_seconds_bucket[15s])))',
             'request_stages':'sum by(stage)(rate(enrollment_stage_seconds_sum[15s]))'}
    monitoring={}
    for name,query in queries.items():
        try:monitoring[name]=request_json('/api/v1/query_range?'+urllib.parse.urlencode({'query':query,'start':start,'end':end,'step':5}))
        except Exception as failure:monitoring[name]={'error':type(failure).__name__}
    (destination/'monitoring.json').write_text(json.dumps(monitoring,indent=2))
    collection_errors={}
    def collect(name, action):
        try:return action()
        except Exception as failure:
            collection_errors[name]=type(failure).__name__
            return None
    collect('metrics',lambda:metrics_snapshot(destination/'prometheus-after.txt'))
    collect('diagnostics',lambda:diagnostics(destination))
    collect('invariants',lambda:invariants(destination))
    counters=collect('raw',lambda:aggregate(destination/'raw.json.gz',destination))
    if counters is not None and args.mode in {'hot','distributed'}:
        collect('allocation',lambda:check_allocation(destination,counters,args.mode,
                args.iterations if args.mode=='hot' else args.vus,baseline))
    if counters is not None and args.mode in {'auth','smoke','distributed','hot'}:
        expected = args.iterations if args.mode=='hot' else args.vus
        if metric_count(counters,'iterations') != expected:
            collection_errors['iterations']='FixedIterationTargetNotMet'
    with (destination/'console.log').open() as log:
        if any('level=error' in line for line in log):
            collection_errors['generator']='GeneratorLoggedErrors'
    if collection_errors:
        (destination/'collection-errors.json').write_text(json.dumps(collection_errors,indent=2))
    print(json.dumps({'run':args.name,'mode':args.mode,'exit_code':status,'aborted':metadata['aborted'],'elapsed_seconds':end-start,'evidence':str(destination)}))
    if status or metadata['aborted'] or collection_errors:sys.exit(1)

if __name__=='__main__':main()
