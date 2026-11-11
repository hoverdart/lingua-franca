#!/usr/bin/env python3
"""Reparse raw logs, enforce the full run plan, and test design-predicted separated regimes."""
from collections import Counter
import json
from pathlib import Path
import sys
from run import N, SOURCE, oracle, sha, summary

def validate(results):
    plan=json.loads((results/'plan.json').read_text());meta=json.loads((results/'build.json').read_text())
    runs=[];errors=[];hypotheses=[]
    if meta['template_sha256']!=sha(SOURCE):errors.append('LF source changed since execution')
    for wait, receipt in meta['profiles'].items():
        rendered=SOURCE.read_text().replace('@maxwait(20 ms)',f'@maxwait({wait if wait=="forever" else wait+" ms"})')
        import hashlib
        if hashlib.sha256(rendered.encode()).hexdigest()!=receipt['source_sha256']:
            errors.append(f'Profile source hash mismatch for maxwait {wait}')
    archived=results/'harness-at-execution.py'
    if not archived.exists() or sha(archived)!=meta['runner_sha256']:errors.append('Archived execution harness missing or hash mismatch')
    for i,case in enumerate(plan['cases']):
        directory=results/f'run-{i:03d}'
        if not (directory/'result.json').exists():
            errors.append(f'Missing completed run {i}');continue
        old=json.loads((directory/'result.json').read_text())
        if old['case']!=case:errors.append(f'Plan case mismatch run {i}')
        records=[]
        for name in ('adapter','workflow'):
            for line in (directory/f'federate__{name}.log').read_text().splitlines():
                if line.startswith('EXPERIMENT '):
                    records.append(dict(json.loads(line[len('EXPERIMENT '):]),federate=name))
        records.sort(key=lambda r:(r['physical_ns'],r['federate']))
        if records!=old['records']:errors.append(f'Raw log / saved record mismatch run {i}')
        violations=oracle(records,case);counts=Counter(r['event'] for r in records)
        expected_contracts={'UNSAFE_RUN'} if case['mutant'] else set()
        passed={v['contract'] for v in violations}==expected_contracts and (not case['mutant'] or len(violations)==N)
        # Broadly separated physical regimes, not exact timing predictions.
        # Omission is handled separately: known absence may be learned promptly
        # or from a later tag, so no pre-assumption about HOLD reason/latency.
        if case['omit_seq']!=-1:
            expected=dict(run=5,holds=1,tardy=0)
            actual=dict(run=counts['run'],holds=counts['hold_missing']+counts['hold_deadline'],tardy=counts['tardy'])
        else:
            late_input=case['wait']!='forever' and case['source_ms']>int(case['wait'])
            if case['mutant']:
                expected=dict(run=6,hold_missing=0,hold_deadline=0,tardy=6)
            elif case['compute_ms']>=100 or (case['source_ms']>=100 and not late_input):
                expected=dict(run=0,hold_missing=0,hold_deadline=6,tardy=0)
            elif late_input:
                expected=dict(run=0,hold_missing=6,hold_deadline=0,tardy=6)
            else:
                expected=dict(run=6,hold_missing=0,hold_deadline=0,tardy=0)
            actual={k:counts[k] for k in expected}
        hypotheses.append(dict(run=i,case=case,expected=expected,actual=actual,matched=expected==actual))
        if not passed:errors.append(f'Contract oracle failure run {i}')
        if expected!=actual:errors.append(f'Predicted regime mismatch run {i}; inspect observed timings')
        runs.append(dict(old,records=records,counts=dict(counts),violations=violations,passed=passed))
    out=summary(runs,results,meta,plan['seed']) if runs else {}
    report=dict(status='pass' if not errors and len(runs)==len(plan['cases']) else 'fail',
                completed_runs=len(runs),planned_runs=len(plan['cases']),errors=errors,
                validator_sha256=sha(__file__),oracle_sha256=sha(Path(__file__).with_name('run.py')),
                original_execution_harness_sha256=meta['runner_sha256'],hypotheses=hypotheses,
                explanation='Final oracle reparsed raw per-federate logs; execution used archived harness. No measurements changed.')
    (results/'validation.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='hypotheses'},indent=2))
    return 0 if report['status']=='pass' else 1

if __name__=='__main__':
    raise SystemExit(validate(Path(sys.argv[1]).resolve()))
