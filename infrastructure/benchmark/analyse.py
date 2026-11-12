"""Derive the Phase 6 evidence table and optional scientific plot from raw local artifacts."""
import csv
import json
from pathlib import Path
import re
from common import ROOT, RESULTS

RUNS=['auth5-final','smoke10-verified','controlled-normal10-r1','controlled-normal10-r2','arrival10-r2','arrival10-refreshed',
      'spike10-awake','late-hot100-r1','early-hot100-r1','late-hot100-r2','early-hot100-r2','ladder10-syntax']
HOT=[n for n in RUNS if 'hot100' in n]


def load(folder,name):return json.loads((folder/name).read_text())


def snapshot(path):
    values={}
    for line in path.read_text().splitlines():
        if not line or line.startswith('#'):continue
        name,value=line.rsplit(None,1)
        try:values[name]=float(value)
        except ValueError:pass
    return values


def metric(values,name):return sum(v for k,v in values.items() if k==name or k.startswith(name+'{'))


def timer(folder,name):
    before=snapshot(folder/'prometheus-before.txt');after=snapshot(folder/'prometheus-after.txt')
    key=name+'_seconds_';count=metric(after,key+'count')-metric(before,key+'count')
    return 1000*(metric(after,key+'sum')-metric(before,key+'sum'))/count if count else None


def histogram(folder,name):
    before=snapshot(folder/'prometheus-before.txt');after=snapshot(folder/'prometheus-after.txt');values={}
    for key,value in after.items():
        if not key.startswith(name+'_seconds_bucket{'):continue
        le=re.search(r'le="([^"]+)"',key)
        if le:values[float(le.group(1))]=value-before.get(key,0)
    return values


def quantile_ms(buckets, fraction=.95):
    total=buckets.get(float('inf'),0)
    if not total:return None
    target=total*fraction;lower=0;prior=0
    for bound,cumulative in sorted(buckets.items()):
        if bound==float('inf'):return lower*1000
        if cumulative>=target:
            return 1000*(lower+(bound-lower)*(target-prior)/(cumulative-prior)) if cumulative>prior else bound*1000
        lower,prior=bound,cumulative
    return None


def analyse():
    rows=[]
    for name in RUNS:
        folder=RESULTS/name
        summary=load(folder,'summary.json');classified=load(folder,'classified-results.json');resources=load(folder,'resources.json');metadata=load(folder,'metadata.json')
        assert not metadata['aborted'] and metadata['exit_code']==0 and metadata.get('timing_valid',True)
        m=summary['metrics'];v=m['portal_api_latency']['values'];counts=classified['counters']
        def count(metric, result=None):return sum(x['count'] for x in counts if x['metric']==metric and (result is None or x['result']==result))
        candidates=[r for r in classified['latencies'] if r['metric']=='portal_enrollment_latency' and r['result']=='success']
        deny=[r for r in classified['latencies'] if r['metric']=='portal_enrollment_latency' and r['result']=='business_denial']
        backend=[c for r in resources for c in r.get('containers',[]) if '-backend-' in c.get('Name','')]
        cpu=max([float(c['CPUPerc'].rstrip('%')) for c in backend],default=None)
        monitor=load(folder,'monitoring.json');pending=[float(v) for x in monitor.get('hikari_pending',{}).get('data',{}).get('result',[]) for t,v in x.get('values',[])]
        values={'run':name,'duration_s':summary['state']['testRunDurationMs']/1000,'requests':m['http_reqs']['values']['count'],
                'rps':m['http_reqs']['values']['rate'],'p50_ms':v['p(50)'],'p95_ms':v['p(95)'],'p99_ms':v['p(99)'],
                'enroll_success_p95_ms':candidates[0]['p95_ms'] if candidates else None,'full_p95_ms':deny[0]['p95_ms'] if deny else None,
                'new_enrollments':count('portal_enrollment_successes'),'business_denials':count('portal_business_denials'),
                'replays':count('portal_outcomes','replay'),'unexpected_rate':m['portal_unexpected']['values']['rate'],
                'rate_limited':count('portal_outcomes','rate_limited'),'unavailable':count('portal_outcomes','unavailable'),
                'technical':count('portal_outcomes','technical')+count('portal_outcomes','unexpected'),
                'network':count('portal_network_errors'),'timeouts':count('portal_timeouts'),'dropped_iterations':count('dropped_iterations'),
                'backend_peak_sampled_cpu_percent':cpu,'k6_peak_rss_mib':max([r['k6_rss_kb']/1024 for r in resources if 'k6_rss_kb' in r],default=None),
                'k6_peak_sampled_cpu_percent':max([r['k6_cpu_percent'] for r in resources if 'k6_cpu_percent' in r],default=None),
                'hikari_pending_sampled_max':max(pending,default=None),
                'pool_mean_ms':timer(folder,'hikaricp_connections_acquire'),
                'pool_p95_hist_ms':quantile_ms(histogram(folder,'hikaricp_connections_acquire')),
                'student_p95_hist_ms':quantile_ms(histogram(folder,'enrollment_student_lock_wait')),
                'section_wait_p95_hist_ms':quantile_ms(histogram(folder,'enrollment_section_lock_wait')),
                'student_mean_ms':timer(folder,'enrollment_student_lock_wait'),
                'section_wait_mean_ms':timer(folder,'enrollment_section_lock_wait'),
                'section_hold_mean_ms':timer(folder,'enrollment_section_lock_hold'),
                'academic_mean_ms':timer(folder,'enrollment_academic_checks'),
                'root_mean_ms':timer(folder,'enrollment_transaction'),'commit_mean_ms':timer(folder,'enrollment_commit')}
        rows.append(values)
    return rows


