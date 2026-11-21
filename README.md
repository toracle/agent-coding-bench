# Agent Program Bench

Compare the runtime performance of programs implemented with AI coding agents.
A submission is identified by **program, model, harness,
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

Pharo Smalltalk is an opt-in variant. Install the Pharo 13 image and
headless launcher using [Pharo's setup script](https://get.pharo.org/64/130+vm)
in a separate directory. Then run from this checkout:

```sh
PHARO_VM=/path/to/pharo PHARO_IMAGE=/path/to/Pharo.image \
  python3 bench.py --variant pharo
```

The worker runs from source inside the Pharo image.

The runner builds each selected variant once and rotates execution order. Each round
starts a new worker, measures time to its `ready` response, and then warms up
and measures requests on that same worker. Every response is checked. Reported
p50, p95 and p99 are nearest-rank percentiles. Build time is recorded separately
from startup and request latency, alongside executable size and nonblank source lines. Executable size excludes
shared libraries, such as `libecl`. Requests are sequential and timings
include local pipe I/O and the benchmark client's work.

### Initial results

Five rounds per variant on Linux x86-64 under WSL2, with 50,000 measured
requests per variant. Pharo used the same settings in a separate run.
See the [Pharo report](reports/program-1-gpt-6.2-sol--codex-pharo.json) and the [machine-readable report](reports/program-1-gpt-6.2-sol--codex.json).

| Language | Runtime | Source lines | Compile / build (ms) | Executable size | Ready p50 (ms) | Request p50 (µs) | Request p95 (µs) | Request p99 (µs) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| common-lisp | sbcl | 18 | 1,073.44 | 34.66 MiB | 5.42 | 71.1 | 138.2 | 499.0 |
| common-lisp | ecl | 18 | 994.12 | 55.48 KiB | 75.15 | 81.7 | 159.7 | 484.0 |
| python | python | 17 | — | — | 12.92 | 78.0 | 141.3 | 343.0 |
| rust | rust | 41 | 1,061.23 | 3.86 MiB | 1.39 | 45.0 | 113.1 | 189.4 |
| clojure | clojure | 19 | — | — | 1550.31 | 86.9 | 172.0 | 809.5 |
| smalltalk | pharo | 13 | — | — | 216.75 | 70.8 | 136.5 | 355.2 |

Compile / build is one elapsed build per compiled variant, including compiler
startup and linking or image saving where applicable. A dash means the manifest
runs the source directly. Any work those runtimes do before sending `ready`
is included in the readiness time. Executable sizes are for the generated files.
SBCL embeds its runtime and core in the executable. ECL loads `libecl`
separately. Python, Clojure, and Pharo produce no executable in this benchmark.

### What happened during code generation

The implementations evolved during an exploratory Codex session. These notes
describe the observed path. Per-language generation times and retry counts
were not recorded.

| Language | Implementation path and observed detours |
| --- | --- |
| Common Lisp | Wrote one shared quote function and verified live discount redefinition on both SBCL and ECL. Packaging split into an SBCL saved executable image and an ECL native link. The first integrated runner attempt could not find SBCL because Roswell uses `x86-64` while the host reports `x86_64`. Normalizing that directory name let both builds run. |
| Clojure | Used an atom for orders and redefined the discount function in the REPL. Extended the same implementation with a buffered stdin loop for the persistent worker. The REPL check and the recorded worker run both passed. |
| Python | Used a dictionary and a function resolved by global name, so the REPL could replace the policy in place. When the REPL code and standalone worker were combined, the worker's `sys` import was restored before the integrated test. |
| Rust | Passed the discount policy as a function argument to reuse the same order map. The worker reused one input buffer and flushed each response. Both the standalone example and the optimized worker passed their observed checks. |
| Smalltalk | Switched from GNU Smalltalk to Pharo and used binary standard streams for the pipe protocol. The first launch tried to create a preferences directory. Passing `--no-default-preferences` allowed the version probe and worker to run in the benchmark environment. |

See the [live development journal](development/live-image/README.md) for a
verified policy change in running SBCL, ECL, and Pharo sessions, with notes
on the earlier Python and Rust workflows.

### Add a combination

Add a workload under `programs/<program>/` with a specification and
`workload.json` containing a readiness line and request/response cases.
Add an agent directory under that program's `implementations/` with model
and harness metadata and a `variants.json` listing its language implementations.
The current runner supports line-based workers.
