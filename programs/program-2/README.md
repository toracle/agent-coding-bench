# Run Program 2

Start with the [HTTP and persistence contract](spec.md), then choose an
implementation from [the variant manifest](variants.json). Commands below
start at the repository root. The runner opens a local server, migrates and
seeds disposable SQLite files, checks the server and CLI over HTTP, and times
separate mixed and expiry workloads. Add `--contract-only` for the correctness
checks or `--output /path/to/result.json` to save measurements.

## Python · Flask, Waitress and SQLAlchemy

```sh
impl=programs/program-2/implementations/gpt-6.2-sol--codex/python
python3 -m venv /tmp/program2-python
/tmp/program2-python/bin/pip install -r "$impl/requirements.txt"
/tmp/program2-python/bin/python programs/program-2/run.py --variant python
```

The server accepts a SQLAlchemy `DATABASE_URL` or `--database-url`. A deployment
using a different database also needs its SQLAlchemy driver and migration and
contract checks on that database. SQLite is the measured backend.

## Rust · tiny_http and Diesel

```sh
impl=programs/program-2/implementations/gpt-6.2-sol--codex/rust
cargo build --locked --manifest-path "$impl/Cargo.toml"
PROGRAM2_RUST_BIN="$PWD/$impl/target/debug/inventory-reservations" \
  python3 programs/program-2/run.py --variant rust
cargo build --locked --release --manifest-path "$impl/Cargo.toml"
python3 programs/program-2/run.py --variant rust
```

The lockfile targets Rust 1.86. The build links SQLite and needs the SQLite
development library. The same binary runs the server or the HTTP client.
Select `client` as its first argument to use the CLI.

## Clojure · JDK HTTP server and Toucan 2

```sh
python3 programs/program-2/run.py --variant clojure
```

The runner launches the Clojure CLI in the variant directory with its pinned
`deps.edn`. To run with a pre-resolved classpath, supply its value in
`PROGRAM2_CLOJURE_CP`. The classpath must include `src` and `resources`
relative to the variant directory. JDK 11 or newer is required.

## Common Lisp · Hunchentoot, Dexador and Mito

Set `PROGRAM2_QUICKLISP_SETUP` to a Quicklisp setup file using the 2024-10-12
distribution and `PROGRAM2_SBCL` or `PROGRAM2_ECL` to the chosen executable
if it is outside PATH. First use may compile dependencies. Application
compilation and server startup can be measured separately.

```sh
impl=programs/program-2/implementations/gpt-6.2-sol--codex/common-lisp
export PROGRAM2_QUICKLISP_SETUP=/absolute/path/to/quicklisp/setup.lisp
export PROGRAM2_LISP_PROFILE=debug
export PROGRAM2_LISP_FASL=/tmp/program2-sbcl-debug.fasl
sbcl --script "$impl/launch.lisp" build
python3 programs/program-2/run.py --variant sbcl

export PROGRAM2_LISP_PROFILE=optimized
export PROGRAM2_LISP_FASL=/tmp/program2-sbcl-optimized.fasl
sbcl --script "$impl/launch.lisp" build
python3 programs/program-2/run.py --variant sbcl
```

For ECL use `ecl -norc -shell "$impl/launch.lisp" -- build`, a separate
`PROGRAM2_LISP_FASL` path, and `--variant ecl`. Do not share FASLs across
implementations. `debug` requests `(speed 0) (debug 3) (safety 3)`.
`optimized` requests `(speed 3) (debug 1) (safety 3)`. These declarations apply
only to the application. They leave dependency builds and safety unchanged.
See [SBCL compiler policy](https://www.sbcl.org/manual/#Compiler-Policy)
and [Cargo profiles](https://doc.rust-lang.org/cargo/reference/profiles.html).

## Pharo · Zinc and Glorp

Use a copy of a Pharo 13 image and its sources and changes files. The setup
command installs SQLite3 and Glorp into the copy and saves it.

```sh
impl=programs/program-2/implementations/gpt-6.2-sol--codex/pharo
export PHARO_VM=/absolute/path/to/pharo
export PROGRAM2_PHARO_IMAGE=/absolute/path/to/program2/Pharo.image
"$PHARO_VM" "$PROGRAM2_PHARO_IMAGE" --no-default-preferences st --quit "$impl/install.st"
python3 programs/program-2/run.py --variant pharo
```

The application itself is filed in from `app.st` when the image starts.

## First SQLite results

Each measured run used one sequential client, 1,000 mixed requests, and two
2,000-reservation expiry scenarios (100 and 1,800 due). The host was Linux under
WSL2. These exploratory runs share the host with some setup work. Readiness
is sampled by polling every 10 ms. Expiry entries are single HTTP calls.
[Raw results](../../reports/program-2-gpt-6.2-sol--codex.json)

| Variant | Ready (ms) | Mixed req/s | p50 (ms) | p95 (ms) | p99 (ms) | Sparse expiry (ms) | Dense expiry (ms) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| python | 903.2 | 173.0 | 4.95 | 14.03 | 17.98 | 39.7 | 257.1 |
| rust-debug | 12.5 | 479.6 | 1.44 | 6.95 | 11.04 | 15.0 | 215.3 |
| rust-release | 12.7 | 773.3 | 0.97 | 2.72 | 8.28 | 7.2 | 70.0 |
| sbcl-debug | 3548.4 | 285.8 | 2.35 | 11.62 | 14.18 | 97.8 | 1335.4 |
| sbcl-optimized | 3174.6 | 435.3 | 1.86 | 5.09 | 12.15 | 79.0 | 1767.3 |
| pharo | 1873.6 | 67.7 | 14.29 | 32.96 | 50.50 | 325.3 | 3294.5 |

Rust's clean dev and release builds took about 83 s and 114 s including
dependencies. The executables measured 31.0 MiB and 2.59 MiB. SBCL's warm
profile build commands took 9.4 s and 8.1 s including launcher and dependency
loading. The generated application FASLs measured 101.6 KiB and 69.3 KiB.
Both FASLs are loaded by SBCL at runtime.

## Launch a server and use its CLI

Each stack exposes the server commands `migrate`, `seed`, and `serve`,
accepting `--database-url`. Use `--benchmark-mode` on `serve` to enable
manual clock advancement. For example, with the Python executables:

```sh
impl=programs/program-2/implementations/gpt-6.2-sol--codex/python
/tmp/program2-python/bin/python "$impl/server.py" migrate --database-url sqlite:///inventory.db
/tmp/program2-python/bin/python "$impl/server.py" seed --database-url sqlite:///inventory.db
/tmp/program2-python/bin/python "$impl/server.py" serve --database-url sqlite:///inventory.db --benchmark-mode
# In a separate terminal from the repository root:
python3 "$impl/cli.py" health
python3 "$impl/cli.py" reserve --item A=3 --item B=2 --ttl 30 --request-id example-1
```

The [implementation journal](journal.md) records the work and the detours.
