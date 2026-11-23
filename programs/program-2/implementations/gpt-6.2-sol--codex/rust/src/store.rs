use diesel::prelude::*;
use diesel::sqlite::SqliteConnection;
use serde_json::{json, Value};
use uuid::Uuid;

pub type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
pub type Reply = (u16, Value);

diesel::table! { stock (sku) { sku -> Text, available -> Integer, } }
diesel::table! { clock (id) { id -> Integer, now -> Integer, } }
diesel::table! {
    reservations (id) { id -> Text, status -> Text, expires_at -> Integer, }
}
diesel::table! {
    reservation_items (reservation_id, sku) { reservation_id -> Text, sku -> Text, quantity -> Integer, }
}
diesel::table! {
    recorded_responses (key) { key -> Text, fingerprint -> Text, status -> Integer, body -> Text, }
}

diesel::allow_tables_to_appear_in_same_query!(reservations, reservation_items);

pub const MAX_TIME: i32 = 2_000_000_000;
pub const MAX_INPUT: i32 = 1_000_000;
const SCHEMA: &str = include_str!("../migrations/0001_inventory.sql");

pub fn error(status: u16, code: &str) -> Reply {
    (status, json!({"error": {"code": code}}))
}

pub fn connect(database_url: &str) -> Result<SqliteConnection> {
    use diesel::connection::SimpleConnection;
    let path = database_url
        .strip_prefix("sqlite:///")
        .ok_or("this build supports sqlite:/// database URLs")?;
    let mut db = SqliteConnection::establish(path)?;
    db.batch_execute("PRAGMA foreign_keys = ON; PRAGMA busy_timeout = 10000;")?;
    Ok(db)
}

pub fn migrate(db: &mut SqliteConnection) -> Result<()> {
    use diesel::connection::SimpleConnection;
    db.immediate_transaction::<_, diesel::result::Error, _>(|db| db.batch_execute(SCHEMA))?;
    Ok(())
}

pub fn seed(db: &mut SqliteConnection, a: i32, b: i32) -> Result<()> {
    db.immediate_transaction::<_, diesel::result::Error, _>(|db| {
        for (sku, quantity) in [("A", a), ("B", b)] {
            if stock::table
                .find(sku)
                .first::<(String, i32)>(db)
                .optional()?
                .is_none()
            {
                diesel::insert_into(stock::table)
                    .values((stock::sku.eq(sku), stock::available.eq(quantity)))
                    .execute(db)?;
            }
        }
        Ok(())
    })?;
    Ok(())
}

pub fn ready(db: &mut SqliteConnection) -> Result<Reply> {
    clock::table.find(1).select(clock::now).first::<i32>(db)?;
    Ok((200, json!({"status": "ready"})))
}

pub fn stock(db: &mut SqliteConnection, sku: &str) -> Result<Reply> {
    let count = stock::table
        .find(sku)
        .select(stock::available)
        .first::<i32>(db)
        .optional()?;
    Ok(count.map_or_else(
        || error(404, "unknown_sku"),
        |count| (200, json!({"sku": sku, "available": count})),
    ))
}

type Reservation = (String, String, i32);

fn find(db: &mut SqliteConnection, id: &str) -> QueryResult<Option<Reservation>> {
    reservations::table
        .find(id)
        .first::<Reservation>(db)
        .optional()
}

fn items(db: &mut SqliteConnection, id: &str) -> QueryResult<Vec<(String, i32)>> {
    reservation_items::table
        .filter(reservation_items::reservation_id.eq(id))
        .order(reservation_items::sku)
        .select((reservation_items::sku, reservation_items::quantity))
        .load(db)
}

fn render(id: &str, status: &str, expires_at: i32, items: &[(String, i32)]) -> Value {
    let items: Vec<_> = items
        .iter()
        .map(|(sku, quantity)| json!({"sku": sku, "quantity": quantity}))
        .collect();
    json!({"id": id, "status": status, "expires_at": expires_at, "items": items})
}

pub fn reservation(db: &mut SqliteConnection, id: &str) -> Result<Reply> {
    let Some((_, status, expires)) = find(db, id)? else {
        return Ok(error(404, "unknown_reservation"));
    };
    Ok((200, render(id, &status, expires, &items(db, id)?)))
}

