"""Validate replay ingress before starting LF. Milliseconds use one synthetic origin."""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_TIME_MS = 86_400_000


def integer(value, name):
    if type(value) is not int or not 0 <= value <= MAX_TIME_MS:
        raise ValueError(f"{name} must be an integer in [0, {MAX_TIME_MS}]")


def nonempty(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def load_scenario(path=None):
    path = Path(path or os.environ.get("LF_REPLAY_SCENARIO", ROOT / "scenarios/normal.json"))
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("scenario must be an object")
    nonempty(data.get("name"), "name")
    integer(data.get("max_gap_ms"), "max_gap_ms")
    if data["max_gap_ms"] == 0:
        raise ValueError("max_gap_ms must be positive")
    events = data.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError("events must be a nonempty list")
    last = -1
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ValueError(f"event {index} must be an object")
        for key in ("id", "kind"):
            nonempty(event.get(key), key)
        for key in ("receive_ms", "source_ms"):
            integer(event.get(key), key)
        if event["receive_ms"] < last:
            raise ValueError("events must be in delivery order; source order may differ")
        last = event["receive_ms"]
        if event["source_ms"] > last:
            raise ValueError("source_ms cannot exceed receive_ms in this clock model")
        kind = event["kind"]
        if kind in ("load", "unload"):
            nonempty(event.get("session"), "session")
            if kind == "load":
                nonempty(event.get("program"), "program")
        elif kind == "sample":
            nonempty(event.get("instance"), "instance")
            integer(event.get("sequence"), "sequence")
            nonempty(event.get("program"), "program")
            if type(event.get("available")) is not bool:
                raise ValueError("available must be boolean")
            if event.get("execution") not in ("RUNNING", "STOPPED", "UNKNOWN"):
                raise ValueError("execution must be RUNNING, STOPPED, or UNKNOWN")
        elif kind == "restart":
            nonempty(event.get("instance"), "instance")
        elif kind != "gap":
            raise ValueError(f"unsupported kind: {kind}")
    totals = data.get("expected_totals_ms")
    if not isinstance(totals, dict):
        raise ValueError("expected_totals_ms must be an object")
    for session, total in totals.items():
        nonempty(session, "expected session")
        integer(total, "expected total")
    if "expected_active_session" not in data:
        raise ValueError("expected_active_session is required (string or null)")
    if data["expected_active_session"] is not None:
        nonempty(data["expected_active_session"], "expected_active_session")
    return data


def program_key(program):
    return program.upper().removesuffix(".NC")
