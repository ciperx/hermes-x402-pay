"""Receipt ledger -- SQLite under HERMES_HOME/plugin-data/hermes-x402-pay/.

Rows are append-only and never pruned. The rolling budget is a SUM over the last
24h of settled rows, so a crash or restart cannot reset the cap.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    url       TEXT NOT NULL,
    method    TEXT,
    status    INTEGER,
    paid      INTEGER,
    price_usd REAL,
    payer     TEXT,
    pay_to    TEXT,
    tx_hash   TEXT,
    note      TEXT
);
CREATE INDEX IF NOT EXISTS payments_ts ON payments(ts);
"""


def _home() -> str:
    home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    return os.path.join(home, "plugin-data", "hermes-x402-pay")


class Ledger:
    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(_home(), "receipts.db")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def record(self, url, method="GET", status=0, paid=False, price_usd=0.0,
               payer="", pay_to="", tx_hash="", note=""):
        with self._conn() as c:
            c.execute(
                "INSERT INTO payments (ts,url,method,status,paid,price_usd,payer,pay_to,tx_hash,note)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (time.time(), url, method, int(status), int(bool(paid)), float(price_usd),
                 payer, pay_to, tx_hash, note),
            )

    def spend_last_24h(self) -> float:
        with self._conn() as c:
            row = c.execute(
                "SELECT COALESCE(SUM(price_usd),0) FROM payments WHERE paid=1 AND ts>=?",
                (time.time() - 86400,),
            ).fetchone()
        return float(row[0] or 0.0)

    def recent(self, limit=10, since_hours=None, paid_only=False):
        limit = max(1, min(int(limit or 10), 200))
        q = "SELECT * FROM payments WHERE 1=1"
        args = []
        if since_hours:
            q += " AND ts>=?"
            args.append(time.time() - float(since_hours) * 3600)
        if paid_only:
            q += " AND paid=1"
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, args).fetchall()]


def to_json(rows):
    return json.dumps(rows, indent=2, default=str)