pub fn write(
    db: &mut SqliteConnection,
    key: &str,
    path: &str,
    payload: &Value,
    apply: impl FnOnce(&mut SqliteConnection, i32) -> Result<Reply>,
) -> Result<Reply> {
    let key: String = key.bytes().map(|byte| format!("{byte:02x}")).collect();
    let fingerprint = serde_json::to_string(&json!(["POST", path, payload]))?;
    db.immediate_transaction(|db| {
        if let Some((_, prior, status, body)) = recorded_responses::table
            .find(&key)
            .first::<(String, String, i32, String)>(db)
            .optional()?
        {
            return if prior == fingerprint {
                Ok((status as u16, serde_json::from_str(&body)?))
            } else {
                Ok(error(409, "idempotency_conflict"))
            };
        }
        let now = clock::table.find(1).select(clock::now).first(db)?;
        let (status, body) = apply(db, now)?;
        if status != 400 {
            diesel::insert_into(recorded_responses::table)
                .values((
                    recorded_responses::key.eq(&key),
                    recorded_responses::fingerprint.eq(&fingerprint),
                    recorded_responses::status.eq(i32::from(status)),
                    recorded_responses::body.eq(serde_json::to_string(&body)?),
                ))
                .execute(db)?;
        }
        Ok((status, body))
    })
}

fn adjust(db: &mut SqliteConnection, sku: &str, quantity: i32) -> QueryResult<usize> {
    diesel::update(stock::table.find(sku))
        .set(stock::available.eq(stock::available + quantity))
        .execute(db)
}

pub fn reserve(
    db: &mut SqliteConnection,
    now: i32,
    items: &[(String, i32)],
    ttl: i32,
) -> Result<Reply> {
    let expires = now + ttl;
    if expires > MAX_TIME {
        return Ok(error(400, "invalid_request"));
    }
    let mut sufficient = true;
    for (sku, quantity) in items {
        let Some(available) = stock::table
            .find(sku)
            .select(stock::available)
            .first::<i32>(db)
            .optional()?
        else {
            return Ok(error(404, "unknown_sku"));
        };
        sufficient &= available >= *quantity;
    }
    if !sufficient {
        return Ok(error(409, "insufficient_stock"));
    }
    let id = Uuid::new_v4().simple().to_string();
    diesel::insert_into(reservations::table)
        .values((
            reservations::id.eq(&id),
            reservations::status.eq("pending"),
            reservations::expires_at.eq(expires),
        ))
        .execute(db)?;
    for (sku, quantity) in items {
        adjust(db, sku, -*quantity)?;
        diesel::insert_into(reservation_items::table)
            .values((
                reservation_items::reservation_id.eq(&id),
                reservation_items::sku.eq(sku),
                reservation_items::quantity.eq(*quantity),
            ))
            .execute(db)?;
    }
    Ok((201, render(&id, "pending", expires, items)))
}

pub fn transition(db: &mut SqliteConnection, id: &str, next: &str) -> Result<Reply> {
    let Some((_, status, expires)) = find(db, id)? else {
        return Ok(error(404, "unknown_reservation"));
    };
    if status != "pending" {
        return Ok(error(409, "invalid_transition"));
    }
    let items = items(db, id)?;
    if next != "confirmed" {
        for (sku, quantity) in &items {
            adjust(db, sku, *quantity)?;
        }
    }
    diesel::update(reservations::table.find(id))
        .set(reservations::status.eq(next))
        .execute(db)?;
    Ok((200, render(id, next, expires, &items)))
}

pub fn advance(db: &mut SqliteConnection, old: i32, seconds: i32) -> Result<Reply> {
    let now = old + seconds;
    if now > MAX_TIME {
        return Ok(error(400, "invalid_request"));
    }
    let totals = reservation_items::table
        .inner_join(reservations::table.on(reservations::id.eq(reservation_items::reservation_id)))
        .filter(reservations::status.eq("pending"))
        .filter(reservations::expires_at.le(now))
        .group_by(reservation_items::sku)
        .select((
            reservation_items::sku,
            diesel::dsl::sum(reservation_items::quantity),
        ))
        .load::<(String, Option<i64>)>(db)?;
    for (sku, quantity) in totals {
        adjust(
            db,
            &sku,
            i32::try_from(quantity.ok_or("empty expiry group")?)?,
        )?;
    }
    let expired = diesel::update(
        reservations::table
            .filter(reservations::status.eq("pending"))
            .filter(reservations::expires_at.le(now)),
    )
    .set(reservations::status.eq("expired"))
    .execute(db)?;
    diesel::update(clock::table.find(1))
        .set(clock::now.eq(now))
        .execute(db)?;
    Ok((200, json!({"now": now, "expired": expired})))
}