def plot(rows,folder):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,3,figsize=(15,8.5),layout='constrained')
    fig.suptitle('SDU registration · Phase 6 measured small HTTP baseline',fontsize=17,fontweight='bold')
    core=[r for r in rows if r['run'].startswith(('controlled','arrival','ramp','spike')) or 'hot100' in r['run']]
    def family(name):return 'Normal' if name.startswith('controlled') else 'Read arrivals' if name.startswith(('arrival','ramp')) else 'Spike' if name.startswith('spike') else 'Hot section'
    colors={'Normal':'#277da1','Read arrivals':'#43aa8b','Spike':'#f8961e','Hot section':'#9b5de5'}
    for ax,key,title,unit in [(axes[0,0],'p95_ms','HTTP p95 by achieved RPS','Milliseconds'),(axes[0,1],'p99_ms','HTTP p99 by achieved RPS','Milliseconds'),(axes[0,2],'backend_peak_sampled_cpu_percent','Backend CPU by achieved RPS','Docker CPU % (100 = one core)')]:
        for label,color in colors.items():
            data=[r for r in core if family(r['run'])==label and r[key] is not None]
            ax.scatter([r['rps'] for r in data],[r[key] for r in data],s=55,label=label,color=color,alpha=.85)
        ax.set(title=title,xlabel='Achieved HTTP requests/s',ylabel=unit);ax.grid(alpha=.2)
    axes[0,0].legend(fontsize=8)
    pending=load(RESULTS/'controlled-normal10-r2','monitoring.json')['hikari_pending']['data']['result']
    ax=axes[1,0]
    for row in pending:
        series=row['values'];start=series[0][0];ax.plot([t-start for t,v in series],[float(v) for t,v in series],marker='o',color='#277da1')
    ax.set(title='Hikari pending · normal run 2',xlabel='Seconds from first sample',ylabel='Waiting callers',ylim=(-.05,1));ax.grid(alpha=.2)
    ax=axes[1,1]
    for mode,color in [('late','#277da1'),('early','#f3722c')]:
        cumulative={}
        for name in HOT:
            if name.startswith(mode):
                for bound,count in histogram(RESULTS/name,'enrollment_section_lock_wait').items():cumulative[bound]=cumulative.get(bound,0)+count
        total=cumulative.get(float('inf'),max(cumulative.values(),default=0));keys=[k for k in sorted(cumulative) if 0.001<=k<=1]
        if total:ax.step([x*1000 for x in keys],[100*cumulative[x]/total for x in keys],where='post',label=mode,color=color)
    ax.set(title='Section lock query duration CDF',xlabel='Milliseconds (log scale)',ylabel='Cumulative %',xscale='log',ylim=(0,103));ax.grid(alpha=.2);ax.legend()
    ax=axes[1,2];items=[r for r in rows if r['run'] in HOT];names=[r['run'].replace('-hot100-r',' ') for r in items]
    wins=[r['new_enrollments'] for r in items];denials=[r['business_denials'] for r in items];errors=[r['technical']+r['network']+r['rate_limited']+r['unavailable'] for r in items]
    ax.bar(names,wins,label='ENROLLED',color='#43aa8b');ax.bar(names,denials,bottom=wins,label='SECTION_FULL',color='#f9c74f')
    ax.bar(names,errors,bottom=[a+b for a,b in zip(wins,denials)],label='Technical / rejected',color='#f94144')
    ax.set(title='Hot allocation outcomes',ylabel='Distinct HTTP intentions');ax.legend(fontsize=8);ax.grid(axis='y',alpha=.2)
    fig.savefig(folder/'baseline.png',dpi=160,facecolor='white')


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--plot',action='store_true');args=parser.parse_args()
    rows=analyse();folder=ROOT/'docs/phase6-assets';folder.mkdir(exist_ok=True)
    (folder/'baseline.json').write_text(json.dumps(rows,indent=2))
    with (folder/'baseline.csv').open('w',newline='') as output:
        writer=csv.DictWriter(output,fieldnames=rows[0].keys());writer.writeheader();writer.writerows(rows)
    if args.plot:plot(rows,folder)
    for row in rows:print(row['run'],round(row['rps'],2),round(row['p95_ms'],2),round(row['p99_ms'],2),row['new_enrollments'],row['business_denials'],row['unexpected_rate'])
