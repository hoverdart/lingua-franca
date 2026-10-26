"""Input-driven contract checks, independent of the coordinator's transition code."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

from replay.scenario import ROOT


class ContractMonitor:
    def __init__(self, scenario):
        self.scenario = scenario
        self.records = []
        self.violations = []
        self.assignment = None
        self.loaded_sessions = set()
        self.command_ids = set()
        self.observation_ids = set()
        self.epoch = None
        self.retired_epochs = set()
        self.high_sequence = -1
        self.anchor = None
        self.accounted = {}
        self.assignment_since = None

    def check(self, condition, contract, message, event, tag):
        if not condition:
            self.violations.append({"contract": contract, "message": message,
                                    "event_id": event["id"], "tag": deepcopy(tag),
                                    "record_index": len(self.records)})

    def observe(self, event, decision, tag):
        check = lambda ok, code, message: self.check(ok, code, message, event, tag)
        index = len(self.records)
        check(index < len(self.scenario["events"]) and event == self.scenario["events"][index],
              "INGRESS_ORDER", "Delivered event differs from the scenario's next input")
        check(tag["time_ns"] == event["receive_ms"] * 1_000_000,
              "LOGICAL_DELIVERY", "LF delivery time differs from receive_ms")
        if self.records:
            previous_tag = self.records[-1]["tag"]
            check((tag["time_ns"], tag["microstep"]) >
                  (previous_tag["time_ns"], previous_tag["microstep"]),
                  "TAG_ORDER", "Inputs must occupy strictly increasing logical tags")
        check(decision["event_id"] == event["id"], "DECISION_ID", "Decision belongs to another input")

        prior_anchor = self.anchor
        eligible_sample = False
        kind = event["kind"]
        duplicate = False
        if kind in ("load", "unload"):
            duplicate = event["id"] in self.command_ids
            if not duplicate:
                self.command_ids.add(event["id"])
                if kind == "load" and self.assignment is None and event["session"] not in self.loaded_sessions:
                    self.assignment = {"session": event["session"], "program": event["program"]}
                    self.assignment_since = event["receive_ms"]
                    self.loaded_sessions.add(event["session"])
                    self.accounted[event["session"]] = 0
                    self.anchor = None
                elif kind == "unload" and self.assignment and event["session"] == self.assignment["session"]:
                    self.assignment = None
                    self.anchor = None
        elif kind in ("gap", "restart"):
            self.anchor = None
            if kind == "restart" and event["instance"] not in self.retired_epochs:
                self._set_epoch(event["instance"])
        else:
            identity = (event["instance"], event["sequence"])
            duplicate = identity in self.observation_ids
            if not duplicate and event["instance"] not in self.retired_epochs:
                self.observation_ids.add(identity)
                if event["instance"] != self.epoch:
                    self._set_epoch(event["instance"])
                    prior_anchor = None
                if event["sequence"] > self.high_sequence:
                    eligible_sample = True
                    self.high_sequence = event["sequence"]
                    self.anchor = event
                    if event["receive_ms"] - event["source_ms"] > self.scenario["max_gap_ms"]:
                        self.anchor = None

        check(decision["active"] == self.assignment, "SESSION_OWNERSHIP",
              f"Expected assignment {self.assignment!r}; observed {decision['active']!r}")
        credit = decision["credit_ms"]
        check(type(credit) is int and credit >= 0, "ACCOUNTING", "Credit must be a nonnegative integer")
        if duplicate:
            check(credit == 0, "DUPLICATE_IDEMPOTENCE", "Replayed input added execution time")
        if credit:
            check(kind == "sample" and eligible_sample and self.assignment is not None,
                  "ATTRIBUTION_EVIDENCE", "Credit requires a new ordered sample and an active assignment")
            check(prior_anchor is not None, "COVERAGE_CONTINUITY",
                  "Credit crossed a load, unload, gap, or adapter restart boundary")
            if prior_anchor and kind == "sample":
                bound = self.scenario["max_gap_ms"]
                delta = event["receive_ms"] - prior_anchor["receive_ms"]
                check(event["instance"] == prior_anchor["instance"]
                      and event["sequence"] == prior_anchor["sequence"] + 1
                      and event["source_ms"] > prior_anchor["source_ms"]
                      and 0 < delta <= bound
                      and all(0 <= item["receive_ms"] - item["source_ms"] <= bound
                              for item in (prior_anchor, event)),
                      "COVERAGE_CONTINUITY", "Credit requires fresh, contiguous evidence in one epoch")
                check(credit == delta, "ACCOUNTING", "Credit differs from the bounded receipt-time interval")
                check(all(item["available"] and item["execution"] == "RUNNING"
                          for item in (prior_anchor, event)),
                      "EXECUTION_EVIDENCE", "Both interval endpoints must be available and running")
                if self.assignment:
                    check(all(item["source_ms"] >= self.assignment_since for item in (prior_anchor, event)),
                          "SESSION_OWNERSHIP", "Credit includes an observation captured before this load")
                    expected_program = self.assignment["program"].upper().removesuffix(".NC")
                    check(all(item["program"].upper().removesuffix(".NC") == expected_program
                              for item in (prior_anchor, event)),
                          "PROGRAM_MATCH", "Credited observations do not match the loaded program")
            expected_session = self.assignment["session"] if self.assignment else None
            check(decision["credited_session"] == expected_session, "SESSION_OWNERSHIP",
                  "Credit was attributed to the wrong session")
            if expected_session:
                self.accounted[expected_session] += credit
        else:
            check(decision["credited_session"] is None, "ACCOUNTING", "Zero credit must have no recipient")
        check(decision["totals_ms"] == self.accounted, "ACCOUNTING", "Totals do not conserve attributed credit")
        self.records.append(deepcopy({"input": event, "tag": tag, "decision": decision}))

    def _set_epoch(self, instance):
        if self.epoch is not None and self.epoch != instance:
            self.retired_epochs.add(self.epoch)
        self.epoch = instance
        self.high_sequence = -1
        self.anchor = None

    def finish(self, destination):
        last = self.records[-1] if self.records else {"input": {"id": "end"}, "tag": {}}
        # End-of-run violations refer to the final decision, not a nonexistent next record.
        def final_check(condition, code, message):
            if not condition:
                self.violations.append({"contract": code, "message": message,
                                        "event_id": last["input"]["id"], "tag": last["tag"],
                                        "record_index": max(0, len(self.records) - 1)})
        final_check(len(self.records) == len(self.scenario["events"]), "COMPLETE_REPLAY",
                    "Not every scenario input produced a monitored decision")
        actual = self.records[-1]["decision"]["totals_ms"] if self.records else {}
        final_check(actual == self.scenario["expected_totals_ms"], "EXPECTED_TOTALS",
                    f"Expected {self.scenario['expected_totals_ms']!r}; observed {actual!r}")
        active = self.records[-1]["decision"]["active"] if self.records else None
        final_check((active["session"] if active else None) == self.scenario["expected_active_session"],
                    "EXPECTED_ASSIGNMENT", "Final assignment differs from the scenario oracle")
        first = self.violations[0] if self.violations else None
        hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in [ROOT / "src/Main.lf", *sorted((ROOT / "replay").glob("*.py"))]}
        result = {"schema_version": 1, "scenario": self.scenario["name"],
                  "mutant": os.environ.get("LF_REPLAY_MUTANT", "none"),
                  "status": "fail" if self.violations else "pass",
                  "totals_ms": actual, "active": active, "violations": self.violations,
                  "counterexample": None if first is None else {
                      "violation": first,
                      "input_prefix": [row["input"] for row in self.records[:first["record_index"] + 1]]},
                  "provenance": {"sources_sha256": hashes, "scenario_sha256": hashlib.sha256(
                      json.dumps(self.scenario, sort_keys=True).encode()).hexdigest()},
                  "records": self.records}
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(f"REPLAY {result['status'].upper()}: {self.scenario['name']} -> {destination}", flush=True)
        if first:
            print(f"  {first['contract']} at {first['event_id']}: {first['message']}", flush=True)
        return result
