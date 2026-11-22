# Program 2 · Inventory Reservations

Each submission delivers a REST server, an HTTP CLI client, database migrations,
and reproducible launch instructions. The server persists its state in a
file-backed SQLite 3 database for the reference benchmark. Configure the
database adapter at startup so that PostgreSQL or MySQL can be used for a
separately verified deployment. All adapters expose the same HTTP contract.

## REST and CLI

The server returns JSON. The CLI sends HTTP requests using a configurable
`--base-url`, prints response JSON to stdout, and exits 0 for 2xx, 1 for a
non-2xx response, or 2 for local argument or transport errors. Local diagnostics
go to stderr. The CLI needs no database connection or database credentials.

| Method and path | CLI command | Success |
| --- | --- | --- |
| GET /healthz | `health` | 200 with `{"status":"ready"}` after the database and schema are ready |
| GET /v1/stock/{sku} | `stock A` | 200 with `{"sku":"A","available":10}` |
| POST /v1/reservations | `reserve --item A=3 --item B=2 --ttl 30 --request-id req-1` | 201 with the new reservation |
| GET /v1/reservations/{id} | `reservation ID` | 200 with the current reservation |
| POST /v1/reservations/{id}/confirm | `confirm ID --request-id req-2` | 200 with the updated reservation |
| POST /v1/reservations/{id}/cancel | `cancel ID --request-id req-3` | 200 with the updated reservation |
| POST /v1/clock/advance | `advance 30 --request-id req-4` | 200 with `{"now":30,"expired":1}` |

A reserve body is `{"items":[{"sku":"A","quantity":3},{"sku":"B","quantity":2}],"ttl":30}`.
Confirm and cancel have an empty body or an empty JSON object. Advance takes
`{"seconds":30}`. Requests with bodies use `Content-Type: application/json`.
The CLI maps `--request-id` to the `Idempotency-Key` header, required on
every POST. The advance route is a benchmark clock control. Enable it only
in benchmark mode.

A reservation representation contains `id` (an opaque string), `status`,
`items` sorted by SKU, and `expires_at` (a logical timestamp in seconds).
SKUs contain only uppercase ASCII letters, digits, underscore or hyphen, from
1 to 32 characters. Idempotency keys are case-sensitive ASCII letters, digits,
underscore or hyphen, from 1 to 128 characters. A reserve must contain 1–100
distinct SKUs. Quantity, TTL and clock increments must be JSON integers from
1 to 1,000,000. The logical clock starts at zero. The server rejects advances
that would move it beyond 2,000,000,000, and rejects a reservation that would
expire beyond that limit.

Errors have shape `{"error":{"code":"..."}}`. Use 400 with `invalid_request`
for invalid JSON, input fields, or idempotency keys. Use 404 with
`unknown_sku` or `unknown_reservation` for missing records. Use 409 with
`insufficient_stock`, `invalid_transition`, or `idempotency_conflict` for
business conflicts. If multiple reservation items fail, check for missing
SKUs before checking for insufficient stock. HTTP status and error code form
the assertion contract.

## State transitions and transactions

- Reserving checks every SKU and deducts the quantities in one transaction.
  A failed reservation deducts nothing.
- Pending reservations can become confirmed, cancelled or expired. Confirming
  keeps the deduction. Cancelling or expiring returns quantities once.
  Terminal reservations reject new transition requests with 409.
- Advancing the clock expires all pending reservations with
  `expires_at <= now` and restores their stock in the same transaction.
  An expired reservation remains available for lookup.
- A valid write's first domain response, including a 404 or 409, is persisted
  with its idempotency key. The fingerprint covers the method, path and
  normalized payload. JSON whitespace and object-key order are immaterial,
  and reserve item order is immaterial. An identical retry returns the
  original status and JSON body, including after a restart. Reusing a key
  for different input returns 409 without replacing its record. Malformed
  inputs are rejected before claiming a key.
- Concurrent attempts must prevent overselling and double release. A crash
  between commit and reply must be recoverable by replaying the key. Keep
  domain mutation and its recorded response in the same DB transaction.

## ORM and database boundary

Map inventory, reservations, reservation items, the logical clock, and
idempotency records using an ORM in the implementation language. Use
versioned migrations and constraints for primary and foreign keys, unique
idempotency keys, and nonnegative available stock. Index pending reservations
by status and expiration. Choose case-sensitive storage for keys across
adapters.

The HTTP handlers call application operations through a persistence boundary.
Use the ORM for normal reads and writes. Keep any backend-specific migrations,
locking or retry logic inside the adapter and explain it in the submission.
Enable SQLite foreign-key enforcement on every connection. Write contention
may be handled by serializing writers or retrying whole transactions. Use
transactional tables for MySQL deployments. Changing the connection
configuration may also require selecting a build-time database feature
for statically compiled implementations.

| Language / runtime | Candidate ORM |
| --- | --- |
| Common Lisp / SBCL and ECL | [Mito + CL-DBI](https://github.com/fukamachi/mito) |
| Clojure | [Toucan 2 + JDBC drivers](https://github.com/camsaul/toucan2) |
| Python | [SQLAlchemy](https://docs.sqlalchemy.org/en/21/dialects/) |
| Rust | [Diesel](https://diesel.rs/guides/getting-started/) |
| Smalltalk / Pharo | [Glorp + database drivers](https://github.com/pharo-rdbms/glorp) |

These are candidates. Pin versions and verify each runtime, ORM and driver
combination as part of the implementation. SQLite is mandatory. Run the
same migrations and contract suite against PostgreSQL or MySQL before
reporting compatibility with either backend.

## Acceptance and benchmark workloads

Each run starts from a fresh file-backed SQLite database populated using the
submission's seed command. The contract fixture starts with A=10 and B=5.
Include checks for atomic multi-SKU failure, exact-boundary expiry, confirm
before expiry, cancel and replay, key conflicts, restart persistence,
concurrent writes against the same stock, and CLI parity with the REST API.

Measure ready time separately. Measure mixed HTTP traffic on a persistent
server using a seeded request trace. Add a separate case that advances the
clock over thousands of active reservations, with a sparse and a dense expiry
set. The timed path includes HTTP, ORM and database work. The load driver
uses HTTP directly. CLI smoke tests verify the client. Record runtime and ORM
versions, SQLite journal and durability settings, request concurrency,
throughput, and p50/p95/p99. Verify responses and final stock against the
trace. Publish SQLite, PostgreSQL and MySQL results as separate runs.

For a development journal, add a limit for new reservations while leaving
existing reservations eligible for confirmation. Record how each environment
applies the change, whether it needs a restart, and which tests caught
mistakes. Keep these observations with the implementation.
