"""Verify the exact private broker startup script locally. Requires Docker; never uses paid resources."""
import os
from pathlib import Path
import secrets
import subprocess
import time
from memory_benchmark import certificate, RABBIT_IMAGE


def main():
    ca, cert, key = certificate('broker')
    env = os.environ | {'SDU_RABBIT_TLS_CA':ca,'SDU_RABBIT_TLS_CERT':cert,'SDU_RABBIT_TLS_KEY':key,'SDU_RABBIT_PASSWORD':secrets.token_urlsafe(32)}
    name='sdu-memory-tls-check'
    def docker(*args): return subprocess.run(['docker',*args],capture_output=True,text=True,timeout=15)
    try:
        subprocess.run(['docker','run','-d','--name',name,'--cpus','2','--memory','1g',
            *[item for k in env if k.startswith('SDU_RABBIT_') for item in ('--env',k)],
            '-v',str(Path(__file__).with_name('start-rabbit.sh').resolve())+':/opt/start.sh:ro',RABBIT_IMAGE,'bash','/opt/start.sh'],
            env=env,check=True,stdout=subprocess.DEVNULL,timeout=20)
        for _ in range(20):
            if docker('inspect','-f','{{.State.Running}}',name).stdout.strip()!='true': break
            if docker('exec','--user','rabbitmq',name,'rabbitmq-diagnostics','-q','check_running').returncode==0:
                for hostname, expected in [('broker',0),('wrong-host',1)]:
                    result=docker('exec',name,'bash','-c',f'openssl s_client -connect 127.0.0.1:5671 -servername broker -verify_hostname {hostname} -verify_return_error -CAfile /tmp/sdu-rabbit/ca.crt </dev/null >/dev/null 2>&1')
                    if bool(result.returncode)!=bool(expected):
                        print(docker('logs','--tail','25',name).stdout)
                        raise RuntimeError(f'TLS hostname verification failed for {hostname}; exit={result.returncode}')
                print(docker('exec','--user','rabbitmq',name,'rabbitmq-diagnostics','-q','listeners').stdout)
                print('Broker boot, verified TLS and wrong-host rejection: passed')
                return
            time.sleep(1)
        result=docker('logs',name)
        safe=(result.stdout+result.stderr).replace(env['SDU_RABBIT_PASSWORD'],'[REDACTED]')
        print(safe[:6500])
        raise SystemExit('TLS broker boot failed')
    finally:
        docker('rm','-fv',name)

if __name__=='__main__': main()
