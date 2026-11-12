"""Pure local safety checks. Never creates Modal resources."""
import unittest
import tempfile
import json
from pathlib import Path
from evidence_transport import checkpoint_worker, read_worker_artifact
from unittest.mock import patch
from memory_load_benchmark import CASE, cost_plan, CAP
from memory_load_evidence import projection_stability, measurement_gate

class MemoryLoadSafety(unittest.TestCase):
    def test_fixed_resources_and_cost_include_four_bounded_invocations(self):
        rates=dict(cpu_hour_cost=.0473,mem_gib_hour_cost=.008,cpu_hour_cost_sandbox=.1419,mem_gib_hour_cost_sandbox=.024,egress_gib_cost=.04)
        self.assertAlmostEqual(2.4589688888888888,cost_plan(rates)['estimate_usd'])
        self.assertEqual((2500,24,80,5000),(CASE.vus,CASE.pool_size,CASE.tomcat_threads,CASE.students))
        with self.assertRaises(ValueError): cost_plan({**rates,'cpu_hour_cost_sandbox':10})
    def test_large_private_checkpoint_returns_only_a_small_manifest(self):
        class Volume:
            def commit(self): pass
            def read_file(self, name):
                yield (root/name.lstrip('/')).read_bytes()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'warmup').mkdir()
            payload=b'private session evidence' * 100000
            volume=Volume()
            manifest=checkpoint_worker(volume,root/'warmup',lambda:payload)
            self.assertLess(len(json.dumps(manifest)),256)
            self.assertNotIn('private session',json.dumps(manifest))
            self.assertEqual(payload,read_worker_artifact(volume,manifest,prefix='warmup/'))
            with self.assertRaises(FileExistsError): checkpoint_worker(volume,root/'warmup',lambda:payload)

    def test_backlog_growth_cannot_pass_as_throughput(self):
        def samples(growth):
            return ''.join(f'T\t{n*5000}\nM\tportal_memory_pending {growth*n}\nM\tportal_memory_confirmed_total {n*1000}\nM\tportal_memory_projected_total {n*(1000-growth)}\n' for n in range(24))
        metrics=dict(window_started_ms=0,window_ended_ms=120000,duration_seconds=120)
        self.assertTrue(projection_stability(samples(0),metrics)['passed'])
        self.assertFalse(projection_stability(samples(50),metrics)['passed'])
        self.assertFalse(projection_stability('',metrics)['passed'])
    def test_short_measurement_or_http_failure_does_not_pass(self):
        values=dict(duration_seconds=120,window_started_ms=1,window_ended_ms=120001,observed_vus_peak=2500,
                    requests=300000,errors=0,all_unexpected=0,interrupted_iterations=0,dropped_iterations=0,
                    p95_ms=500,p99_ms=700,offered_iterations=228000)
        payload=dict(exit=0,ledger=b'evidence',recovery=dict(pending=0,resolved=0))
        with patch('benchmark_worker.phase_metrics',return_value=values):
            self.assertTrue(measurement_gate(CASE,payload)['passed'])
            values['all_unexpected']=1
            self.assertFalse(measurement_gate(CASE,payload)['passed'])
            values['all_unexpected']=0;values['duration_seconds']=30
            self.assertFalse(measurement_gate(CASE,payload)['passed'])
if __name__=='__main__': unittest.main()
