#!/usr/bin/env python3
"""Build and benchmark variants of a line-based workload on Linux."""

import argparse
from datetime import datetime, timezone
import json
from math import ceil
import os
from pathlib import Path
import platform
import select
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter_ns

ROOT = Path(__file__).resolve().parent


def runtime(name, roswell_path):
    return os.environ.get(name.upper(), str(Path.home() / ".roswell" / roswell_path)
                          if (Path.home() / ".roswell" / roswell_path).is_file() else name)


def quantiles(samples):
    values = sorted(samples)
    return {f"p{p}": round(values[ceil(len(values) * p / 100) - 1] / 1000, 3)
            for p in (50, 95, 99)}


def read_reply(pipe, expected):
    deadline = perf_counter_ns() + 10_000_000_000
    reply = bytearray()
    while not reply.endswith(b"\n") and len(reply) <= len(expected):
        remaining = (deadline - perf_counter_ns()) / 1e9
        if remaining <= 0 or not select.select([pipe], [], [], remaining)[0]:
            raise TimeoutError(f"timed out waiting for {expected!r}")
        chunk = os.read(pipe.fileno(), len(expected) + 1)
        if not chunk:
            break
        reply.extend(chunk)
    if reply != expected:
        raise ValueError(f"expected {expected!r}, received {reply!r}")


def measure(command, cwd, ready, cases, warmup, requests):
    started = perf_counter_ns()
    with subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE) as worker:
        try:
            read_reply(worker.stdout, ready)
            startup = perf_counter_ns() - started
            samples = []
            for index in range(warmup + requests):
                payload, expected = cases[index % len(cases)]
                started = perf_counter_ns()
                worker.stdin.write(payload)
                worker.stdin.flush()
                read_reply(worker.stdout, expected)
                if index >= warmup:
                    samples.append(perf_counter_ns() - started)
            worker.stdin.close()
            if worker.wait(timeout=10):
                raise RuntimeError(f"worker exited with status {worker.returncode}")
        finally:
            if worker.poll() is None:
                worker.kill()
    return startup, samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--program", default="program-1")
    parser.add_argument("--agent", default="gpt-6.2-sol--codex")
    parser.add_argument("--variant", action="append", help="repeat to select variants")
    parser.add_argument("--warmup", type=int, default=1000)
    parser.add_argument("--requests", type=int, default=10000)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if min(args.requests, args.rounds) < 1 or args.warmup < 0:
        parser.error("requests and rounds must be positive and warmup nonnegative")

    program = ROOT / "programs" / args.program
    implementation = program / "implementations" / args.agent
    workload = json.loads((program / "workload.json").read_text())
    manifest = json.loads((implementation / "variants.json").read_text())
    variants = [v for v in manifest["variants"] if not args.variant or v["id"] in args.variant]
    if not variants or (args.variant and {v["id"] for v in variants} != set(args.variant)):
        parser.error("unknown variant")

    def line(value):
        return (value + "\n").encode()

    cases = [(line(request), line(reply)) for request, reply in workload["cases"]]
    result = {
        "program": args.program, "model": manifest["model"], "harness": manifest["harness"],
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "environment": {"platform": platform.platform(), "machine": platform.machine(),
                        "logical_cpus": os.cpu_count(), "driver_python": platform.python_version()},
        "warmup_per_round": args.warmup, "requests_per_round": args.requests, "rounds": args.rounds,
        "variants": [],
    }
    machine = platform.machine().replace("_", "-")
    roswell = f"impls/{machine}/linux"
    tools = {"sbcl": runtime("sbcl", f"{roswell}/sbcl-bin/2.5.0/bin/sbcl"),
             "ecl": runtime("ecl", f"{roswell}/ecl/24.5.10/bin/ecl"), "python": sys.executable}
    with TemporaryDirectory(prefix="agent-program-bench-") as directory:
        prepared = []
        for variant in variants:
            out = Path(directory) / variant["id"]
            out.mkdir()
            source = implementation / variant["source"]
            variables = dict(tools, out=str(out), source=str(source))

            def command(key):
                return [arg.format_map(variables) for arg in variant[key]]

            version = subprocess.check_output(command("version"), cwd=out, text=True, timeout=120).strip()
            build_ms = artifact_bytes = None
            if "build" in variant:
                start = perf_counter_ns()
                process = subprocess.run(command("build"), cwd=out, capture_output=True,
                                         text=True, timeout=120)
                if process.returncode:
                    raise RuntimeError(process.stdout + process.stderr)
                build_ms = round((perf_counter_ns() - start) / 1e6, 3)
                artifact_bytes = (out / variant["artifact"]).stat().st_size
            prepared.append((variant, out, command("run"), version, build_ms, artifact_bytes, source, [], []))

        for round_index in range(args.rounds):
            offset = round_index % len(prepared)
            for _, out, run, _, _, _, _, startups, timings in prepared[offset:] + prepared[:offset]:
                startup, samples = measure(run, out, line(workload["ready"]), cases,
                                           args.warmup, args.requests)
                startups.append(startup)
                timings.extend(samples)

        for variant, _, _, version, build_ms, size, source, startups, timings in prepared:
            result["variants"].append({
                "id": variant["id"], "language": variant["language"], "runtime_version": version,
                "nonblank_source_lines": sum(bool(s.strip()) for s in source.read_text().splitlines()),
                "build_ms": build_ms, "executable_bytes": size,
                "startup_us": quantiles(startups), "round_trip_us": quantiles(timings),
                "verified_requests": len(timings),
            })
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded)
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
