# Physical-time execution, maxwait, and deadline experiment

This is a **new experiment**, independent of the original Python-target logical
replay. It compiles LF to two native C federates and an RTI, runs them as separate
processes over localhost TCP, injects real elapsed service time without changing
logical tags, and measures native LF tardy and deadline handlers.

It responds to the November 10, 2026 feedback that a logical-time simulation is
not a test of the generated implementation under physical-time behavior. The
controller's logical tags and dependency order now govern actual RUN/HOLD
decisions in a running generated program. Sensors and the actuator remain
synthetic: RUN/HOLD is logged, not sent to real equipment.

## Run and verify

From the repository root (existing LF 0.13.0 virtual environment):

```sh
.venv/bin/python experiments/physical-time/run.py \
  --repeats 3 --seed 20261111 \
  --output experiments/physical-time/results/my-new-run
.venv/bin/python experiments/physical-time/validate.py \
  experiments/physical-time/results/my-new-run
.venv/bin/python -m unittest discover \
  -s experiments/physical-time -p 'test_*.py' -v
```

Use a new output directory each time; the runner refuses to overwrite an old
result. Native compilation needs CMake and a C compiler. No network downloads,
Python packages, account credentials, Foundation data, or external services are
required. The generated LF launcher opens local TCP sockets. It does not connect
to Foundation or operate machine controllers. Allow roughly six minutes for
60 launches, plus compilation, on the tested Mac.

`REPORT.md` summarizes the measured run. `results/measured-2026-11-11/` contains
the randomized plan, four compiler logs, build/source/executable hashes, the
original execution harness, raw per-federate and RTI logs, all per-run JSON
traces, `summary.csv`, `summary.json`, and final `validation.json`.

## Controlled design

The same LF program and six input cycles are used in every condition. Timers
fire at logical 200, 600, 1000, 1400, 1800, and 2200 ms. Sequence numbers are
unique and every observation represents authorization for its own cycle only.
The period is 400 ms, large enough to separate the chosen perturbations.

1. **Adapter federate:** physically sleeps before producing an observation.
   Its LF tag does not change. This is a controlled elapsed service delay in
   the producer, not simulated timestamp skew, CPU demand, or isolated network
   latency. `source_start` and `produced` record actual elapsed duration.
2. **Workflow federate:** a local LF timer ensures decisions are evaluated even
   if an observation is missing. An on-tag normal input reaction accepts the
   current observation before the timer reaction. The timer permits RUN only
   when the accepted sequence and logical timestamp match this cycle.
3. **Tardy handler:** actual LF `tardy` code logs the original intended tag and
   the processing tag, then drops the expired observation. It cannot authorize
   a later cycle. It only runs if the late observation eventually arrives.
4. **Deadline handler:** a downstream actuator reaction has a 100 ms deadline
   relative to the cycle tag. Sleeping in the upstream decision reaction makes
   this actuator reaction ready late; LF then runs its deadline handler instead
   of its ordinary body. This is a reaction-**start** constraint, not a body
   completion deadline or an independently scheduled watchdog.

The RTI coordinates startup/shutdown. Data coordination is explicitly
`decentralized`. The source has `@maxwait(0)`; the workflow's maxwait is explicitly
set in each generated profile. No `after` connection delay is added and fast
mode is not used (LF rejects the `fast` property for federated programs even
when set to false). The runtime performs startup clock synchronization. All
processes share one host; clock synchronization across machines is not tested.

### Factors and controls

| Group | Conditions | Repeats | Cycles/run |
| --- | --- | --- | --- |
| Factorial | maxwait 20, 80, 200 ms, forever × producer delay 5, 50, 150 ms | 3 | 6 |
| Upstream service delay | maxwait 20, 200 ms; producer 5 ms; decision service 150 ms | 3 | 6 |
| Missing observation | skip cycle 3; maxwait 20 ms, forever; producer 5 ms | 3 | 6 |
| Negative control | bypass freshness guard; maxwait 20 ms; producer 50 ms | 3 | 6 |
| Worker control | 4 workers: (maxwait, producer) = (20,5), (20,50), (200,150) ms | 3 | 6 |

