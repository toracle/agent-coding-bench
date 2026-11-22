"""Exercise a Program 2 submission over HTTP."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import platform
import socket
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier
from time import monotonic, perf_counter_ns, sleep

HERE = Path(__file__).resolve().parent


class Client:
    def __init__(self, port):
        self.port = port
        self.connection = HTTPConnection("127.0.0.1", port, timeout=30)

    def request(self, path, expected, *, key=None, body=None):
        headers = {"Accept": "application/json"}
        if key is not None:
            headers["Idempotency-Key"] = key
        if body is not None:
            headers["Content-Type"] = "application/json"
        started = perf_counter_ns()
        self.connection.request("POST" if key is not None or body is not None else "GET",
                                path, None if body is None else json.dumps(body), headers)
        response = self.connection.getresponse()
        data = response.read()
        elapsed = perf_counter_ns() - started
        if response.status not in expected:
            raise AssertionError(f"{path}: expected status {expected}, got {response.status}: {data!r}")
        return response.status, json.loads(data), elapsed

    def reserve(self, key, items, ttl, expected=(201,)):
        body = {"items": [{"sku": sku, "quantity": count} for sku, count in items], "ttl": ttl}
        return self.request("/v1/reservations", expected, key=key, body=body)

    def change(self, reservation, action, key, expected=(200,)):
        return self.request(f"/v1/reservations/{reservation}/{action}", expected, key=key)

    def advance(self, key, seconds, timeout=30):
        self.connection.timeout = timeout
        if self.connection.sock is not None:
            self.connection.sock.settimeout(timeout)
        try:
            return self.request("/v1/clock/advance", (200,), key=key, body={"seconds": seconds})
        finally:
            self.connection.timeout = 30
            if self.connection.sock is not None:
                self.connection.sock.settimeout(30)

    def stock(self, sku, count):
        _, result, elapsed = self.request(f"/v1/stock/{sku}", (200,))
        assert result == {"sku": sku, "available": count}, result
        return elapsed

    def close(self):
        self.connection.close()


class Submission:
    def __init__(self, manifest, directory):
        self.manifest = manifest
        self.directory = directory
        self.source = HERE / manifest["directory"]
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]

    def command(self, tool, *args):
        rust_bin = os.environ.get("PROGRAM2_RUST_BIN", str(self.source / "target/release/inventory-reservations"))
        if self.manifest.get("clojure") and os.environ.get("PROGRAM2_CLOJURE_CP"):
            prefix = ["java", "-cp", os.environ["PROGRAM2_CLOJURE_CP"], "clojure.main", "-m", "inventory.main"]
            return prefix + (["client"] if tool == "cli" else []) + list(args)
        return [arg.format(python=sys.executable, source=self.source, rust_bin=rust_bin,
                           sbcl=os.environ.get("PROGRAM2_SBCL", "sbcl"),
                           ecl=os.environ.get("PROGRAM2_ECL", "ecl"),
                           pharo_vm=os.environ.get("PHARO_VM", "pharo"),
                           pharo_image=os.environ.get("PROGRAM2_PHARO_IMAGE", "Pharo.image"))
                for arg in self.manifest[tool]] + list(args)

    def prepare(self, stock_a=10, stock_b=5):
        self.database = "sqlite:///" + str(self.directory / "inventory.db")
        for command in (("migrate",), ("seed", "--stock-a", str(stock_a), "--stock-b", str(stock_b))):
            subprocess.run(self.command("server", *command, "--database-url", self.database),
                           check=True, timeout=120, cwd=self.source)

    @contextmanager
    def serve(self):
        command = self.command("server", "serve", "--database-url", self.database,
                               "--port", str(self.port), "--benchmark-mode")
        with (self.directory / "server.log").open("w+") as log:
            started = perf_counter_ns()
            process = subprocess.Popen(command, cwd=self.source, stdout=log, stderr=log)
            client = Client(self.port)
            try:
                deadline = monotonic() + 120
                while monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("server exited during startup")
                    try:
                        _, ready, _ = client.request("/healthz", (200, 503))
                        if ready == {"status": "ready"}:
                            break
                    except OSError:
                        client.close()
                    sleep(0.01)
                else:
                    raise TimeoutError("server did not become ready")
                yield client, perf_counter_ns() - started
            except BaseException:
                log.seek(0)
                print(log.read(), file=sys.stderr)
                raise
            finally:
                client.close()
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


def contract(submission):
    submission.prepare()
    with submission.serve() as (client, _):
        client.stock("A", 10)
        client.stock("B", 5)
        _, created, _ = client.reserve("reserve", [("B", 2), ("A", 3)], 10)
        assert created["status"] == "pending" and created["expires_at"] == 10
        assert created["items"] == [{"sku": "A", "quantity": 3}, {"sku": "B", "quantity": 2}]
        assert client.reserve("reserve", [("A", 3), ("B", 2)], 10)[1] == created
        assert client.reserve("reserve", [("A", 1)], 10, (409,))[1]["error"]["code"] == "idempotency_conflict"
        assert client.reserve("failure", [("A", 1), ("B", 4)], 10, (409,))[1]["error"]["code"] == "insufficient_stock"
        client.stock("A", 7)
        client.stock("B", 3)
        assert client.advance("tick-1", 9)[1] == {"now": 9, "expired": 0}
        assert client.advance("tick-2", 1)[1] == {"now": 10, "expired": 1}
        assert client.advance("tick-2", 1)[1] == {"now": 10, "expired": 1}
        path = "/v1/reservations/" + created["id"]
        assert client.request(path, (200,))[1]["status"] == "expired"
        assert client.change(created["id"], "confirm", "expired-confirm", (409,))[1]["error"]["code"] == "invalid_transition"
        client.stock("A", 10)
        client.stock("B", 5)
        # Domain failures are replayed after stock becomes sufficient.
        assert client.reserve("failure", [("A", 1), ("B", 4)], 10, (409,))[1]["error"]["code"] == "insufficient_stock"
        _, confirmed, _ = client.reserve("confirm-r", [("A", 2)], 2)
        assert client.change(confirmed["id"], "confirm", "confirm")[1]["status"] == "confirmed"
        _, cancelled, _ = client.reserve("cancel-r", [("B", 1)], 2)
        original = client.change(cancelled["id"], "cancel", "cancel")[1]
        assert original["status"] == "cancelled"
        assert client.change(cancelled["id"], "cancel", "cancel")[1] == original
        assert client.advance("tick-3", 2)[1] == {"now": 12, "expired": 0}
        client.stock("A", 8)
        client.stock("B", 5)
        assert client.reserve("malformed", [("A", 0)], 10, (400,))[1]["error"]["code"] == "invalid_request"
        client.reserve("malformed", [("A", 1)], 10)

        barrier = Barrier(2, timeout=10)

        def compete(index):
            competitor = Client(submission.port)
            try:
                barrier.wait()
                return competitor.reserve(f"race-{index}", [("B", 4)], 2, (201, 409))[0]
            finally:
                competitor.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(compete, range(2))) == [201, 409]
        client.stock("B", 1)
        assert client.advance("tick-4", 2)[1] == {"now": 14, "expired": 1}
        client.stock("B", 5)

        # A separate CLI process can outlive the server idle keep-alive timeout.
        client.close()
        base = f"http://127.0.0.1:{submission.port}"
        cli = submission.command("cli", "--base-url", base)
        good = subprocess.run([*cli, "stock", "A"], capture_output=True, text=True, timeout=45, cwd=submission.source)
        assert good.returncode == 0, (good.stdout, good.stderr)
        assert json.loads(good.stdout) == {"sku": "A", "available": 7}
        bad = subprocess.run([*cli, "stock", "MISSING"], capture_output=True, text=True, timeout=45, cwd=submission.source)
        assert bad.returncode == 1 and json.loads(bad.stdout)["error"]["code"] == "unknown_sku"

        created_by_cli = subprocess.run(
            [*cli, "reserve", "--item", "B=1", "--ttl", "3", "--request-id", "cli-reserve"],
            capture_output=True, text=True, timeout=45, cwd=submission.source,
        )
        assert created_by_cli.returncode == 0, (created_by_cli.stdout, created_by_cli.stderr)
        created_body = json.loads(created_by_cli.stdout)
        assert client.reserve("cli-reserve", [("B", 1)], 3)[1] == created_body
        assert client.change(created_body["id"], "cancel", "cli-cleanup")[1]["status"] == "cancelled"
        client.stock("B", 5)

    with submission.serve() as (client, _):
        client.stock("A", 7)
        client.stock("B", 5)
        assert client.reserve("reserve", [("A", 3), ("B", 2)], 10)[1] == created
        assert client.request(path, (200,))[1]["status"] == "expired"


def latency(samples):
    ordered = sorted(samples)
    return {f"p{p}": round(ordered[(len(ordered) * p + 99) // 100 - 1] / 1000, 3)
            for p in (50, 95, 99)}


def mixed(submission, cycles):
    submission.prepare(stock_a=10 + cycles, stock_b=5 + cycles)
    with submission.serve() as (client, startup):
        samples = []
        available = 10 + cycles
        started = perf_counter_ns()
        for index in range(cycles):
            samples.append(client.stock("A", available))
            key = f"mixed-{index}"
            _, order, elapsed = client.reserve(key, [("A", 1), ("B", 1)], 50)
            samples.append(elapsed)
            replay = client.reserve(key, [("B", 1), ("A", 1)], 50)
            assert replay[1] == order
            samples.append(replay[2])
            action = "confirm" if index % 2 == 0 else "cancel"
            _, changed, elapsed = client.change(order["id"], action, f"{key}-finish")
            assert changed["status"] == ("confirmed" if action == "confirm" else "cancelled")
            samples.append(elapsed)
            if action == "confirm":
                available -= 1
        duration = (perf_counter_ns() - started) / 1e9
        client.stock("A", available)
        client.stock("B", available - 5)
        return {"startup_ms": round(startup / 1e6, 3), "requests": len(samples),
                "throughput_rps": round(len(samples) / duration, 2), "latency_us": latency(samples)}


def seed_expiry_snapshot(submission, count, expired):
    """Prepare an equivalent SQLite state outside the timed HTTP path."""
    reservations = []
    recorded = []
    for index in range(count):
        identifier = f"fixture-{index:08d}"
        ttl = 1 if index < expired else 2
        items = [{"sku": "A", "quantity": 1}]
        reservations.append((identifier, "pending", ttl))
        fingerprint = json.dumps(["POST", "/v1/reservations", {"items": items, "ttl": ttl}],
                                 sort_keys=True, separators=(",", ":"))
        body = json.dumps({"id": identifier, "status": "pending", "items": items, "expires_at": ttl})
        recorded.append((f"expiry-{index}".encode("ascii").hex(), fingerprint, 201, body))

    with sqlite3.connect(submission.directory / "inventory.db") as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        changed = connection.execute("UPDATE stock SET available=0 WHERE sku='A' AND available=?", (count,))
        assert changed.rowcount == 1
        connection.executemany("INSERT INTO reservations (id,status,expires_at) VALUES (?,?,?)", reservations)
        connection.executemany(
            "INSERT INTO reservation_items (reservation_id,sku,quantity) VALUES (?,'A',1)",
            ((row[0],) for row in reservations),
        )
        connection.executemany(
            "INSERT INTO recorded_responses (key,fingerprint,status,body) VALUES (?,?,?,?)", recorded,
        )


def expiry(submission, count, expired, setup_clients, snapshot):
    submission.prepare(stock_a=count)
    if snapshot:
        seed_expiry_snapshot(submission, count, expired)
    with submission.serve() as (client, _):
        def prepare_group(writer, offset):
            for index in range(offset, count, setup_clients):
                writer.reserve(f"expiry-{index}", [("A", 1)], 1 if index < expired else 2)

        if snapshot:
            client.stock("A", 0)
        elif setup_clients == 1:
            prepare_group(client, 0)
        else:
            client.close()

            def prepare_in_worker(offset):
                writer = Client(submission.port)
                try:
                    prepare_group(writer, offset)
                finally:
                    writer.close()

            with ThreadPoolExecutor(max_workers=setup_clients) as pool:
                list(pool.map(prepare_in_worker, range(setup_clients)))
            client.stock("A", 0)
        try:
            response = client.advance("expiry-clock", 1, timeout=180)
        except TimeoutError:
            return {"active_before": count, "expected_expired": expired, "timeout_ms": 180_000}
        assert response[1] == {"now": 1, "expired": expired}
        client.stock("A", expired)
        return {"active_before": count, "expired": expired, "request_ms": round(response[2] / 1e6, 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", default="python")
    parser.add_argument("--contract-only", action="store_true")
    parser.add_argument("--cycles", type=int, default=250)
    parser.add_argument("--reservations", type=int, default=2000)
    parser.add_argument("--expiry-setup", choices=("http", "snapshot"))
    parser.add_argument("--expiry-setup-clients", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.cycles < 1 or args.reservations < 2:
        parser.error("cycles must be positive and reservations at least two")
    manifest = json.loads((HERE / "variants.json").read_text())
    if args.variant not in manifest:
        parser.error("unknown variant")
    setup = args.expiry_setup or manifest[args.variant].get("expiry_setup", "http")
    setup_clients = (
        args.expiry_setup_clients if args.expiry_setup_clients is not None
        else manifest[args.variant].get("expiry_setup_clients", 1)
    )
    if not 1 <= setup_clients <= 32:
        parser.error("expiry setup clients must be between 1 and 32")
    result = {"variant": args.variant, "platform": platform.platform(), "contract": "passed"}
    with TemporaryDirectory(prefix="bench-program-2-") as scratch:
        root = Path(scratch)

        def submission(name):
            path = root / name
            path.mkdir()
            return Submission(manifest[args.variant], path)

        contract(submission("contract"))
        if not args.contract_only:
            result["request_concurrency"] = 1
            result["mixed"] = mixed(submission("mixed"), args.cycles)
            result["expiry_setup"] = setup
            if setup == "http":
                result["expiry_setup_clients"] = setup_clients
            result["sparse_expiry"] = expiry(submission("sparse"), args.reservations,
                                             max(1, args.reservations // 20), setup_clients, setup == "snapshot")
            result["dense_expiry"] = expiry(submission("dense"), args.reservations,
                                            args.reservations * 9 // 10, setup_clients, setup == "snapshot")
    output = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(output)
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
