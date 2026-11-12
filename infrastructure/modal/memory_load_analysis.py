"""Derive clearly labeled ramp diagnostics from immutable benchmark artifacts. Never runs a workload."""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
from bench_evidence import read_samples, resource_summary
from memory_load_evidence import projection_stability
from mixed_evidence import parse_client_ledger, business_results
from smoke import RUNS, save_json


def analyze(directory):
    report=json.loads((directory/'report.json').read_text())
    if report.get('kind')!='memory-rabbit-load': raise ValueError('Not a memory load report')
    phase='measure' if report.get('metrics') else 'warmup'
    metrics=report.get('metrics') or report.get('warmup_metrics') or {}
    summary_path=directory/f'{phase}-summary.json'
    if not summary_path.exists(): return report
    summary=json.loads(summary_path.read_text())['metrics']
    def val(name,stat): return summary.get(name,{}).get('values',{}).get(stat)
    report['diagnostic_http']=dict(label='Ramp and aborted tail' if phase=='warmup' and metrics.get('duration_seconds') is None else phase,
        duration_seconds=metrics.get('k6_runtime_seconds'),requests=val('http_reqs','count'),rps=val('http_reqs','rate'),
        p50_ms=val('http_req_duration','p(50)'),p95_ms=val('http_req_duration','p(95)'),p99_ms=val('http_req_duration','p(99)'),
        observed_vus=metrics.get('observed_vus_peak'),unexpected=metrics.get('all_unexpected'),
        interrupted_iterations=metrics.get('interrupted_iterations'))
    data_path=directory/f'{phase}-ledger.log.gz'
    if data_path.exists():
        starts,results=parse_client_ledger(data_path.read_bytes())
        counts=Counter((r['route'],r['status'],r['outcome'],r['code']) for r in results.values())
        report['diagnostic_http']['classifications']=[dict(route=k[0],status=k[1],outcome=k[2],code=k[3],count=n) for k,n in sorted(counts.items())]
    samples=(directory/'samples.tsv').read_text() if (directory/'samples.tsv').exists() else ''
    window=dict(metrics)
    if metrics.get('duration_seconds') is None:
        window.update(window_started_ms=metrics.get('scenario_started_ms'),window_ended_ms=metrics.get('observed_window_ended_ms'),duration_seconds=metrics.get('k6_runtime_seconds'))
        report['resource_window']='ramp'
    else: report['resource_window']=phase
    frames=read_samples(samples)
    pg_path=directory/'postgres-samples.tsv.gz'
    if pg_path.exists(): frames += read_samples(gzip.decompress(pg_path.read_bytes()).decode())
    report['diagnostic_resources']=resource_summary(read_samples(samples),window)
    if pg_path.exists():
        pg_resources=resource_summary(read_samples(gzip.decompress(pg_path.read_bytes()).decode()),window)
        report['diagnostic_resources'].update({k:v for k,v in pg_resources.items() if k.startswith('postgres_')})
    report['diagnostic_projection']=projection_stability(samples,window)
    report['memory_journal']['diagnostic_pending_peak']=report['diagnostic_projection'].get('peak_pending')
    selected=[f['prom'] for f in read_samples(samples) if window.get('window_started_ms') is not None
              and window.get('window_ended_ms') is not None and window['window_started_ms'] <= f['epoch_ms'] <= window['window_ended_ms']]
    timers={}
    for timer in ('lock_wait','lock_hold','confirm','projection'):
        count='portal_memory_'+timer+'_seconds_count';total='portal_memory_'+timer+'_seconds_sum'
        pairs=[(s[count],s[total]) for s in selected if count in s and total in s]
        n=pairs[-1][0]-pairs[0][0] if len(pairs)>=2 else 0
        timers[timer]=dict(samples=n,mean_ms=1000*(pairs[-1][1]-pairs[0][1])/n if n>0 else None,
            peak_observed_max_ms=max((s['portal_memory_'+timer+'_seconds_max']*1000 for s in selected if 'portal_memory_'+timer+'_seconds_max' in s),default=None))
    report['diagnostic_memory_timers']=timers
    if not samples and (directory/'metrics.prom').exists():
        from memory_load_evidence import metric_values
        final=metric_values((directory/'metrics.prom').read_text())
        report['diagnostic_memory_timers_window']='Whole run including recovery and projection drain'
        for timer in timers:
            count=final.get('portal_memory_'+timer+'_seconds_count')
            total=final.get('portal_memory_'+timer+'_seconds_sum')
            maximum=final.get('portal_memory_'+timer+'_seconds_max')
            timers[timer]=dict(samples=count,mean_ms=1000*total/count if count and total is not None else None,
                              peak_observed_max_ms=maximum*1000 if maximum is not None else None)
    evidence_path=directory/f'{phase}-reconciliation.json.gz'
    if evidence_path.exists():
        evidence=json.loads(gzip.decompress(evidence_path.read_bytes()))
        report['diagnostic_business']=business_results(evidence,{},window,business_phase=phase)
        lags=[]
        accepted=[]
        commits=[]
        for op in evidence['operations']:
            durable=op['durable']
            ack=next((a['response'] for a in op['attempts'] if a['response'] and a['response']['outcome']=='success'),None)
            if durable and ack and durable.get('commit_ms') is not None:
                lags.append(durable['commit_ms']-ack['ended_ms'])
                accepted.append((ack['ended_ms'],1));commits.append((durable['commit_ms'],-1))
        if lags:
            start,end=window.get('window_started_ms'),window.get('window_ended_ms')
            if start is not None and end is not None:
                span=(end-start)/1000
                accepted_count=sum(start<=t<end for t,_ in accepted)
                commit_count=sum(start<=t<end for t,_ in commits)
                backlog=peak=0
                for _,delta in sorted(accepted+commits):
                    backlog+=delta;peak=max(peak,backlog)
                report['diagnostic_projection'].update(accepted_http_per_second=accepted_count/span,
                    committed_sql_per_second=commit_count/span,accepted_http=accepted_count,committed_sql=commit_count,
                    acknowledged_unprojected_peak_estimate=peak,clock_uncertainty=True)
            ordered=sorted(lags)
            report['projection_commit_after_http_ms']=dict(samples=len(lags),p50=ordered[len(lags)//2],p95=ordered[int(len(lags)*.95)],max=max(lags),clock_note='Cross-container wall-clock comparison; near-zero differences are uncertain.')
    save_json(directory/'report.json',report)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('run_id');args=p.parse_args()
    if '/' in args.run_id or not args.run_id.startswith('modal-'): raise ValueError('Invalid run ID')
    r=analyze(RUNS/args.run_id)
    print(json.dumps({k:r.get(k) for k in ('diagnostic_http','diagnostic_projection','diagnostic_memory_timers','diagnostic_business','projection_commit_after_http_ms')},indent=2))
