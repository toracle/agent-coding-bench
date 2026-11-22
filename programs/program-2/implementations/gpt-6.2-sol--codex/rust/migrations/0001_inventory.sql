CREATE TABLE IF NOT EXISTS stock (
  sku TEXT NOT NULL PRIMARY KEY, available INTEGER NOT NULL CHECK (available >= 0)
);
CREATE TABLE IF NOT EXISTS clock (id INTEGER NOT NULL PRIMARY KEY, now INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS reservations (
  id TEXT NOT NULL PRIMARY KEY, status TEXT NOT NULL, expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_expiry ON reservations(status, expires_at);
CREATE TABLE IF NOT EXISTS reservation_items (
  reservation_id TEXT NOT NULL REFERENCES reservations(id),
  sku TEXT NOT NULL REFERENCES stock(sku),
  quantity INTEGER NOT NULL,
  PRIMARY KEY (reservation_id, sku)
);
CREATE TABLE IF NOT EXISTS recorded_responses (
  key TEXT NOT NULL PRIMARY KEY, fingerprint TEXT NOT NULL, status INTEGER NOT NULL, body TEXT NOT NULL
);
INSERT INTO clock (id, now) SELECT 1, 0 WHERE NOT EXISTS (SELECT 1 FROM clock WHERE id = 1);
