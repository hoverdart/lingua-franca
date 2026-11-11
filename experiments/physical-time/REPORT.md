# Measured findings: LF physical-time control experiment

**Run date:** November 11, 2026. **Repository base:**
`a50cc05cc223031ffd2255d96332496c81e8a2c2`. **Platform:** native macOS,
LF compiler reporting 0.13.0, generated C federates over localhost TCP.

## Answer to the research question

The new experiment exercises an actual LF-generated implementation under real
elapsed-time perturbations. Changing maxwait changes the RUN/HOLD decisions and
the runtime's native tardy handling. Increasing maxwait can preserve logical
ordering, but it does **not** restore a physical deadline that has already been
missed. A local timer detects missing observations without waiting for a tardy
message to arrive.

All **60 native runs / 360 cycles** completed and passed the final experiment
checks. In the **342 unmutated cycles**, the independent oracle found zero unsafe
RUNs and zero RUNs logged after the 100 ms deadline. These cycles produced
174 RUNs and 168 HOLDs (75 missing-input, 93 deadline). The separate negative
control deliberately produced **18 unsafe RUNs**, and the oracle detected all 18.
There were also **28 passing unit tests** (14 new oracle corruption tests and 14
existing repository tests), plus 39 original replay executions with identical
semantic reports across worker counts 1/4/1 and all 4 original mutants detected.

These results support the tested synthetic implementation and conditions only.
They are not a proof of safety, hard-real-time scheduling, or industrial efficacy.

## Main controlled matrix

Three repetitions and six cycles per run yield 18 cycles per table row.
The actuator reaction-start deadline is 100 ms; decision service delay is 0.
“Producer delay” is requested physical sleep before producing the observation,
not an LF logical delay and not isolated wire latency.

| Maxwait | Producer delay | RUN | Missing HOLD | Deadline HOLD | Tardy | Median output lag |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
|20 ms|5 ms|18|0|0|0|10.94 ms|
|20 ms|50 ms|0|18|0|18|24.04 ms|
|20 ms|150 ms|0|18|0|18|23.67 ms|
|80 ms|5 ms|18|0|0|0|11.21 ms|
|80 ms|50 ms|18|0|0|0|57.33 ms|
|80 ms|150 ms|0|18|0|18|84.45 ms|
|200 ms|5 ms|18|0|0|0|11.53 ms|
|200 ms|50 ms|18|0|0|0|57.29 ms|
|200 ms|150 ms|0|0|18|0|158.50 ms|
|forever|5 ms|18|0|0|0|10.32 ms|
|forever|50 ms|18|0|0|0|56.99 ms|
|forever|150 ms|0|0|18|0|159.02 ms|

Output lag is measured physical logging time minus the fixed logical cycle tag.
It includes producer scheduling, service delay, loopback transport, workflow
scheduling, and logging. Maxwait is a waiting budget, not an observed hard bound:
the 20 ms conditions issued missing-input HOLDs a few milliseconds beyond 20 ms.

The strongest paired comparison is the 50 ms producer condition. Raising maxwait
from 20 to 80 ms changes all 18 cycles from missing-input HOLD plus tardy handler
to timely RUN with normal reception. At 150 ms, raising maxwait from 80 to 200 ms
eliminates all 18 tardy handlers, but all 18 cycles still HOLD because the 100 ms
actuator deadline is missed. Unbounded waiting produces the same categorical
result as 200 ms in that finite-delay condition.

## Additional controls

**Upstream elapsed service time:** With a 5 ms producer delay and 150 ms of physical
sleep in the decision reaction, all 36 cycles across maxwait 20/200 ms invoked the
native downstream deadline handler. Median output lags were 162.96 and 162.25 ms.
There were no tardy messages. This separates reaction-path lateness from
maxwait expiry. It measures elapsed service time, not CPU utilization.

**Omitted observation:** Cycle 3 was omitted in three runs at each of two waits.
With 20 ms maxwait, all three omissions yielded missing-input HOLD at 21.46–25.21 ms
lag. With forever, all three yielded deadline HOLD at 408.28–411.65 ms: the next
higher-tag observation allowed the runtime to establish the prior absence.
The other 15 cycles per wait produced RUN. This demonstrates the availability
cost of unbounded waiting in this setup. It is not a process crash or network
partition experiment, and a missing message itself cannot fire a tardy handler.

