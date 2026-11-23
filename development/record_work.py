"""Record an implementation trial in a development journal outside benchmark results."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from time import perf_counter


def append(path, event):
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(event) + "\n")


def summarize(path, stack):
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    trial = []
    for event in events:
        if event["stack"] == stack:
            if event["stage"] == "start":
                trial.clear()
            trial.append(event)
    if not trial or trial[0]["stage"] != "start":
        raise ValueError(f"no start recorded for {stack}")
    passed = next((event for event in trial
                   if event["stage"] == "contract" and event["exit_code"] == 0), None)
    if passed is not None:
        trial = trial[:trial.index(passed) + 1]
    result = {"stack": stack}
    for stage in ("compile", "load", "contract"):
        attempts = [event for event in trial if event["stage"] == stage]
        result[stage] = {
            "attempts": len(attempts),
            "failed": sum(event["exit_code"] != 0 for event in attempts),
            "seconds": round(sum(event["seconds"] for event in attempts), 3),
        }
    started = datetime.fromisoformat(trial[0]["started_at"])
    result["seconds_to_first_pass"] = (
        round((datetime.fromisoformat(passed["finished_at"]) - started).total_seconds(), 3)
        if passed is not None else None
    )
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("journal", type=Path)
    parser.add_argument("stack")
    parser.add_argument("stage", choices=("start", "compile", "load", "contract", "summary"))
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if (args.stage in ("start", "summary")) == bool(command):
        parser.error("start and summary take no command; other stages require one")
    if args.stage == "summary":
        summarize(args.journal, args.stack)
        return 0
    event = {"stack": args.stack, "stage": args.stage,
             "started_at": datetime.now(timezone.utc).isoformat()}
    if args.stage == "start":
        append(args.journal, event)
        return 0
    started = perf_counter()
    exit_code = 127
    try:
        exit_code = subprocess.run(command).returncode
        return exit_code
    finally:
        event.update(seconds=round(perf_counter() - started, 3), exit_code=exit_code,
                     finished_at=datetime.now(timezone.utc).isoformat())
        append(args.journal, event)


if __name__ == "__main__":
    raise SystemExit(main())
