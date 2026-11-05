-- Paste this into the D1 database Console (Cloudflare dashboard) once.
CREATE TABLE IF NOT EXISTS trades (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  sym         TEXT NOT NULL,
  exchange    TEXT,
  name        TEXT,
  conf        TEXT,
  entry_date  TEXT NOT NULL,
  entry       REAL NOT NULL,
  stop        REAL NOT NULL,
  target      REAL NOT NULL,
  qty         INTEGER NOT NULL DEFAULT 1,
  status      TEXT NOT NULL DEFAULT 'Open',
  last_price  REAL,
  last_date   TEXT,
  exit_date   TEXT,
  exit_price  REAL,
  UNIQUE (sym, entry_date)
);
CREATE TABLE IF NOT EXISTS events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  received_at  TEXT NOT NULL DEFAULT (datetime('now')),
  body         TEXT NOT NULL
);