**Freshness-bypass negative control:** With 20 ms maxwait and 50 ms producer delay,
bypassing the freshness guard produced 18 unsafe RUNs before the corresponding
late observations were processed. All 18 were independently flagged as
`UNSAFE_RUN`. The native tardy handler alone does not undo an unsafe decision
already made by defective application code.

**Four-worker control:** The three selected conditions reproduced their
one-worker categorical results in all 54 cycles: normal RUN at (20,5), missing
HOLD+tardy at (20,50), and deadline HOLD at (200,150), where pairs are
(maxwait, producer delay) in milliseconds. Physical timings varied. This is not
a throughput comparison or a general determinism proof.

**Injection check:** All requested physical sleeps actually elapsed. Across
all runs, measured producer-body elapsed times were 5.115–6.990 ms for 5 ms
requests, 50.250–55.218 ms for 50 ms requests, and 150.389–156.670 ms for 150 ms
requests. Logical tags remained unchanged across the injected elapsed time.
The 60 measured launches took 315.18 seconds in total, excluding compilation and
subsequent validation/regression checks.

## How this addresses the feedback

- The original experiment replayed synthetic timestamps in a single process.
  This experiment compiles and runs two actual communicating LF federates,
  with logical-time ordering governing executable decisions.
- It measures physical-time effects and records real LF-generated deadline and
  tardy-handler execution, rather than manufacturing “violations” from a
  Python comparison of fixture timestamps.
- The two injection hooks provide a small reproducible ground-truth experiment
  for future physical-time annotations. They do not implement the proposed
  compiler annotation or simulation-mode feature themselves.
- The fail-closed policy combines local timer, freshness guard, tardy rejection,
  and downstream deadline handling. It does not claim maxwait alone creates
  application safety.

The paper's decentralized-coordination discussion (§3), deadline discussion
(§4.5), and missing-input fault detection (§4.6) guided the design. See the
[protocol and sources](README.md) for exact source descriptions and version
caveats.

## Audit trail and reproducibility

`results/measured-2026-11-11/plan.json` fixes all 60 randomized cases (seed 20261111).
Each `run-NNN/` contains raw adapter/workflow/RTI/launcher logs and parsed records.
`build.json` records compiler version, source hashes, and generated executable
hashes. `summary.csv` contains the full 20-condition table; `summary.json` contains
the machine-readable aggregate. `validation.json` confirms 60/60 completed runs,
all design-predicted regimes, exact raw-log/parsed-record agreement, and zero
unexpected oracle violations.

An independent code review found that the first oracle checked unexpected
omissions but did not require a requested omission. The final oracle fixes that
gap and a corruption test detects it. The execution harness was preserved
verbatim as `harness-at-execution.py`, with its original hash. Final validation
reparsed all original raw logs using the corrected oracle and recorded the new
validator hashes. No measurements were changed, omitted, or selected after the
fact. The regime checks were encoded during collection, so these results are
not claimed to be preregistered.

The original replay source, scenarios, executable, and existing artifact reports
were preserved. Its regression executions wrote to a separate
`results/replay-regression/` directory and used its existing source-matching
build receipt; that older executable was not rebuilt. The initial measurements were completed locally before publication. No Foundation production data was needed or used.

## Boundaries and next experiment

The tested “actuator” only writes RUN/HOLD to a log; no hardware is controlled.
The model checks per-cycle freshness, not the original session ownership or
accounting contracts. All processes run on one host, with no independent network
impairment, cross-host clock error, silent process outage, durable restart, or
real-machine feedback. Ordinary OS scheduling can exceed any finite observation
window. LF's lazy deadline handler is not a watchdog that fires exactly when a
budget expires.

The most useful next experiment is a two-host run with connection-level delay,
measured clock error, and a truly silent producer. That would test whether the
same fail-closed choices survive physical network and host failure modes, and
provide stronger evidence for any elapsed-time annotation/simulation proposal.
