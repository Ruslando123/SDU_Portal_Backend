"""No cloud resources are created by these bounded smoke-runner checks."""
import unittest
from unittest.mock import patch
import json
from memory_benchmark import cost_plan, reconcile
from smoke import VIOLATIONS


class MemoryRunnerSafety(unittest.TestCase):
    def test_cost_uses_sandbox_rates_and_fails_closed(self):
        rates = dict(cpu_hour_cost=.0473, mem_gib_hour_cost=.008,
                     cpu_hour_cost_sandbox=.1419, mem_gib_hour_cost_sandbox=.024, egress_gib_cost=.04)
        plan = cost_plan(rates)
        self.assertAlmostEqual(.34491024305555557, plan['estimate_usd'])
        self.assertIsNone(plan['actual_usd'])
        with self.assertRaises(ValueError): cost_plan({**rates, 'cpu_hour_cost_sandbox': 100})
        with self.assertRaises(ValueError): cost_plan({**rates, 'mem_gib_hour_cost': float('nan')})

    def test_http_success_is_insufficient_without_full_projection(self):
        counts = dict(active=0, withdrawn=0, audit=0, durable=0, ledger=0,
                      fsync='on', sync='on', full_page_writes='on')
        with patch('memory_benchmark.query', side_effect=['\n'.join('0' for _ in VIOLATIONS), '[]', json.dumps(counts)]):
            result = reconcile(None, None, [])
        self.assertFalse(result['passed'])
        self.assertEqual(0, result['unique_durable_operations'])

    def test_reconciliation_checks_principal_target_body_and_replays(self):
        operations, durable = [], []
        for vu in range(1, 11):
            for operation in ('ENROLL', 'WITHDRAW'):
                key = f'{operation}-{vu}'
                body = dict(code=operation, enrollmentId=str(vu))
                method = 'POST' if operation == 'ENROLL' else 'DELETE'
                path = '/api/v1/sections/section/enrollments' if operation == 'ENROLL' else f'/api/v1/enrollments/{vu}'
                entry = dict(vu=vu, key=key, method=method, path=path, httpStatus=201 if method=='POST' else 200, body=body, replayed=False)
                operations.append(entry)
                if operation=='ENROLL': operations.append({**entry, 'replayed':True})
                durable.append(dict(key=key, principal=f'81000000-0000-4000-8000-{vu:012d}', type=operation,
                                    target='section' if method=='POST' else str(vu), status=entry['httpStatus'], body=body))
        counts = dict(active=0,withdrawn=10,audit=20,durable=20,ledger=0,fsync='on',sync='on',full_page_writes='on')
        def verify():
            with patch('memory_benchmark.query', side_effect=['\n'.join('0' for _ in VIOLATIONS), json.dumps(durable), json.dumps(counts)]):
                return reconcile(None,None,operations)
        self.assertTrue(verify()['passed'])
        durable[0]['principal']='different-principal'
        self.assertFalse(verify()['passed'])
        self.assertEqual(['ENROLL-1','ENROLL-1'],verify()['mismatches'])

if __name__ == '__main__': unittest.main()
