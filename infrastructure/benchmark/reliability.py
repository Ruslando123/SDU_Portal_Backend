"""Real isolated container/process/HTTP failures; no production database target is accepted."""
import argparse
import concurrent.futures
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import socket
import sys
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from common import ROOT, LOCAL, RESULTS, compose, guard, literal, settings, sql
from run import invariants, metrics_snapshot

BASE='http://127.0.0.1:18080'


def request(method,path,session,key=None):
    headers={'Cookie':'SESSION='+session['cookie'],'X-CSRF-TOKEN':session['csrf']}
    if key:headers['Idempotency-Key']=key
    req=urllib.request.Request(BASE+path,data=b'' if method in ['POST','DELETE'] else None,headers=headers,method=method)
    try:
        with urllib.request.urlopen(req,timeout=15) as response:return response.status,response.read(),dict(response.headers)
    except urllib.error.HTTPError as failure:return failure.code,failure.read(),dict(failure.headers)


def assert_unavailable(response):
    status, body, headers = response
    assert status==503,status
    problem=json.loads(body)
    assert problem['status']==503 and problem['code']=='TEMPORARILY_UNAVAILABLE' and problem['retryable'] is True
    assert problem['detail']=='Session storage is temporarily unavailable.'
    assert headers.get('Retry-After')=='1' and headers.get('X-Correlation-ID')==problem['correlationId']
    assert headers.get('Content-Type','').startswith('application/problem+json')
    assert not any(key.lower() in {'set-cookie','idempotency-replayed'} for key in headers)


def ready():
    deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        try:
            with urllib.request.urlopen(BASE+'/api/v1/auth/csrf',timeout=1) as response:
                if response.status==200:return
        except OSError:time.sleep(.2)
    raise RuntimeError('Isolated backend did not recover')


def backend_container():
    result=compose('ps','-q','backend',capture_output=True).stdout.strip()
    if not result:raise RuntimeError('Isolated backend is not running')
    return result


def wait_for(query,predicate,timeout=5):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        value=sql(query)
        if predicate(value):return value
        time.sleep(.03)
    raise RuntimeError('Controlled failure point was not observed')


