# Program 2 · Implementation journal

Implementations were developed with GPT-6.2-sol and Codex. This journal
records observed steps and corrections. Per-language generation times were
not captured. Results measure the submitted programs.

## Python

Implemented the transaction and HTTP contract first to give the shared
runner a reference. A SQLAlchemy session transaction records the mutation and
its replay response together. A write to the clock row acquires the DB lock
before reads. For expiry, SQLAlchemy eager-loads the due reservation items
and aggregates stock restoration per SKU. The initial HTTP contract run
passed, including fresh-process replay and concurrent attempts against one
SKU. Installing the web and ORM dependencies and opening the listening
socket needed approved execution outside the default sandbox.

## Rust

Implemented the same contract using Diesel's typed query DSL and SQLite
immediate transactions. Cargo's first resolution selected ICU dependencies
that required Rust 1.88 through the HTTP client. Declaring Rust 1.86 and
regenerating the lockfile with Cargo's compatible-version resolver produced
a working build. The first compiled debug build passed the HTTP contract.
The release profile compiled independently. The initial timed release
attempt also exposed that this host lacks `/usr/bin/time`, so the clean
release build was timed with a monotonic Python clock.

## Clojure

Used Toucan models for the tables and dynamically scoped the JDBC connection
for each request. Kept JSON and HTTP in a thin layer around the store, using
the JDK server and client. Migration, CLI verification and restart checks
each start a JVM and load the dependencies. The bench invokes those commands
outside steady-state request timing. Toucan 2 and SQLite JDBC loaded from
Clojars and Maven Central with a temporary Maven cache. The first integrated
contract run passed. When the contract later grew to cover a CLI write and
HTTP replay, the old driver connection went idle while the CLI JVM started.
Closing that connection before invoking a separate client fixed the runner.
The first mixed workload showed a median of 51.9 ms with the JDK HTTP
server's default socket settings under build load. The submitted variant
enables TCP_NODELAY for tiny request/response pairs. In the later run
the median was 8.4 ms, and the expanded CLI contract passed.
The runs took place at different times under different background load.

## Common Lisp · SBCL

Used one application source for the Lisp variants. The cached Quicklisp
directories contained Hunchentoot, and Mito and the other dependencies had
to be fetched. A copied Roswell Quicklisp setup loaded a Roswell-specific
hook that failed when started directly under SBCL. Removing that hook from
the temporary copy allowed dependency loading. The first REST contract
reached CLI verification, where Dexador rejected the assumed
`:ignore-status` option. Handling its documented HTTP condition preserved
the response body and restored the CLI status convention. Both compiled
application profiles then passed the SQLite workload.

## Common Lisp · ECL

The same Common Lisp source and compiler policy declarations are used.
The initial ECL dependency build timed out while compiling native
dependencies. Reusing the partial cache advanced through CFFI and the
SQLite driver. A separate startup probe found that ECL needs `--` before
application arguments for UIOP to return them in script mode. This difference
lives in the launcher manifest. Lowering the C compiler optimization during
dependency setup helped advance the native build. Loading dependencies from
source hit ECL's foreign-code limitation on the `clines` form, so the native
dependency path is the reproducible option.

Once dependencies loaded, the first migration timed out in the SQLite
driver. A direct SQLite call worked, and a trace of CL-DBI's fetch showed
two statement steps where one was expected. A connection subclass isolated
the single-step fix to Program 2 on ECL. Migration then passed and seed
exposed a second assumption. CL-DBI inferred the dialect from the class's
package, so the subclass now explicitly identifies itself as SQLite.
The first contract then reached CLI verification, where ASDF emitted a
loader message on stdout before the JSON. Routing loader diagnostics to
stderr preserved the CLI output contract. Both ECL profiles use the same
driver workaround and dependency cache. Each passed the shared REST and
CLI contract. The repeated HTTP calls used to
prepare the expiry fixtures took considerably longer than the clock advance
being timed. The runner now supports parallel fixture preparation while
keeping a single client for the measured clock advance. The first dense
expiry reached 2,000 reservations, then its clock advance exceeded the
harness's 30-second response limit. The expiry workload now waits up to
180 seconds for that one request. Correctness still requires the exact
expiry count and restored stock. I also added an optional fixture snapshot
for ECL to avoid replaying thousands of HTTP setup requests. The runner
creates that fixture before launching the server; the timed advance still
uses the REST server and its ORM. The verified retry returned 1,800 expirations
in 67.9 s in debug and 64.9 s in the optimized build. Mixed throughput stayed
near 8 requests/s in both builds. This workload exposed costs outside the
application compiler declarations.

## Pharo

Copied the image, loaded Glorp and the SQLite driver into the copy and used
Zinc for HTTP. The first migration failed because Glorp derives an uppercase
table-initializer selector. Inspecting `DescriptorSystem >> #initializeTable:`
in the installed image exposed that convention. Generating the selector
and descriptor table name consistently fixed migration and seed. The first HTTP request found a
Smalltalk keyword-message grouping error in validation. Explicitly nesting
the Boolean expressions fixed it, and the first complete contract passed.
The dependencies live in the prepared image, while the application stays
in a text source file loaded at startup. The earlier [live-image journal](../../development/live-image/README.md)
shows a separate hot-reload exercise; this run focused on the REST and ORM
implementation.

