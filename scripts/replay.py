#!/usr/bin/env python3
"""Build and exercise the actual LF executable, checking persisted terminal results."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from replay.coordinator import MUTANTS
from replay.scenario import load_scenario


def environment():
    env = os.environ.copy()
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env["LF_REPLAY_ROOT"] = str(ROOT)
    return env


def build():
    env = environment()
    compiler = shutil.which("lfc", path=env["PATH"])
    if not compiler:
        raise RuntimeError("lfc not found; run this script with .venv/bin/python")
    receipt = ROOT / "artifacts/build.json"
    receipt.unlink(missing_ok=True)
    subprocess.run([compiler, "src/Main.lf"], cwd=ROOT, env=env, check=True, timeout=180)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    version = subprocess.run([compiler, "--version"], env=env, text=True, capture_output=True,
                             check=True, timeout=15).stdout.strip()
    receipt.write_text(json.dumps({"compiler": version,
                                  "lf_source_sha256": hashlib.sha256((ROOT / "src/Main.lf").read_bytes()).hexdigest()},
                                 indent=2) + "\n")


def run(path, report, mutant="none", workers=1):
    path, report = Path(path).resolve(), Path(report).resolve()
    if path == report:
        raise ValueError("Report destination must differ from the input scenario")
    load_scenario(path)  # Reject malformed ingress before invoking native code.
    if not (ROOT / "bin/Main").is_file():
        raise RuntimeError("Build first: .venv/bin/python scripts/replay.py build")
    receipt_path = ROOT / "artifacts/build.json"
    if not receipt_path.is_file():
        raise RuntimeError("Build receipt missing; run: .venv/bin/python scripts/replay.py build")
    receipt = json.loads(receipt_path.read_text())
    if receipt["lf_source_sha256"] != hashlib.sha256((ROOT / "src/Main.lf").read_bytes()).hexdigest():
        raise RuntimeError("LF source changed since compilation; run: .venv/bin/python scripts/replay.py build")
    env = environment()
    env.update(LF_REPLAY_SCENARIO=str(path), LF_REPLAY_REPORT=str(report), LF_REPLAY_MUTANT=mutant)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.unlink(missing_ok=True)  # An old successful report cannot mask an incomplete run.
    process = subprocess.run([str(ROOT / "bin/Main"), "--workers", str(workers)],
                             cwd=ROOT, env=env, text=True, capture_output=True, timeout=30)
    if process.returncode or not report.exists():
        raise RuntimeError(f"LF replay did not finish ({process.returncode}):\n{process.stdout}\n{process.stderr}")
    result = json.loads(report.read_text())
    if len(result.get("records", [])) != len(load_scenario(path)["events"]):
        raise RuntimeError("LF replay produced an incomplete report")
    result["provenance"]["build"] = receipt
    report.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(f"{result['status'].upper():4} {path.stem} [{mutant}, {workers} worker(s)] -> {report.relative_to(ROOT) if report.is_relative_to(ROOT) else report}")
    return result


def verify(repeats):
    build()
    count = 0
    for path in sorted((ROOT / "scenarios").glob("*.json")):
        baseline = None
        for repeat in range(repeats):
            workers = 1 if repeat % 2 == 0 else 4
            result = run(path, ROOT / f"artifacts/verify/{path.stem}-{repeat}.json", workers=workers)
            if result["status"] != "pass":
                raise RuntimeError(f"Correct model violated contracts: {result['violations']}")
            # Compare full semantic records including LF tags, excluding physical execution order.
            if baseline is not None and result != baseline:
                raise RuntimeError(f"Replay diverged: {path.name}, repetition {repeat}")
            baseline = result
            count += 1
    cases = [("stale-unload", "stale-unload", "SESSION_OWNERSHIP"),
             ("duplicate", "duplicate-credit", "DUPLICATE_IDEMPOTENCE"),
             ("wrong-program", "wrong-program", "PROGRAM_MATCH"),
             ("gap", "bridge-gap", "COVERAGE_CONTINUITY")]
    for scenario, mutant, contract in cases:
        result = run(ROOT / f"scenarios/{scenario}.json", ROOT / f"artifacts/verify/mutant-{mutant}.json", mutant)
        if result["status"] != "fail" or contract not in {v["contract"] for v in result["violations"]}:
            raise RuntimeError(f"Monitor failed to detect {mutant} via {contract}")
        print(f"     Detected {contract} as expected")
    print(f"Verified {count} correct-model LF executions and {len(cases)} detected mutants.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("build")
    replay = commands.add_parser("run")
    replay.add_argument("scenario", nargs="?", type=Path, default=ROOT / "scenarios/normal.json")
    replay.add_argument("--mutant", choices=MUTANTS, default="none")
    replay.add_argument("--workers", type=int, choices=range(1, 17), default=1)
    replay.add_argument("--report", type=Path, default=ROOT / "artifacts/report.json")
    suite = commands.add_parser("verify")
    suite.add_argument("--repeats", type=int, choices=range(2, 11), default=3)
    args = parser.parse_args()
    try:
        if args.command == "build":
            build()
        elif args.command == "verify":
            verify(args.repeats)
        else:
            result = run(args.scenario, args.report, args.mutant, args.workers)
            if result["violations"]:
                first = result["violations"][0]
                print(f"{first['contract']} at {first['event_id']}: {first['message']}")
                return 1
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Replay error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
