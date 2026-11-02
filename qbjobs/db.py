"""The job record: one SQLite file (jobs.db) that holds everything QuickBooks doesn't.

Money is stored as whole cents so totals never drift. QuickBooks stays the books: this file never
holds a ledger, only jobs, the hours and materials that went into them, and what the customer is billed.
"""
import sqlite3
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS customers (
    id      INTEGER PRIMARY KEY,
    name    TEXT NOT NULL,
    qbo_id  TEXT UNIQUE,             -- the QuickBooks Customer Id once linked
    email   TEXT,
    phone   TEXT
);
CREATE TABLE IF NOT EXISTS items (   -- a copy of your QuickBooks products and services
    qbo_id      TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    type        TEXT,
    unit_price  INTEGER              -- cents
);
CREATE TABLE IF NOT EXISTS techs (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    cost_rate  INTEGER NOT NULL      -- cents per hour, what an hour costs you (wage + taxes + benefits)
);
CREATE TABLE IF NOT EXISTS jobs (
    id                INTEGER PRIMARY KEY,
    number            TEXT NOT NULL UNIQUE,   -- becomes the invoice number in QuickBooks
    customer_id       INTEGER NOT NULL REFERENCES customers(id),
    title             TEXT NOT NULL,
    address           TEXT,
    status            TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'invoiced')),
    quoted            INTEGER,                -- cents, what you quoted
    created_at        TEXT NOT NULL DEFAULT (date('now')),
    completed_at      TEXT,
    notes             TEXT,
    qbo_invoice_id    TEXT,
    qbo_subtotal      INTEGER,                -- cents, before sales tax
    qbo_total         INTEGER,                -- cents, with sales tax
    qbo_balance       INTEGER,                -- cents still owed
    invoiced_at       TEXT,
    invoice_attempt   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS labor (
    id         INTEGER PRIMARY KEY,
    job_id     INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    tech_id    INTEGER NOT NULL REFERENCES techs(id),
    work_date  TEXT NOT NULL DEFAULT (date('now')),
    hours      REAL NOT NULL CHECK (hours > 0),
    cost_rate  INTEGER NOT NULL      -- cents per hour, copied from the tech when the hours are logged
);
CREATE TABLE IF NOT EXISTS materials (
    id           INTEGER PRIMARY KEY,
    job_id       INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    description  TEXT NOT NULL,
    qty          REAL NOT NULL CHECK (qty > 0),
    unit_cost    INTEGER NOT NULL    -- cents, what you paid
);
CREATE TABLE IF NOT EXISTS bill_lines (  -- what the customer is charged; these become invoice lines
    id           INTEGER PRIMARY KEY,
    job_id       INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    description  TEXT NOT NULL,
    qty          REAL NOT NULL CHECK (qty > 0),
    unit_price   INTEGER NOT NULL,   -- cents
    qbo_item_id  TEXT                -- blank = the default item in settings
);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or config.DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def cents(amount) -> int:
    """'12.34', 12.34 or Decimal -> 1234. Rounds half up to the cent."""
    if isinstance(amount, int):
        return amount * 100
    d = Decimal(str(amount).replace("$", "").replace(",", "").strip())
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def dollars(c: int | None) -> str:
    if c is None:
        return "-"
    sign = "-" if c < 0 else ""
    c = abs(c)
    return f"{sign}${c // 100:,}.{c % 100:02d}"


def line_amount(qty: float, unit_cents: int) -> int:
    """qty x unit price, rounded to the cent the way QuickBooks does."""
    return int((Decimal(str(qty)) * unit_cents).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def get_setting(conn, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    conn.commit()


# ---- jobs -------------------------------------------------------------------------------------------

def new_job(conn, customer_id: int, title: str, quoted: int | None = None, address: str | None = None,
            number: str | None = None, created_at: str | None = None) -> sqlite3.Row:
    if not number:
        prefix = get_setting(conn, "job_prefix", "J-")
        start = int(get_setting(conn, "job_start", "1001"))
        count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        number = f"{prefix}{start + count}"
        while conn.execute("SELECT 1 FROM jobs WHERE number = ?", (number,)).fetchone():
            count += 1
            number = f"{prefix}{start + count}"
    cur = conn.execute(
        "INSERT INTO jobs (number, customer_id, title, quoted, address, created_at) "
        "VALUES (?, ?, ?, ?, ?, COALESCE(?, date('now')))",
        (number, customer_id, title, quoted, address, created_at))
    conn.commit()
    return get_job(conn, cur.lastrowid)


def get_job(conn, ref) -> sqlite3.Row:
    """A job by id (12) or by number (J-1001)."""
    ref = str(ref).strip()
    if ref.isdigit():
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (int(ref),)).fetchone()
    else:
        row = conn.execute("SELECT * FROM jobs WHERE number = ? COLLATE NOCASE", (ref,)).fetchone()
    if not row:
        raise LookupError(f"No job {ref!r}")
    return row


def add_labor(conn, job_id: int, tech_id: int, hours: float, work_date: str | None = None) -> None:
    rate = conn.execute("SELECT cost_rate FROM techs WHERE id = ?", (tech_id,)).fetchone()
    if not rate:
        raise LookupError(f"No tech {tech_id}")
    conn.execute("INSERT INTO labor (job_id, tech_id, hours, cost_rate, work_date) "
                 "VALUES (?, ?, ?, ?, COALESCE(?, date('now')))", (job_id, tech_id, hours, rate[0], work_date))
    conn.commit()


def add_material(conn, job_id: int, description: str, qty: float, unit_cost: int) -> None:
    conn.execute("INSERT INTO materials (job_id, description, qty, unit_cost) VALUES (?, ?, ?, ?)",
                 (job_id, description, qty, unit_cost))
    conn.commit()


def add_bill_line(conn, job_id: int, description: str, qty: float, unit_price: int,
                  qbo_item_id: str | None = None) -> None:
    job = get_job(conn, job_id)
    if job["status"] == "invoiced":
        raise ValueError(f"{job['number']} is already invoiced; change the invoice in QuickBooks instead")
    conn.execute("INSERT INTO bill_lines (job_id, description, qty, unit_price, qbo_item_id) VALUES (?, ?, ?, ?, ?)",
                 (job_id, description, qty, unit_price, qbo_item_id))
    conn.commit()


def mark_done(conn, job_id: int, completed_at: str | None = None) -> None:
    conn.execute("UPDATE jobs SET status = 'done', completed_at = COALESCE(?, date('now')) "
                 "WHERE id = ? AND status = 'open'", (completed_at, job_id))
    conn.commit()
