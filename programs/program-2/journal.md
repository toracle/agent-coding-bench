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
