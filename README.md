# Agent Program Bench

Compare both the performance of a program and the work an AI coding agent
takes to deliver it. A submission is identified by **program, model, harness,
language, and runtime variant**. The same Common Lisp implementation, for
example, can be measured with either ECL or SBCL.

## Program 1 · Order quotes

A persistent worker computes quotes from orders held in memory. See the
[specification](programs/program-1/spec.md) and
[implementation matrix](programs/program-1/implementations/gpt-6.2-sol--codex/variants.json).
The initial implementations were developed with GPT-6.2-sol and Codex.

### Measure performance

On Linux, install Python 3, Rust, SBCL, ECL with a C compiler, and Clojure CLI
with a JVM for the variants you intend to benchmark. Use `SBCL` and `ECL` to
override their executable paths. The runner also checks the Roswell paths
for the versions used in the initial experiment.

```sh
python3 bench.py --output result.json
python3 bench.py --variant ecl --variant rust --requests 1000 --rounds 2
```

The runner builds each variant once and rotates execution order. Each round
starts a new worker, measures time to its `ready` response, and then warms up
and measures requests on that same worker. Every response is checked. Reported
p50, p95 and p99 are nearest-rank percentiles. The report also includes build
time, executable size, and nonblank source lines. Executable size excludes
shared libraries, such as `libecl`. Requests are sequential and timings
include local pipe I/O and the benchmark client's work.

### Initial results

Five rounds per variant on Linux x86-64 under WSL2, with 50,000 measured
requests per variant. See the [machine-readable report](reports/program-1-gpt-6.2-sol--codex.json).

| Language | Runtime | Source lines | Ready p50 (ms) | Request p50 (µs) | Request p95 (µs) | Request p99 (µs) |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| common-lisp | sbcl | 18 | 5.42 | 71.1 | 138.2 | 499.0 |
| common-lisp | ecl | 18 | 75.15 | 81.7 | 159.7 | 484.0 |
| python | python | 17 | 12.92 | 78.0 | 141.3 | 343.0 |
| rust | rust | 41 | 1.39 | 45.0 | 113.1 | 189.4 |
| clojure | clojure | 19 | 1550.31 | 86.9 | 172.0 | 809.5 |

### Measure agent productivity

Start one new agent session per language in a separate checkout with the same
starting snapshot. Give each session the same task specification, acceptance
cases, and budget. For a from-scratch trial, remove that language's reference
source from the starting snapshot before launching the agent. Save the task
prompt and starting revision alongside trial results for reproducibility.

```sh
python3 record_trial.py --variant python --output trial.json -- \
  codex exec -m gpt-6.2-sol 'Implement the Python worker in its manifest source path using programs/program-1/spec.md. Verify the worker.'
python3 bench.py --variant python --trial trial.json --output result.json
```

The recorder captures agent wall time and exit status, then separately times
a clean build and protocol acceptance check. A report can include multiple
trial files. Token counts and human intervention counts are nullable fields
for data supplied by the harness or trial operator. An agent process exiting
successfully and its implementation passing acceptance are separate outcomes.
The initial implementations are retrospective and have no recorded
implementation wall time, token counts, or intervention counts.

### Add a combination

Add a workload under `programs/<program>/` with a specification and
`workload.json` containing a readiness line and request/response cases.
Add an agent directory under that program's `implementations/` with model
and harness metadata and a `variants.json` listing its language implementations.
The current runner supports line-based workers.
