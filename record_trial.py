#!/usr/bin/env python3
"""Record one agent invocation and a separate acceptance check."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter_ns

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--program", default="program-1")
    parser.add_argument("--agent", default="gpt-6.2-sol--codex")
    parser.add_argument("--variant", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("supply the agent command after --")

    manifest_path = ROOT / "programs" / args.program / "implementations" / args.agent / "variants.json"
    manifest = json.loads(manifest_path.read_text())
    variant = next((v for v in manifest["variants"] if v["id"] == args.variant), None)
    if variant is None:
        parser.error("unknown variant")
    output = args.output.resolve()
    with output.open("x") as report:
        trial = {
            "program": args.program, "model": manifest["model"], "harness": manifest["harness"],
            "variant": variant["id"], "language": variant["language"],
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "agent_wall_seconds": None, "agent_exit_code": None,
            "acceptance_wall_seconds": None, "acceptance_exit_code": None,
            "input_tokens": None, "output_tokens": None, "human_interventions": None,
        }
        try:
            start = perf_counter_ns()
            try:
                trial["agent_exit_code"] = subprocess.run(command, cwd=ROOT).returncode
            finally:
                trial["agent_wall_seconds"] = round((perf_counter_ns() - start) / 1e9, 3)

            start = perf_counter_ns()
            acceptance = subprocess.run(
                [sys.executable, str(ROOT / "bench.py"), "--program", args.program,
                 "--agent", args.agent, "--variant", args.variant, "--warmup", "0",
                 "--rounds", "1", "--requests", "20"],
                cwd=ROOT, stdout=subprocess.DEVNULL,
            )
            trial["acceptance_wall_seconds"] = round((perf_counter_ns() - start) / 1e9, 3)
            trial["acceptance_exit_code"] = acceptance.returncode
        finally:
            json.dump(trial, report, indent=2)
            report.write("\n")
    return int(bool(trial["agent_exit_code"] or trial["acceptance_exit_code"]))


if __name__ == "__main__":
    sys.exit(main())
