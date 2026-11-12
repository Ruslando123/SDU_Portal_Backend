"""Small, repeatable AD-13 comparison. Early mode is isolated and never the application default."""
import json
import os
import subprocess
import sys
import time
import urllib.request
from common import ROOT, compose, guard


def ready():
    until=time.monotonic()+40
    while time.monotonic()<until:
        try:
            with urllib.request.urlopen('http://127.0.0.1:18080/api/v1/auth/csrf',timeout=1) as r:
                if r.status==200:return
        except OSError:time.sleep(.2)
    raise RuntimeError('Benchmark backend not ready')


def main():
    guard()
    for repetition in [1,2]:
        for early in [False,True]:
            mode='early' if early else 'late'
            compose('stop','-t','10','backend',capture_output=True)
            subprocess.run([sys.executable,str(ROOT/'infrastructure/benchmark/dataset.py'),'reset-run'],check=True)
            env=dict(os.environ,BENCH_EARLY_LOCKING=str(early).lower())
            compose('up','-d','backend',env=env,capture_output=True);ready()
            commands=[['--mode','smoke','--vus','5','--max-vus','10','--name',f'{mode}-warmup-r{repetition}'],
                      ['--mode','hot','--vus','5','--max-vus','10','--iterations','100','--hot-pause','.2','--name',f'{mode}-hot100-r{repetition}']]
            for args in commands:subprocess.run([sys.executable,str(ROOT/'infrastructure/benchmark/run.py'),*args],check=True)
    # Restore the production default; retain raw comparison evidence.
    compose('stop','-t','10','backend',capture_output=True)
    compose('up','-d','backend',capture_output=True);ready()

if __name__=='__main__':main()