def run(name):
    guard()
    if not re.fullmatch(r'[A-Za-z0-9_-]+',name):raise ValueError('Use a safe run name')
    destination=RESULTS/name
    destination.mkdir(parents=True,exist_ok=False)
    compose('stop','-t','10','backend',capture_output=True)
    subprocess.run([sys.executable,str(ROOT/'infrastructure/benchmark/dataset.py'),'reset-run'],check=True)
    compose('up','-d','backend',capture_output=True);ready()
    dataset=json.loads((LOCAL/'dataset.json').read_text());session=json.loads((LOCAL/'sessions.json').read_text())[0]
    student=dataset['students'][0];section=student['section'];path=f'/api/v1/sections/{section}/enrollments'
    results=[]
    def checkpoint(name,values):
        entry={'scenario':name,**values};results.append(entry)
        folder=destination/name;folder.mkdir();invariants(folder)
        (destination/'results.json').write_text(json.dumps(results,indent=2));print(json.dumps(entry),flush=True)
    def cleanup(body):
        enrollment=json.loads(body)['enrollmentId']
        status,body,_=request('DELETE','/api/v1/enrollments/'+enrollment,session,str(uuid.uuid4()))
        assert status==200,(status,body)

    # Pause either before the COMMIT command or within a deferred COMMIT trigger.
    # Disconnect while COMMIT is executing is deliberately treated as ambiguous, never assumed rollback.
    for during_commit in [False, True]:
        key=str(uuid.uuid4())
        trigger=("CREATE CONSTRAINT TRIGGER phase6_pause_commit AFTER INSERT ON audit.audit_events DEFERRABLE INITIALLY DEFERRED" if during_commit
                 else "CREATE TRIGGER phase6_pause_commit BEFORE INSERT ON audit.audit_events")
        sql("CREATE FUNCTION public.phase6_pause_commit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.event_type='ENROLLED' THEN PERFORM pg_sleep(30); END IF; RETURN NEW; END $$; "
            +trigger+" FOR EACH ROW EXECUTE FUNCTION public.phase6_pause_commit();")
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future=executor.submit(request,'POST',path,session,key)
                observed=wait_for("SELECT query FROM pg_stat_activity WHERE application_name='portal-benchmark' AND wait_event='PgSleep'",lambda x:bool(x))
                assert ('COMMIT' in observed) == during_commit, observed
                subprocess.run(['docker','kill','--signal','KILL',backend_container()],check=True,capture_output=True)
                try:lost_status=future.result(timeout=10)[0]
                except Exception:lost_status=0
            wait_for("SELECT count(*) FROM pg_stat_activity WHERE application_name='portal-benchmark'",lambda x:int(x)==0,timeout=40)
            committed=int(sql('SELECT count(*) FROM operations.idempotency_records WHERE idempotency_key='+literal(key)+" AND state='COMPLETED'"))
            if not during_commit:
                assert committed==0
                assert sql('SELECT count(*) FROM enrollment.enrollments WHERE student_id='+literal(student['student'])+" AND status='ACTIVE'")=='0'
        finally:sql('DROP TRIGGER phase6_pause_commit ON audit.audit_events; DROP FUNCTION public.phase6_pause_commit();')
        compose('up','-d','backend',capture_output=True);ready()
        status,body,headers=request('POST',path,session,key);assert status==201
        replayed=headers.get('Idempotency-Replayed')=='true'
        assert replayed==(committed==1)
        assert sql('SELECT count(*) FROM enrollment.enrollments WHERE student_id='+literal(student['student'])+" AND status='ACTIVE'")=='1'
        cleanup(body)
        checkpoint('kill_during_commit' if during_commit else 'kill_before_commit',{'actual_signal':'SIGKILL','client_status':lost_status,
            'postgres_step_at_kill':'COMMIT' if during_commit else 'audit INSERT before COMMIT',
            'completed_outcomes_after_server_finishes':committed,'same_key_recovery':status,'recovered_as_replay':replayed})

    # A real HTTP proxy withholds an already committed upstream response, then drops the socket.
    for terminate in [False,True]:
        key=str(uuid.uuid4());upstream_ready=threading.Event();release=threading.Event();captured={}
        class Proxy(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                connection=http.client.HTTPConnection('127.0.0.1',18080,timeout=15)
                headers={key:value for key,value in self.headers.items() if key.lower() not in ['host','connection','content-length']}
                try:
                    connection.request('POST',self.path,body=b'',headers=headers)
                    response=connection.getresponse();captured['status']=response.status;captured['body']=response.read()
                    upstream_ready.set();release.wait(15)
                    self.connection.shutdown(socket.SHUT_RDWR)
                finally:connection.close()
        server=ThreadingHTTPServer(('127.0.0.1',0),Proxy)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def dropped_request():
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=20)
            try:
                connection.request('POST',path,body=b'',headers={'Cookie':'SESSION='+session['cookie'],'X-CSRF-TOKEN':session['csrf'],'Idempotency-Key':key})
                return connection.getresponse().status
            finally:connection.close()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                pending=executor.submit(dropped_request)
                assert upstream_ready.wait(12)
                assert captured['status']==201
                assert sql('SELECT count(*) FROM operations.idempotency_records WHERE idempotency_key='+literal(key)+" AND state='COMPLETED'")=='1'
                if terminate:subprocess.run(['docker','kill','--signal','KILL',backend_container()],check=True,capture_output=True)
                release.set()
                try:client_status=pending.result(timeout=5)
                except Exception:client_status=0
        finally:release.set();server.shutdown();server.server_close();thread.join(timeout=2)
        if terminate:compose('up','-d','backend',capture_output=True);ready()
        status,body,headers=request('POST',path,session,key)
        assert status==201 and body==captured['body'] and headers.get('Idempotency-Replayed')=='true'
        cleanup(body)
        checkpoint('kill_after_commit' if terminate else 'lost_http_response',{'actual_signal':'SIGKILL' if terminate else None,'client_status':client_status,'same_key_recovery':status,'exact_body_replay':True})

    # Temporary real database shutdown; the default durable settings stay enabled.
    key=str(uuid.uuid4())
    compose('stop','-t','5','postgres',capture_output=True)
    try:
        outage=request('POST',path,session,key)
        assert_unavailable(outage)
        status=outage[0]
    finally:compose('up','-d','--wait','postgres',capture_output=True)
    ready();recovered,body,_=request('POST',path,session,key);assert recovered==201
    replay,again,headers=request('POST',path,session,key);assert replay==201 and body==again and headers.get('Idempotency-Replayed')=='true'
    cleanup(body)
    checkpoint('database_disconnect',{'database_action':'real clean stop and restart','outage_http_status':status,'same_key_recovery':recovered,'repeated_key_replay':replay})
    # Exhaust the actual Hikari pool with two root attempts waiting on a controlled section lock.
    compose('stop','-t','10','backend',capture_output=True)
    small_pool=dict(os.environ,BENCH_POOL_SIZE='2',BENCH_POOL_TIMEOUT_MS='500',BENCH_VALIDATION_TIMEOUT='250')
    compose('up','-d','backend',capture_output=True,env=small_pool);ready()
    sessions=json.loads((LOCAL/'sessions.json').read_text())[:2]
    env=dict(os.environ,PGPASSWORD=settings()['BENCH_DB_PASSWORD'])
    blocker=subprocess.Popen(['psql','-X','-qAt','-v','ON_ERROR_STOP=1','-h','127.0.0.1','-p','16432','-U','benchmark_migrator','-d','portal_benchmark'],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
    try:
        blocker.stdin.write("BEGIN; SELECT section_id FROM sections.course_sections WHERE section_id="+literal(section)+" FOR UPDATE; SELECT 'LOCKED';\n");blocker.stdin.flush()
        while blocker.stdout.readline().strip()!='LOCKED':
            if blocker.poll() is not None:raise RuntimeError('Controlled section lock failed')
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            requests=[executor.submit(request,'POST',path,s,str(uuid.uuid4())) for s in sessions]
            wait_for("SELECT count(*) FROM pg_stat_activity WHERE application_name='portal-benchmark' AND wait_event_type='Lock'",lambda x:int(x)>=2)
            excess=executor.submit(request,'GET','/api/v1/me',session)
            time.sleep(.1)
            snapshot=destination/'pool-during.txt';metrics_snapshot(snapshot)
            exhausted=excess.result(timeout=5)
            assert_unavailable(exhausted)
            exhausted_status=exhausted[0]
            blocker.stdin.write('ROLLBACK;\n\\q\n');blocker.stdin.flush();blocker.wait(timeout=5)
            effects=[pending.result(timeout=10) for pending in requests]
            assert all(result[0]==201 for result in effects),[result[0] for result in effects]
            for s,result in zip(sessions,effects):
                enrollment=json.loads(result[1])['enrollmentId']
                assert request('DELETE','/api/v1/enrollments/'+enrollment,s,str(uuid.uuid4()))[0]==200
        checkpoint('pool_exhaustion',{'pool_max':2,'acquisition_timeout_ms':500,'excess_http_status':exhausted_status,'recovered_requests':2})
    finally:
        if blocker.poll() is None:blocker.terminate();blocker.wait(timeout=5)
        compose('stop','-t','10','backend',capture_output=True)
        compose('up','-d','backend',capture_output=True);ready()
    (destination/'complete.json').write_text(json.dumps({'passed':len(results),'results':results},indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--name',default='phase7a-reliability')
    args=parser.parse_args()
    assertion=subprocess.Popen(['caffeinate','-i','-w',str(os.getpid())]) if sys.platform=='darwin' else None
    try:run(args.name)
    finally:
        if assertion and assertion.poll() is None:assertion.terminate();assertion.wait(timeout=2)
