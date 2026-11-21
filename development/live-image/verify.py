"""Verify a policy edit against a live worker and a fresh process."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from bench import read_reply


def run_worker(command, directory, policy, actions):
    with subprocess.Popen(command, cwd=directory, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE) as worker:
        try:
            read_reply(worker.stdout, b"ready\n")
            for action in actions:
                if action is None:
                    shutil.copyfile(HERE / "policies/after" / policy, directory / policy)
                    continue
                request, response = action
                worker.stdin.write((request + "\n").encode())
                worker.stdin.flush()
                read_reply(worker.stdout, (response + "\n").encode())
                print(f"  {request} -> {response}", flush=True)
            worker.stdin.close()
            if worker.wait(timeout=10):
                raise RuntimeError(f"worker exited with status {worker.returncode}")
        finally:
            if worker.poll() is None:
                worker.kill()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", choices=("sbcl", "ecl", "pharo"))
    args = parser.parse_args()
    if args.runtime == "pharo":
        launcher = shutil.which(os.environ.get("PHARO_VM", "pharo"))
        image = Path(os.environ.get("PHARO_IMAGE", "Pharo.image")).expanduser().resolve()
        if launcher is None or not image.is_file():
            parser.error("set PHARO_VM and PHARO_IMAGE to the headless launcher and image")
        command = [str(Path(launcher).absolute()), str(image), "--no-default-preferences",
                   "st", "--quit", str(HERE / "worker.st")]
        policy = "policy.st"
    else:
        implementation = {"sbcl": "sbcl-bin/2.5.0", "ecl": "ecl/24.5.10"}[args.runtime]
        command = ["ros", "-L", implementation, "-l", str(HERE / "worker.lisp"), "-q"]
        policy = "policy.lisp"

    before = [("A", "11250"), ("B", "13500")]
    after = [("A", "9562"), ("B", "12750")]
    with TemporaryDirectory(prefix="live-image-") as scratch:
        directory = Path(scratch)
        shutil.copyfile(HERE / "policies/before" / policy, directory / policy)
        print(f"{args.runtime}: one live process", flush=True)
        run_worker(command, directory, policy, [("seed", "seeded"), *before, None, *before,
                                        ("reload", "reloaded"), *after])
        print(f"{args.runtime}: fresh process from the revised source", flush=True)
        run_worker(command, directory, policy, [("seed", "seeded"), *after])