## Portability

SQLite is the verified reference backend. Python delegates connection
selection to SQLAlchemy, while Rust, Clojure, Common Lisp and Pharo keep
their SQLite-specific connection setup and locking inside the store.
SQLAlchemy rendered the table and index DDL for all three dialects. The
PostgreSQL and MySQL transaction and HTTP contract checks still require
running instances.

## Follow-up · Bulk expiry

The original Common Lisp path selected due reservations, then reloaded each
reservation and its items, loaded and saved the affected stock, and saved
the reservation. It also prepared an individual response that the clock
endpoint discarded. Rust's original path also iterated over reservations.
It updated stock with SQL arithmetic in one statement per item.

The revised Common Lisp clock handler groups due quantities by SKU in one
query, restores each affected SKU once, and updates all due reservation
statuses in one statement. The response count comes from the affected rows.
Stock restoration, status changes, the clock and the idempotency response
remain in the same write transaction.

I compiled both versions to SBCL application FASLs
using the same optimized compiler policy, alternated run order, and used a
fresh SQLite snapshot for each request. Every run started with 2,000 pending
reservations, expired exactly 1,800, and restored 1,800 units of stock.

| Path | Individual HTTP timings (ms) | Median (ms) |
| --- | --- | ---: |
| Per reservation | 1349.974, 1275.032, 1401.594 | 1349.974 |
| Bulk | 29.677, 52.662, 27.811 | 29.677 |

The measured median fell by about 45 times. The change targets clock advance.
The comparison uses SBCL for both paths and does not measure a bulk Rust path.

## Follow-up · Bulk expiry across stacks

Python/SQLAlchemy and Rust/Diesel build the grouped aggregate and conditional
update through their query builders. Clojure/Toucan and Pharo/Glorp use
parameterized SQL through the existing transaction's database connection.
All four restore stock once per affected SKU and update the due reservation
statuses in one statement. The clock and replay record are committed with
the stock and status changes.

Rust's first compile failed because Diesel's typed join requires declaring
that the two tables may appear in the same query. The second compile
succeeded and Cargo reported 8.56 s for the incremental release build.
Pharo's first HTTP contract reached the expiry operation and failed because
the SQLite driver returned a row object with `atIndex:` access. The second
contract passed after using that accessor. Python and Clojure passed their first contracts.
The timed replay uses already edited sources and warm dependency caches.

### Timing an implementation trial

Start the clock before editing. Run the tool again for every compile, image
or module load, and contract attempt. The summary stops at the first
successful contract. Its elapsed time includes work between commands.
Compile and load totals include failed attempts, and their failure counts
show how many retries were needed.

```sh
python3 development/record_work.py /tmp/trial.jsonl rust start
python3 development/record_work.py /tmp/trial.jsonl rust compile -- \
  cargo build --locked --release --manifest-path programs/program-2/implementations/gpt-6.2-sol--codex/rust/Cargo.toml
python3 development/record_work.py /tmp/trial.jsonl rust contract -- \
  python3 programs/program-2/run.py --variant rust --contract-only
python3 development/record_work.py /tmp/trial.jsonl rust summary
```

The beginning of this edit preceded the timing request. Exact time from
the first edit through acceptance, and the duration of earlier failed
attempts, were not recorded. The prepared-source replay measures the
build or load and contract path separately from runtime benchmarking.

### Measured build or load through first passing contract

Each timed replay used the already edited source, a warm dependency cache,
one build or load check and one contract run. Rust used release, and both
Lisp implementations used the optimized policy. A load check covers Python
bytecode compilation, Clojure namespace loading, or Pharo file-in.
Contract time includes fresh server and CLI processes and a server restart.

| Runtime | Build or load (s) | Attempts / failures | Contract (s) | Elapsed to pass (s) |
| --- | ---: | ---: | ---: | ---: |
| python | 0.19 | 1 / 0 | 5.51 | 6.22 |
| rust | 6.32 | 1 / 0 | 0.27 | 6.71 |
| sbcl | 4.06 | 1 / 0 | 17.63 | 21.80 |
| ecl | 10.33 | 1 / 0 | 52.73 | 63.18 |
| clojure | 15.28 | 1 / 0 | 144.86 | 160.27 |
| pharo | 0.44 | 1 / 0 | 3.55 | 4.10 |

The individual events are available in the [timing log](../../development/program-2-bulk-work.jsonl).

### Bulk expiry across runtimes

Each row is one HTTP request on the same 2,000-reservation SQLite
fixture. Every run verified 1,800 expirations and 1,800 units of stock
restored. Fixture preparation is outside the measurement.

| Runtime | Elapsed (ms) |
| --- | ---: |
| python | 20.76 |
| rust | 5.01 |
| sbcl | 32.19 |
| ecl | 167.88 |
| clojure | 97.73 |
| pharo | 19.89 |

[Raw runtime measurements](../../reports/program-2-bulk-expiry.json).
