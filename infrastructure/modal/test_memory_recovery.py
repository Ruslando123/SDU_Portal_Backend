"""Recovery must preserve keys and finish both parts of an interrupted registration journey."""
import gzip
import json
import unittest
from unittest.mock import patch
from itertools import chain, repeat
from mixed_recovery import recover
from memory_recovery import recovery_opener
import ssl
from memory_recovery import recover_memory
from mixed_evidence import parse_client_ledger
from mixed_recovery import pending_journeys

class RecoveryTests(unittest.TestCase):
    def test_lost_followup_delete_keeps_key_across_recovery_passes(self):
        principal='81000000-0000-4000-8000-000000000001'
        section='90000000-0000-4000-8000-000000000001'
        episode='eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee'
        key='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
        trace='10000000-0000-4000-8000-000000000001'
        ledger=gzip.compress((f'SDU_START {trace} enroll {key} 1 {principal} {section} 1000\n'
            f'SDU_RESULT {trace} enroll 0 timeout unattributed 1000 5000 - - 1 -').encode())
        actor=dict(student='80000000-0000-4000-8000-000000000001',cookie='synthetic',header='X-CSRF-TOKEN',csrf='fixture')
        calls=[];checkpoints=[]
        class Response:
            headers={'X-Sdu-Benchmark-Hop':'backend','Idempotency-Replayed':'true'}
            def __init__(self,status,body): self.code=status;self.body=json.dumps(body).encode()
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def read(self,maximum): return self.body[:maximum]
        class Client:
            def open(self,request,timeout):
                calls.append((request.method,request.get_header('Idempotency-key')))
                if request.method=='POST':
                    return Response(201,dict(code='ENROLLED',enrollmentId=episode,sectionId=section,correlationId=trace))
                if sum(method=='DELETE' for method,_ in calls)<=2: raise TimeoutError('lost after commit')
                return Response(200,dict(code='WITHDRAWN',enrollmentId=episode,correlationId=request.get_header('X-correlation-id')))
        result,state=recover_memory(ledger,[actor],'https://test.invalid',{},Client,'warmup',lambda rows,state:checkpoints.append(rows))
        self.assertTrue(state['complete']);self.assertEqual(1,state['resolved']);self.assertEqual(2,len(state['rounds']))
        self.assertEqual([('POST',key)],[(method,k) for method,k in calls if method=='POST'])
        self.assertEqual(1,len({k for method,k in calls if method=='DELETE'}))
        combined=gzip.compress(gzip.decompress(ledger)+b'\n'+gzip.decompress(result))
        self.assertEqual([],pending_journeys(combined))
        starts,_=parse_client_ledger(result)
        self.assertEqual([1,2,3],[s['attempt'] for s in starts.values() if s['route']=='withdraw'])
        self.assertEqual(1,len(pending_journeys(checkpoints[0])))

    def test_expired_queued_work_does_not_build_a_tls_client(self):
        actor=dict(student='80000000-0000-4000-8000-000000000001')
        intent=dict(principal='81000000-0000-4000-8000-000000000001',started_ms=1)
        with patch('mixed_recovery.pending_journeys',return_value=[intent]*2500), patch('mixed_recovery.time.monotonic',side_effect=chain([0],repeat(1000))):
            _,state=recover(b'', [actor], 'https://test.invalid',{},lambda:self.fail('Expired work constructed a client'),
                            'warmup',max_journeys=2500,workers=16)
        self.assertEqual(0,state['requests'])

    def test_recovery_reuses_verified_tls_context_without_following_redirects(self):
        factory=recovery_opener(); clients=[factory() for _ in range(2)]
        contexts=[next(h._context for h in c.handlers if isinstance(h, __import__('urllib.request',fromlist=['HTTPSHandler']).HTTPSHandler)) for c in clients]
        self.assertIs(contexts[0],contexts[1]);self.assertTrue(contexts[0].check_hostname)
        self.assertEqual(ssl.CERT_REQUIRED,contexts[0].verify_mode)
        for c in clients:
            handler=next(h for h in c.handlers if isinstance(h,__import__('worker').NoRedirect))
            self.assertIsNone(handler.redirect_request(None,None,302,'',{},'https://other.invalid'))

    def test_empty_journey_set_performs_no_requests(self):
        _,state=recover_memory(gzip.compress(b''),[],'https://test.invalid',{},lambda:self.fail('Unexpected request'),'warmup')
        self.assertTrue(state['complete']);self.assertEqual(0,state['requests'])

if __name__=='__main__': unittest.main()
