"""Adversarial unit tests of the independent trace oracle; no native runtime needed."""
from copy import deepcopy
import unittest
from run import oracle, tag, N

CASE=dict(source_ms=5,compute_ms=0,omit_seq=-1,mutant=0)
def fixture():
    records=[]
    for seq in range(1,N+1):
        t=tag(seq)
        for event,offset,began in [('source_start',0,0),('produced',5,0),('accepted',6,6),
                                   ('decision_start',7,7),('decision_end',8,7),('run',9,9)]:
            records.append(dict(event=event,seq=seq,value=1,logical_ns=t,microstep=0,
                                intended_ns=t,intended_microstep=0,physical_ns=t+offset*1_000_000,
                                began_ns=t+began*1_000_000))
    return records

def codes(records,case=CASE):
    return {v['contract'] for v in oracle(records,case)}

class OracleTests(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(codes(fixture()),set())
    def test_missing_terminal(self):
        self.assertIn('COMPLETE_TRACE',codes([r for r in fixture() if not (r['seq']==2 and r['event']=='run')]))
    def test_duplicate_terminal(self):
        r=fixture();r.append(deepcopy(r[-1]));self.assertIn('COMPLETE_TRACE',codes(r))
    def test_unsafe_run(self):
        r=fixture();r=[x for x in r if not (x['event']=='accepted' and x['seq']==2)]
        self.assertIn('UNSAFE_RUN',codes(r))
    def test_unnecessary_hold(self):
        r=fixture();r[-1]['event']='hold_missing';self.assertIn('UNNECESSARY_HOLD',codes(r))
    def test_early_deadline(self):
        r=fixture();r[-1]['event']='hold_deadline';self.assertIn('EARLY_DEADLINE',codes(r))
    def test_late_run(self):
        r=fixture();r[-1]['physical_ns']=tag(6)+101_000_000;self.assertIn('LATE_RUN',codes(r))
    def test_requested_omission_not_honored(self):
        self.assertIn('FIXTURE',codes(fixture(),dict(CASE,omit_seq=3)))
    def test_unexpected_omission(self):
        r=fixture();r[1]['event']='omitted';self.assertIn('FIXTURE',codes(r))
    def test_insufficient_elapsed(self):
        r=fixture();r[1]['physical_ns']-=1;self.assertIn('PHYSICAL_INJECTION',codes(r))
    def test_changed_tag(self):
        r=fixture();r[1]['logical_ns']+=1;self.assertIn('UNCHANGED_LOGICAL_TAG',codes(r))
    def test_wrong_intended_tag(self):
        r=fixture();r[1]['intended_ns']+=1;self.assertIn('INTENDED_TAG',codes(r))
    def test_extra_sequence(self):
        r=fixture();extra=deepcopy(r[-1]);extra['seq']=7;r.append(extra);self.assertIn('COMPLETE_TRACE',codes(r))
    def test_bad_tardy_tag(self):
        r=fixture();r[2]['event']='tardy';self.assertIn('TARDY_TAG',codes(r))

if __name__=='__main__': unittest.main()
