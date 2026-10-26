"""System under test. This module never calls the monitor."""

from copy import deepcopy

from replay.scenario import load_scenario, program_key

MUTANTS = ("none", "stale-unload", "duplicate-credit", "wrong-program", "bridge-gap")


class Coordinator:
    def __init__(self, mutant="none", max_gap_ms=None):
        if mutant not in MUTANTS:
            raise ValueError(f"unknown mutant: {mutant}")
        self.mutant = mutant
        self.max_gap = max_gap_ms if max_gap_ms is not None else load_scenario()["max_gap_ms"]
        self.active = None
        self.totals = {}
        self.commands = set()
        self.samples = set()
        self.instance = None
        self.retired = set()
        self.last_sequence = -1
        self.previous = None
        self.last_credit = 0
        self.loaded_at = None

    def apply(self, event):
        kind = event["kind"]
        credit = 0
        credited_session = None
        reason = "anchor"
        if kind in ("load", "unload"):
            if event["id"] in self.commands:
                reason = "duplicate-command"
            else:
                self.commands.add(event["id"])
                if kind == "load":
                    if self.active or event["session"] in self.totals:
                        reason = "load-conflict"
                    else:
                        self.active = {"session": event["session"], "program": event["program"]}
                        self.loaded_at = event["receive_ms"]
                        self.totals[event["session"]] = 0
                        self.previous = None
                        reason = "loaded"
                elif self.active and (self.active["session"] == event["session"]
                                      or self.mutant == "stale-unload"):
                    self.active = None
                    self.previous = None
                    reason = "unloaded"
                else:
                    reason = "stale-unload"
        elif kind in ("gap", "restart"):
            if self.mutant != "bridge-gap":
                self.previous = None
            if kind == "restart" and event["instance"] in self.retired:
                reason = "retired-instance"
            else:
                if kind == "restart":
                    self._epoch(event["instance"])
                reason = kind
        else:
            key = (event["instance"], event["sequence"])
            if key in self.samples:
                reason = "duplicate-sample"
                if self.mutant == "duplicate-credit" and self.active:
                    credit = self.last_credit
            elif event["instance"] in self.retired:
                reason = "retired-instance"
            else:
                self.samples.add(key)
                if event["instance"] != self.instance:
                    self._epoch(event["instance"])
                if event["sequence"] <= self.last_sequence:
                    reason = "out-of-order"
                else:
                    prior = self.previous
                    if prior and self.active:
                        delta = event["receive_ms"] - prior["receive_ms"]
                        chronological = event["source_ms"] > prior["source_ms"]
                        fresh = all(item["receive_ms"] - item["source_ms"] <= self.max_gap
                                    for item in (prior, event))
                        in_session = all(item["source_ms"] >= self.loaded_at for item in (prior, event))
                        continuous = (event["sequence"] == prior["sequence"] + 1
                                      and 0 < delta <= self.max_gap and chronological and fresh and in_session)
                        running = all(item["available"] and item["execution"] == "RUNNING"
                                      for item in (prior, event))
                        matching = all(program_key(item["program"]) == program_key(self.active["program"])
                                       for item in (prior, event))
                        if continuous and running and (matching or self.mutant == "wrong-program"):
                            credit = delta
                            reason = "attributed"
                        else:
                            reason = "unattributed"
                    self.last_sequence = event["sequence"]
                    # Old source timestamps cannot become anchors for later credit.
                    self.previous = event if event["receive_ms"] - event["source_ms"] <= self.max_gap else None
                    self.last_credit = credit
        if credit and self.active:
            credited_session = self.active["session"]
            self.totals[credited_session] += credit
        return deepcopy({"event_id": event["id"], "reason": reason, "active": self.active,
                         "credit_ms": credit, "credited_session": credited_session,
                         "totals_ms": self.totals})

    def _epoch(self, instance):
        if self.instance is not None and instance != self.instance:
            self.retired.add(self.instance)
        self.instance = instance
        self.last_sequence = -1
        self.previous = None
