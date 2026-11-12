"""Post-test recovery after quiescence and journal drain; never a measured workload or user-facing auto-cancel."""
import gzip
import time
import ssl
import urllib.request
from worker import NoRedirect


def recovery_opener():
    """Create one verified trust context per recovery invocation, not per queued journey."""
    context=ssl.create_default_context()
    context.set_alpn_protocols(["http/1.1"])
    def create():
        return urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),
                                          urllib.request.HTTPSHandler(context=context))
    return create

from mixed_recovery import pending_journeys, recover


def recover_memory(ledger, actors, origin, headers, opener, phase, checkpoint=lambda rows, state: None):
    # Keep every attempt and every generated withdrawal key in the checkpoint. A later pass derives
    # its next action from the combined ledger, so it cannot invent a replacement key after a lost reply.
    started = time.monotonic()
    initial = len(pending_journeys(ledger))
    combined = ledger
    fragments = []
    deadline = time.monotonic() + 90
    total_requests = total_replays = 0
    rounds = []
    for index in range(3):
        pending = pending_journeys(combined)
        remaining = int(deadline - time.monotonic())
        if not pending or remaining < 1: break
        fragment, summary = recover(combined, actors, origin, headers, opener, phase, max_journeys=2500,
            recovery_seconds=min(30,remaining), workers=16, attempt_limit=8, withdrawals_first=True)
        total_requests += summary['requests']; total_replays += summary['replays']
        rounds.append(summary)
        if fragment:
            fragments.append(fragment)
            combined=gzip.compress(b'\n'.join([gzip.decompress(ledger),*(gzip.decompress(x) for x in fragments)]))
        unresolved = len(pending_journeys(combined))
        checkpoint(combined,dict(round=index+1,remaining=unresolved))
        if unresolved == 0 or summary['requests'] == 0: break
    remaining = pending_journeys(combined)
    return (gzip.compress(b'\n'.join(gzip.decompress(x) for x in fragments)) if fragments else None), {
        'pending':initial,'resolved':initial if not remaining else max(0,initial-len(remaining)),
        'remaining':len(remaining),'complete':not remaining,'requests':total_requests,'replays':total_replays,
        'rounds':rounds,'elapsed_seconds':time.monotonic()-started,'concurrency':16,'deadline_seconds':90,'projection_drained_before_start':True,
        'unresolved':remaining,
    }
