#!/usr/bin/env python3
"""Build and run actual LF federations; independently check their emitted traces."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import re
import signal
import statistics
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE = HERE / 'src/PhysicalTime.lf'
DEADLINE_NS = 100_000_000
N = 6

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def tag(seq):
    return 200_000_000 + (seq - 1) * 400_000_000

def oracle(records, case):
    """No application state or fresh/allow flag is used to authorize a RUN."""
    violations = []
    def fail(code, seq, detail):
        violations.append(dict(contract=code, seq=seq, detail=detail))
    for seq in range(1, N + 1):
        events = [r for r in records if r['seq'] == seq]
        groups = defaultdict(list)
        for r in events:
            groups[r['event']].append(r)
        for event in ('source_start', 'decision_start', 'decision_end'):
            if len(groups[event]) != 1:
                fail('COMPLETE_TRACE', seq, f'{event}: {len(groups[event])}')
        sources = groups['produced'] + groups['omitted']
        outputs = groups['run'] + groups['hold_missing'] + groups['hold_deadline']
        if len(sources) != 1 or len(outputs) != 1:
            fail('COMPLETE_TRACE', seq, f'sources={len(sources)}, outputs={len(outputs)}')
            continue
        source, out = sources[0], outputs[0]
        delivered = groups['accepted'] + groups['tardy']
        if len(delivered) != (source['event'] == 'produced'):
            fail('DELIVERY_ACCOUNTING', seq, f'{len(delivered)} receive records')
        for r in events:
            if r['event'] != 'tardy' and (r['logical_ns'], r['microstep']) != (tag(seq), 0):
                fail('UNCHANGED_LOGICAL_TAG', seq, r['event'])
            if r['intended_ns'] != tag(seq) or r['intended_microstep'] != 0:
                fail('INTENDED_TAG', seq, r['event'])
        for r in groups['tardy']:
            if (r['logical_ns'],r['microstep']) <= (r['intended_ns'],r['intended_microstep']):
                fail('TARDY_TAG', seq, str(r))
        for r, requested in [(source,case['source_ms'])] + [(r,case['compute_ms']) for r in groups['decision_end']]:
            if r['physical_ns'] - r['began_ns'] < requested * 1_000_000:
                fail('PHYSICAL_INJECTION', seq, f'{requested} ms not elapsed')
        # Membership in actual normal receive path, matching tag and sequence,
        # and receive before decision, rather than trusting decision.value.
        start = groups['decision_start'][0] if len(groups['decision_start']) == 1 else None
        fresh = any(r['logical_ns'] == tag(seq) and r['microstep'] == 0 and
                    start and r['physical_ns'] <= start['physical_ns'] for r in groups['accepted'])
        if out['event'] == 'run' and not fresh:
            fail('UNSAFE_RUN', seq, 'RUN without an on-tag accepted observation')
        if out['event'] == 'run' and out['physical_ns'] - tag(seq) > DEADLINE_NS:
            fail('LATE_RUN', seq, 'RUN logged after 100 ms reaction-start budget')
        if out['event'] == 'hold_missing' and fresh:
            fail('UNNECESSARY_HOLD', seq, 'on-tag observation was available')
        if out['event'] == 'hold_deadline' and out['physical_ns'] - tag(seq) <= DEADLINE_NS:
            fail('EARLY_DEADLINE', seq, 'deadline handler before the deadline')
        if source['event'] == 'omitted' and seq != case['omit_seq']:
            fail('FIXTURE', seq, 'unexpected omission')
    if any(r['seq'] not in range(1,N+1) for r in records):
        fail('COMPLETE_TRACE', -1, 'unexpected sequence')
    return violations

def environment():
    env = os.environ.copy()
    env['PATH'] = str(ROOT/'.venv/bin') + os.pathsep + '/opt/homebrew/bin' + os.pathsep + env.get('PATH','')
    # Do not let a previous interactive experiment contaminate this one.
    for name in ('LF_SOURCE_MS','LF_COMPUTE_MS','LF_OMIT_SEQ','LF_UNSAFE_MUTANT'):
        env.pop(name,None)
    return env

def build(results):
    import shutil
    env = environment()
    compiler = shutil.which('lfc',path=env['PATH'])
    if not compiler:
        raise RuntimeError('lfc missing; use the repository virtual environment')
    metadata = dict(compiler=subprocess.check_output([compiler,'--version'],env=env,text=True).strip(),
                    compiler_path=compiler, platform=platform.platform(), python=sys.version,
                    base_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                    template_sha256=sha(SOURCE), runner_sha256=sha(__file__), profiles={})
    for wait in ('20','80','200','forever'):
        profile = HERE/'build'/f'w{wait}'
        src = profile/'src/PhysicalTime.lf'
        src.parent.mkdir(parents=True,exist_ok=True)
        src.write_text(SOURCE.read_text().replace('@maxwait(20 ms)',f'@maxwait({wait if wait=="forever" else wait+" ms"})'))
        log = results/f'build-w{wait}.log'
        with log.open('w') as f:
            subprocess.run([compiler,str(src)],cwd=profile,env=env,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=120)
        binary_dir=profile/'fed-gen/PhysicalTime/bin'
        metadata['profiles'][wait] = dict(source_sha256=sha(src),binaries={p.name:sha(p) for p in binary_dir.iterdir() if p.is_file()})
    (results/'build.json').write_text(json.dumps(metadata,indent=2)+'\n')
    return metadata

def cases(repeats,seed):
    base = [dict(wait=w,source_ms=s,compute_ms=0,omit_seq=-1,mutant=0,workers=1,kind='factorial')
            for w in ('20','80','200','forever') for s in (5,50,150)]
    base += [dict(wait=w,source_ms=5,compute_ms=150,omit_seq=-1,mutant=0,workers=1,kind='compute') for w in ('20','200')]
    base += [dict(wait=w,source_ms=5,compute_ms=0,omit_seq=3,mutant=0,workers=1,kind='omission') for w in ('20','forever')]
    base += [dict(wait='20',source_ms=50,compute_ms=0,omit_seq=-1,mutant=1,workers=1,kind='negative_control')]
    base += [dict(wait=w,source_ms=s,compute_ms=0,omit_seq=-1,mutant=0,workers=4,kind='worker_control') for w,s in [('20',5),('20',50),('200',150)]]
    result=[dict(c,repeat=r) for r in range(repeats) for c in base]
    random.Random(seed).shuffle(result)
    return result

def one(case,index,results):
    env = environment()
    env.update(LF_SOURCE_MS=str(case['source_ms']),LF_COMPUTE_MS=str(case['compute_ms']),
               LF_OMIT_SEQ=str(case['omit_seq']),LF_UNSAFE_MUTANT=str(case['mutant']))
    launcher=HERE/'build'/f'w{case["wait"]}'/'bin/PhysicalTime'
    # Separate files prevent cross-process stdout from corrupting JSON lines.
    run_dir=results/f'run-{index:03d}'
    run_dir.mkdir()
    started=time.monotonic()
    with (run_dir/'launcher.log').open('w') as f:
        p=subprocess.Popen([str(launcher),'-l','--workers',str(case['workers'])],cwd=run_dir,env=env,
                           stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            rc=p.wait(timeout=25)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid,signal.SIGTERM)
            p.wait(timeout=5)
            raise RuntimeError(f'{run_dir.name} exceeded 25 s; child process group stopped')
    if rc:
        raise RuntimeError(f'{run_dir.name}: launcher exit {rc}; inspect its logs')
    records=[]
    for name in ('adapter','workflow'):
        for line in (run_dir/f'federate__{name}.log').read_text().splitlines():
            if line.startswith('EXPERIMENT '):
                records.append(dict(json.loads(line[len('EXPERIMENT '):]),federate=name))
    records.sort(key=lambda r:(r['physical_ns'],r['federate']))
    violations=oracle(records,case)
    counts=Counter(r['event'] for r in records)
    expected={"UNSAFE_RUN"} if case['mutant'] else set()
    observed={v['contract'] for v in violations}
    passed=observed==expected and (not case['mutant'] or len(violations)==N)
    outputs=[r for r in records if r['event'] in ('run','hold_missing','hold_deadline')]
    result=dict(case=case,counts=dict(counts),passed=passed,violations=violations,records=records,
                elapsed_s=time.monotonic()-started,run=run_dir.name,
                output_lag_ms=[(r['physical_ns']-r['logical_ns'])/1e6 for r in outputs],
                injection_ms=[(r['physical_ns']-r['began_ns'])/1e6 for r in records if r['event'] in ('produced','omitted')])
    (run_dir/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(f'{index:02} {"PASS" if passed else "FAIL"} {case} {dict(counts)}',flush=True)
    return result

def summary(runs,results,meta,seed):
    grouped=defaultdict(list)
    for r in runs:
        c=r['case']; grouped[(c['kind'],c['wait'],c['source_ms'],c['compute_ms'],c['workers'])].append(r)
    rows=[]
    for (kind,w,s,c,workers),group in sorted(grouped.items()):
        counts=sum((Counter(r['counts']) for r in group),Counter())
        lags=[lag for r in group for lag in r['output_lag_ms']]
        row=dict(kind=kind,maxwait_ms=w,source_ms=s,compute_ms=c,workers=workers,runs=len(group),cycles=N*len(group),
                 run=counts['run'],hold_missing=counts['hold_missing'],hold_deadline=counts['hold_deadline'],
                 tardy=counts['tardy'],output_lag_median_ms=statistics.median(lags),output_lag_max_ms=max(lags))
        rows.append(row)
    out=dict(status='pass' if all(r['passed'] for r in runs) else 'fail',seed=seed,runs=len(runs),
             cycles=N*len(runs),metadata=meta,rows=rows)
    (results/'summary.json').write_text(json.dumps(out,indent=2)+'\n')
    import csv
    with (results/'summary.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    return out

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--seed',type=int,default=20261111)
    parser.add_argument('--output',type=Path,default=HERE/'results/measured-2026-11-11')
    args=parser.parse_args()
    if not 1<=args.repeats<=20: parser.error('repeats must be 1..20')
    results=args.output.resolve();results.mkdir(parents=True,exist_ok=False)
    matrix=cases(args.repeats,args.seed)
    (results/'plan.json').write_text(json.dumps(dict(seed=args.seed,cases=matrix),indent=2)+'\n')
    meta=build(results)
    runs=[]
    for i,case in enumerate(matrix):
        runs.append(one(case,i,results))
        summary(runs,results,meta,args.seed)
    out=summary(runs,results,meta,args.seed)
    print(json.dumps(dict(status=out['status'],runs=out['runs'],cycles=out['cycles']),indent=2))
    return 0 if out['status']=='pass' else 1

if __name__=='__main__':
    raise SystemExit(main())