There are 20 conditions × 3 repeats = 60 runs, 360 cycles. The full run order was
shuffled once using seed 20261111 and saved before launching the matrix. One
exploratory smoke run preceded the matrix. These are not preregistered scientific
results: final validation checks were strengthened during collection following
independent code review. The original execution harness and its hash are
preserved, and the final validator reparses the unchanged raw logs.

A skipped output is an **application-level omission**, not a crashed process or
network partition. With unbounded maxwait, this runtime can learn that a tag was
absent when a subsequent higher-tag message arrives. Thus omission timing must
be measured rather than assumed to be prompt.

### Independent oracle and controls

`run.py:oracle` checks six complete source/start/end/terminal traces, preserved
logical and intended tags, delivery accounting, elapsed injection duration,
no RUN without an actual same-tag accepted input, no late RUN, and no
unnecessary missing-input HOLD. It derives freshness from normal receive records
and timestamps, never from the controller's `fresh` or `allow` flag or internal
state. The exact omission fixture must also be honored.

`validate.py` reparses the separate raw process logs, checks all planned cases,
compares raw records with saved records, verifies source/profile/execution-harness
hashes, reruns the final oracle, and checks the predicted qualitative regimes.
A negative-control run passes the **experiment check only when all six unsafe
RUN decisions are detected**. It is not a safe implementation passing tests.
Corruption tests independently check missing/duplicate terminal outputs, unsafe
and late RUNs, unnecessary HOLDs, false deadlines, unhonored omissions, logical
tag changes, and incomplete injection.

Logs from different processes are not treated as a total causal order. The
freshness-before-decision comparison uses only events within the workflow
federate. `produced` is logged inside the producer reaction before the generated
network sender runs; it is not a wire-send or wire-receive timestamp. Reported
output lag is physical logging time minus logical tag, including scheduler,
service, loopback, and logging effects. Percentiles/maxima are finite samples,
not proven latency bounds.

## Limits and next step

- This is a bounded synthetic implementation experiment on one ordinary macOS
  host, not hard-real-time evidence, an industrial safety case, or multi-machine
  clock/network validation.
- No real CNC, sensor, operator, hardware actuator, process crash, restart,
  persistence, network partition, or pure network impairment was exercised.
- The original session-ownership/accounting contracts remain in the separate
  replay experiment. This experiment checks per-cycle freshness and deadline
  authorization, not the old session-ID contract or industrial workflow parity.
- The `LF_SOURCE_MS` and `LF_COMPUTE_MS` environment hooks are test instrumentation
  for two known reaction bodies, not a compiler-level physical-time annotation
  feature or a new LF simulation mode. The measured traces provide a baseline
  for evaluating such a feature later.
- A finite maxwait bounds intentional waiting for unknown inputs, not total
  physical response time under arbitrary OS stalls. A deadline handler is lazy:
  it cannot detect a missing input until some event lets the reaction run.
- Increasing maxwait can recover logically ordered observations without
  recovering a physical deadline already lost. This experiment measures that
  distinction rather than assuming a larger wait is always better.

Next: put the federates on different hosts, inject connection-level delay while
preserving message tags, measure synchronization error, and add a true silent
producer/process outage. Compare elapsed-time annotations with these native
measurements before claiming a faithful simulation of deadline/STP behavior.

## Sources

- Edward A. Lee, email feedback, November 10, 2026 (private correspondence;
  not copied into this repository).
- Francesco Paladino, Shulu Li, Edward A. Lee, *Maxwait: A Generalized Mechanism
  for Distributed Time-Sensitive Systems*, emailed RTNS Toulouse November 2026
  preprint, 25 pages: §3 pp. 6–7, §4.5 pp. 15–16, §4.6 p. 17. The public
  [arXiv record](https://arxiv.org/abs/2601.21146) may be a different revision.
- [LF distributed execution documentation](https://www.lf-lang.org/docs/writing-reactors/distributed-execution/).
- [LF deadline semantics](https://www.lf-lang.org/docs/writing-reactors/deadlines/).

The email attachment is not redistributed. No Foundation production data was
read or copied. The initial measurements were completed locally before publication.
