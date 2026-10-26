"""Boundary and adversarial checks; full LF executions live in scripts/replay.py verify."""

from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from replay.coordinator import Coordinator
from replay.monitor import ContractMonitor
from replay.scenario import ROOT, load_scenario


class ReplayTests(unittest.TestCase):
    def replay(self, name, mutant="none", edit=None):
        scenario = load_scenario(ROOT / f"scenarios/{name}.json")
        model = Coordinator(mutant, scenario["max_gap_ms"])
        monitor = ContractMonitor(scenario)
        for index, event in enumerate(scenario["events"]):
            decision = model.apply(event)
            if edit:
                edit(index, decision)
            monitor.observe(event, decision, {"time_ns": event["receive_ms"] * 1_000_000, "microstep": index + 1})
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            return monitor.finish(Path(temporary) / "report.json")

    def test_fixture_oracles_and_contracts(self):
        for path in sorted((ROOT / "scenarios").glob("*.json")):
            with self.subTest(scenario=path.stem):
                report = self.replay(path.stem)
                self.assertEqual(report["violations"], [])

    def test_each_intentional_defect_has_a_specific_counterexample(self):
        for name, mutant, contract in [
            ("stale-unload", "stale-unload", "SESSION_OWNERSHIP"),
            ("duplicate", "duplicate-credit", "DUPLICATE_IDEMPOTENCE"),
            ("wrong-program", "wrong-program", "PROGRAM_MATCH"),
            ("gap", "bridge-gap", "COVERAGE_CONTINUITY"),
        ]:
            with self.subTest(mutant=mutant):
                report = self.replay(name, mutant)
                self.assertIn(contract, {v["contract"] for v in report["violations"]})
                first = report["counterexample"]["violation"]
                self.assertEqual(report["counterexample"]["input_prefix"][-1]["id"], first["event_id"])

    def test_monitor_rejects_tampered_accounting_without_using_model_state(self):
        def corrupt(index, decision):
            if index == 2:
                decision["totals_ms"]["A"] += 100
        self.assertIn("ACCOUNTING", {v["contract"] for v in self.replay("normal", edit=corrupt)["violations"]})

    def test_monitor_does_not_trust_decision_identity(self):
        def corrupt(index, decision):
            decision["event_id"] = "unrelated"
        self.assertIn("DECISION_ID", {v["contract"] for v in self.replay("normal", edit=corrupt)["violations"]})

    def test_missing_event_is_not_a_successful_replay(self):
        monitor = ContractMonitor(load_scenario(ROOT / "scenarios/normal.json"))
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            report = monitor.finish(Path(temporary) / "report.json")
        self.assertEqual(report["status"], "fail")
        self.assertIn("COMPLETE_REPLAY", {v["contract"] for v in report["violations"]})

    def test_invalid_ingress_is_rejected_before_native_runtime(self):
        original = load_scenario(ROOT / "scenarios/normal.json")
        changes = [lambda d: d["events"][0].update(receive_ms=True),
                   lambda d: d["events"][1].update(receive_ms=-1),
                   lambda d: d["events"][1].update(source_ms=999),
                   lambda d: d["events"][2].update(receive_ms=5, source_ms=5),
                   lambda d: d["events"][0].update(kind="machine-motion"),
                   lambda d: d["events"][1].update(available="yes"),
                   lambda d: d.update(events=[]),
                   lambda d: d.update(max_gap_ms=0),
                   lambda d: d.pop("expected_active_session")]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "invalid.json"
            for change in changes:
                data = deepcopy(original)
                change(data)
                path.write_text(json.dumps(data))
                with self.subTest(data=data), self.assertRaises(ValueError):
                    load_scenario(path)

    def test_duplicate_unload_cannot_close_later_assignment(self):
        scenario = load_scenario(ROOT / "scenarios/stale-unload.json")
        model = Coordinator(max_gap_ms=20)
        for event in scenario["events"][:3]:
            model.apply(event)
        retry = deepcopy(scenario["events"][1])
        retry["receive_ms"] = 30
        result = model.apply(retry)
        self.assertEqual(result["reason"], "duplicate-command")
        self.assertEqual(result["active"]["session"], "B")

    def test_load_conflict_preserves_existing_assignment(self):
        scenario = load_scenario(ROOT / "scenarios/stale-unload.json")
        model = Coordinator(max_gap_ms=20)
        model.apply(scenario["events"][0])
        result = model.apply(scenario["events"][2])
        self.assertEqual(result["reason"], "load-conflict")
        self.assertEqual(result["active"]["session"], "A")

    def test_delayed_old_epoch_restart_does_not_reactivate_retired_adapter(self):
        scenario = load_scenario(ROOT / "scenarios/restart.json")
        model = Coordinator(max_gap_ms=20)
        for event in scenario["events"][:6]:
            model.apply(event)
        old_restart = {"id": "old-restart", "kind": "restart", "receive_ms": 41, "source_ms": 0,
                       "instance": "boot-1"}
        self.assertEqual(model.apply(old_restart)["reason"], "retired-instance")
        self.assertEqual(model.instance, "boot-2")

    def test_sample_captured_before_load_cannot_establish_session_execution(self):
        scenario = load_scenario(ROOT / "scenarios/normal.json")
        model = Coordinator(max_gap_ms=20)
        load = deepcopy(scenario["events"][0])
        load.update(receive_ms=10, source_ms=10)
        model.apply(load)
        first, second = deepcopy(scenario["events"][1:3])
        first.update(receive_ms=11, source_ms=9)
        second.update(receive_ms=20, source_ms=20)
        model.apply(first)
        self.assertEqual(model.apply(second)["credit_ms"], 0)


class CommandLineTests(unittest.TestCase):
    def invoke(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "scripts/replay.py"), *args],
                              cwd=ROOT, text=True, capture_output=True, timeout=30)

    def test_contract_failure_has_nonzero_exit_and_reviewable_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "failure.json"
            result = self.invoke("run", "scenarios/stale-unload.json", "--mutant", "stale-unload", "--report", str(path))
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("SESSION_OWNERSHIP", result.stdout)
            self.assertEqual(json.loads(path.read_text())["status"], "fail")

    def test_invalid_input_has_distinct_exit_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "invalid.json"
            path.write_text("{}")
            result = self.invoke("run", str(path))
            self.assertEqual(result.returncode, 2, result.stderr)

    def test_report_cannot_overwrite_input_scenario(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scenario.json"
            original = (ROOT / "scenarios/normal.json").read_text()
            path.write_text(original)
            result = self.invoke("run", str(path), "--report", str(path))
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(path.read_text(), original)

    def test_native_startup_error_cannot_reuse_old_success_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "old.json"
            path.write_text('{"status":"pass"}')
            # Native reactions fail before shutdown when the mutant is invalid.
            # Bypass argparse to test the run/report boundary directly.
            from scripts.replay import run
            with self.assertRaises(RuntimeError):
                run(ROOT / "scenarios/normal.json", path, mutant="unknown")
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
