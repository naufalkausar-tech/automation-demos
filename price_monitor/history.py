"""SQLite price history: one row per offer per run, so each run can say what changed since the last one."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .core import Offer, Product

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, at TEXT NOT NULL, note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS offers (
    run_id INTEGER NOT NULL REFERENCES runs(id), key TEXT NOT NULL, competitor TEXT, name TEXT, url TEXT,
    price REAL, in_stock INTEGER, PRIMARY KEY (run_id, key));
-- previous_offers looks up the latest run per key; without this every lookup scans the whole history
CREATE INDEX IF NOT EXISTS offers_by_key ON offers (key, run_id);
CREATE TABLE IF NOT EXISTS our_prices (
    run_id INTEGER NOT NULL REFERENCES runs(id), sku TEXT NOT NULL, price REAL, PRIMARY KEY (run_id, sku));
CREATE TABLE IF NOT EXISTS links (
    run_id INTEGER NOT NULL REFERENCES runs(id), sku TEXT NOT NULL, key TEXT NOT NULL, PRIMARY KEY (run_id, sku, key));
"""
OUR_PRICE = "(our price)"


def open_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    return conn


def previous_offers(conn: sqlite3.Connection) -> dict[str, tuple[float | None, bool | None]]:
    """Last recorded (price, in_stock) per offer key. Call before recording the current run."""
    rows = conn.execute("SELECT key, price, in_stock FROM offers o "
                        "WHERE run_id = (SELECT MAX(run_id) FROM offers WHERE key = o.key)")
    return {key: (price, None if stock is None else bool(stock)) for key, price, stock in rows}


def record_run(conn: sqlite3.Connection, offers: list[Offer], products: list[Product],
               linked: dict[str, list[Offer]], note: str = "", at: str | None = None) -> int:
    at = at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with conn:
        run_id = conn.execute("INSERT INTO runs (at, note) VALUES (?, ?)", (at, note)).lastrowid
        conn.executemany("INSERT OR REPLACE INTO offers VALUES (?, ?, ?, ?, ?, ?, ?)",
                         [(run_id, o.key, o.competitor, o.name, o.url, o.price,
                           None if o.in_stock is None else int(o.in_stock)) for o in offers])
        conn.executemany("INSERT OR REPLACE INTO our_prices VALUES (?, ?, ?)",
                         [(run_id, p.sku, p.our_price) for p in products])
        conn.executemany("INSERT OR REPLACE INTO links VALUES (?, ?, ?)",
                         [(run_id, sku, o.key) for sku, found in linked.items() for o in found])
    return run_id


def history_rows(conn: sqlite3.Connection, last_runs: int = 30) -> list[dict]:
    """Our price and every matched competitor price per SKU per run, newest run first, with the change since
    the previous run in which that SKU/offer pair was seen."""
    first = conn.execute("SELECT COALESCE(MIN(id), 0) FROM (SELECT id FROM runs ORDER BY id DESC LIMIT ?)",
                         (last_runs,)).fetchone()[0]
    rows = conn.execute(
        "SELECT r.id, r.at, r.note, l.sku, o.key, o.competitor, o.name, o.url, o.price, o.in_stock "
        "FROM links l JOIN runs r ON r.id = l.run_id JOIN offers o ON o.run_id = l.run_id AND o.key = l.key "
        "WHERE r.id >= ? "
        "UNION ALL "
        "SELECT r.id, r.at, r.note, p.sku, '', ?, '', '', p.price, NULL "
        "FROM our_prices p JOIN runs r ON r.id = p.run_id WHERE r.id >= ? "
        "ORDER BY 4, 5, 1", (first, OUR_PRICE, first)).fetchall()
    out, last = [], {}
    for run_id, at, note, sku, key, source, name, url, price, stock in rows:
        before = last.get((sku, key))
        last[(sku, key)] = price
        out.append({"run_id": run_id, "at": at, "note": note, "sku": sku, "source": source, "product": name,
                    "url": url, "price": price, "in_stock": None if stock is None else bool(stock),
                    "change": None if before is None or price is None else round(price - before, 2)})
    out.sort(key=lambda r: (-r["run_id"], r["sku"], r["source"] != OUR_PRICE, r["source"]))
    return out
