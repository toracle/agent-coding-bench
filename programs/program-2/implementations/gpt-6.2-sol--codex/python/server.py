"""Reference Program 2 REST server."""

import argparse
import os
import re

from flask import Flask, request
from sqlalchemy.exc import SQLAlchemyError
from waitress import serve

from store import Inventory, advance, error, reserve, transition

SKU = re.compile(r"[A-Z0-9_-]{1,32}")
KEY = re.compile(r"[A-Za-z0-9_-]{1,128}")


def positive(value):
    return type(value) is int and 1 <= value <= 1_000_000


def reserve_payload(body):
    if not isinstance(body, dict) or body.keys() != {"items", "ttl"} or not positive(body["ttl"]):
        raise ValueError
    items = body["items"]
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValueError
    seen = set()
    for item in items:
        if not isinstance(item, dict) or item.keys() != {"sku", "quantity"}:
            raise ValueError
        sku = item["sku"]
        if not isinstance(sku, str) or not SKU.fullmatch(sku) or sku in seen or not positive(item["quantity"]):
            raise ValueError
        seen.add(sku)
    return {"items": sorted(items, key=lambda item: item["sku"]), "ttl": body["ttl"]}


def create_app(inventory, benchmark_mode=False):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    app.config["MAX_RESERVATION_QUANTITY"] = 1_000_000

    def send(result):
        status, body = result
        return body, status

    def write(payload, operation):
        key = request.headers.get("Idempotency-Key", "")
        if not KEY.fullmatch(key):
            return send(error(400, "invalid_request"))
        return send(inventory.write(key, request.path, payload, operation))

    @app.get("/healthz")
    def health():
        try:
            if inventory.ready():
                return {"status": "ready"}
        except SQLAlchemyError:
            app.logger.exception("database readiness check failed")
        return {"status": "unavailable"}, 503

    @app.get("/v1/stock/<sku>")
    def stock(sku):
        if not SKU.fullmatch(sku):
            return send(error(400, "invalid_request"))
        return send(inventory.stock(sku))

    @app.get("/v1/reservations/<reservation_id>")
    def reservation(reservation_id):
        return send(inventory.reservation(reservation_id))

    @app.post("/v1/reservations")
    def create_reservation():
        try:
            payload = reserve_payload(request.get_json(silent=True))
        except ValueError:
            return send(error(400, "invalid_request"))
        return write(payload, lambda session, clock: reserve(
            session, clock, payload, app.config["MAX_RESERVATION_QUANTITY"],
        ))

    @app.post("/v1/reservations/<reservation_id>/<action>")
    def change_reservation(reservation_id, action):
        statuses = {"confirm": "confirmed", "cancel": "cancelled"}
        if action not in statuses:
            return send(error(404, "not_found"))
        if request.get_data() and request.get_json(silent=True) != {}:
            return send(error(400, "invalid_request"))
        return write({}, lambda session, _: transition(session, reservation_id, statuses[action]))

    if benchmark_mode:
        @app.post("/v1/clock/advance")
        def advance_clock():
            body = request.get_json(silent=True)
            if not isinstance(body, dict) or body.keys() != {"seconds"} or not positive(body["seconds"]):
                return send(error(400, "invalid_request"))
            return write(body, lambda session, clock: advance(session, clock, body["seconds"]))

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("migrate", "seed", "serve"))
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", "sqlite:///inventory.db"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--benchmark-mode", action="store_true")
    parser.add_argument("--stock-a", type=int, default=10)
    parser.add_argument("--stock-b", type=int, default=5)
    args = parser.parse_args()
    if not (0 <= args.stock_a <= 2_000_000_000 and 0 <= args.stock_b <= 2_000_000_000):
        parser.error("initial stock must be between 0 and 2,000,000,000")
    store = Inventory(args.database_url)
    if args.command == "migrate":
        store.migrate()
    elif args.command == "seed":
        store.seed(args.stock_a, args.stock_b)
    else:
        serve(create_app(store, args.benchmark_mode), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
