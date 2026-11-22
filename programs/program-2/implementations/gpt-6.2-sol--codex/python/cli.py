"""HTTP client for Program 2."""

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


def item(value):
    try:
        sku, count = value.split("=", 1)
        return {"sku": sku, "quantity": int(count)}
    except ValueError:
        raise argparse.ArgumentTypeError("expected SKU=QUANTITY") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://127.0.0.1:8080"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("health")
    sub.add_parser("stock").add_argument("sku")
    sub.add_parser("reservation").add_argument("id")
    reserve = sub.add_parser("reserve")
    reserve.add_argument("--item", action="append", type=item, required=True)
    reserve.add_argument("--ttl", type=int, required=True)
    reserve.add_argument("--request-id", required=True)
    for action in ("confirm", "cancel"):
        cmd = sub.add_parser(action)
        cmd.add_argument("id")
        cmd.add_argument("--request-id", required=True)
    advance = sub.add_parser("advance")
    advance.add_argument("seconds", type=int)
    advance.add_argument("--request-id", required=True)
    args = parser.parse_args()

    command = args.command
    payload = None
    if command == "health":
        path = "/healthz"
    elif command == "stock":
        path = "/v1/stock/" + quote(args.sku, safe="")
    elif command in ("reservation", "confirm", "cancel"):
        path = "/v1/reservations/" + quote(args.id, safe="")
        if command != "reservation":
            path += "/" + command
            payload = {}
    elif command == "reserve":
        path = "/v1/reservations"
        payload = {"items": args.item, "ttl": args.ttl}
    else:
        path = "/v1/clock/advance"
        payload = {"seconds": args.seconds}

    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers.update({"Content-Type": "application/json", "Idempotency-Key": args.request_id})
    try:
        response = urlopen(Request(args.base_url.rstrip("/") + path, data=data, headers=headers), timeout=10)
    except HTTPError as exc:
        response = exc
    except (URLError, OSError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2
    with response:
        sys.stdout.buffer.write(response.read().rstrip(b"\n") + b"\n")
        return 0 if 200 <= response.status < 300 else 1


if __name__ == "__main__":
    sys.exit(main())
