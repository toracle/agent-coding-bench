"""ORM models and transaction boundary for inventory reservations."""

import json
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint, ForeignKey, Index, Integer, String, Text,
    create_engine, event, func, select, update,
)
from sqlalchemy.orm import DeclarativeBase, mapped_column, relationship, sessionmaker

MAX_TIME = 2_000_000_000


class Base(DeclarativeBase):
    pass


class SchemaRevision(Base):
    __tablename__ = "schema_revision"
    id = mapped_column(Integer, primary_key=True)
    revision = mapped_column(Integer, nullable=False)


class Stock(Base):
    __tablename__ = "stock"
    __table_args__ = (CheckConstraint("available >= 0"),)
    sku = mapped_column(String(32), primary_key=True)
    available = mapped_column(Integer, nullable=False)


class Clock(Base):
    __tablename__ = "logical_clock"
    id = mapped_column(Integer, primary_key=True)
    now = mapped_column(Integer, nullable=False)


class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (Index("ix_reservation_expiry", "status", "expires_at"),)
    id = mapped_column(String(32), primary_key=True)
    status = mapped_column(String(12), nullable=False)
    expires_at = mapped_column(Integer, nullable=False)
    items = relationship("Item", order_by="Item.sku", cascade="all, delete-orphan")

    def representation(self):
        return {
            "id": self.id, "status": self.status, "expires_at": self.expires_at,
            "items": [{"sku": item.sku, "quantity": item.quantity} for item in self.items],
        }


class Item(Base):
    __tablename__ = "reservation_items"
    reservation_id = mapped_column(ForeignKey("reservations.id"), primary_key=True)
    sku = mapped_column(ForeignKey("stock.sku"), primary_key=True)
    quantity = mapped_column(Integer, nullable=False)


class RecordedResponse(Base):
    __tablename__ = "recorded_responses"
    key = mapped_column(String(256), primary_key=True)
    fingerprint = mapped_column(Text, nullable=False)
    status = mapped_column(Integer, nullable=False)
    body = mapped_column(Text, nullable=False)


def error(status, code):
    return status, {"error": {"code": code}}


class Inventory:
    def __init__(self, url):
        self.engine = create_engine(url)
        if self.engine.dialect.name == "sqlite":
            @event.listens_for(self.engine, "connect")
            def configure(connection, _):
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA busy_timeout = 10000")
        self.sessions = sessionmaker(self.engine)

    def migrate(self):
        # Revision 1 is the initial schema. Future migrations must preserve its definitions.
        Base.metadata.create_all(self.engine)
        with self.sessions.begin() as session:
            if session.get(SchemaRevision, 1) is None:
                session.add(SchemaRevision(id=1, revision=1))
                session.add(Clock(id=1, now=0))

    def seed(self, stock_a=10, stock_b=5):
        with self.sessions.begin() as session:
            for sku, available in (("A", stock_a), ("B", stock_b)):
                if session.get(Stock, sku) is None:
                    session.add(Stock(sku=sku, available=available))

    def ready(self):
        with self.sessions() as session:
            revision = session.get(SchemaRevision, 1)
            return revision is not None and revision.revision == 1 and session.get(Clock, 1) is not None

    def stock(self, sku):
        with self.sessions() as session:
            stock = session.get(Stock, sku)
            if stock is None or stock.sku != sku:
                return error(404, "unknown_sku")
            return 200, {"sku": sku, "available": stock.available}

    def reservation(self, reservation_id):
        with self.sessions() as session:
            reservation = session.get(Reservation, reservation_id)
            if reservation is None or reservation.id != reservation_id:
                return error(404, "unknown_reservation")
            return 200, reservation.representation()

    def write(self, request_id, path, payload, operation):
        # Hex encoding makes ASCII keys case-sensitive even under a case-insensitive collation.
        key = request_id.encode("ascii").hex()
        fingerprint = json.dumps(["POST", path, payload], sort_keys=True, separators=(",", ":"))
        with self.sessions.begin() as session:
            # Acquire the database write lock (SQLite) or the clock row lock before reads.
            session.execute(update(Clock).where(Clock.id == 1).values(now=Clock.now))
            clock = session.get(Clock, 1)
            previous = session.get(RecordedResponse, key)
            if previous is not None:
                if previous.fingerprint != fingerprint:
                    return error(409, "idempotency_conflict")
                return previous.status, json.loads(previous.body)
            status, body = operation(session, clock)
            if status != 400:
                session.add(RecordedResponse(key=key, fingerprint=fingerprint,
                                             status=status, body=json.dumps(body)))
            return status, body


def reserve(session, clock, payload, max_quantity):
    expires_at = clock.now + payload["ttl"]
    if expires_at > MAX_TIME or any(item["quantity"] > max_quantity for item in payload["items"]):
        return error(400, "invalid_request")
    requested = {item["sku"]: item["quantity"] for item in payload["items"]}
    stock = {row.sku: row for row in session.scalars(select(Stock).where(Stock.sku.in_(requested)))}
    if requested.keys() - stock.keys():
        return error(404, "unknown_sku")
    if any(stock[sku].available < quantity for sku, quantity in requested.items()):
        return error(409, "insufficient_stock")
    reservation = Reservation(id=uuid4().hex, status="pending", expires_at=expires_at)
    for sku, quantity in requested.items():
        stock[sku].available -= quantity
        reservation.items.append(Item(sku=sku, quantity=quantity))
    session.add(reservation)
    return 201, reservation.representation()


def release(session, reservations):
    quantities = {}
    for reservation in reservations:
        for item in reservation.items:
            quantities[item.sku] = quantities.get(item.sku, 0) + item.quantity
    if quantities:
        for row in session.scalars(select(Stock).where(Stock.sku.in_(quantities))):
            row.available += quantities[row.sku]


def transition(session, reservation_id, status):
    reservation = session.get(Reservation, reservation_id)
    if reservation is None or reservation.id != reservation_id:
        return error(404, "unknown_reservation")
    if reservation.status != "pending":
        return error(409, "invalid_transition")
    if status == "cancelled":
        release(session, [reservation])
    reservation.status = status
    return 200, reservation.representation()


def advance(session, clock, seconds):
    now = clock.now + seconds
    if now > MAX_TIME:
        return error(400, "invalid_request")
    due = (Reservation.status == "pending", Reservation.expires_at <= now)
    totals = session.execute(
        select(Item.sku, func.sum(Item.quantity))
        .join(Reservation, Reservation.id == Item.reservation_id)
        .where(*due)
        .group_by(Item.sku)
    ).all()
    for sku, quantity in totals:
        session.execute(update(Stock).where(Stock.sku == sku)
                        .values(available=Stock.available + quantity))
    expired = session.execute(update(Reservation).where(*due).values(status="expired")).rowcount
    clock.now = now
    return 200, {"now": now, "expired": expired}
