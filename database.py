
import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager

BASE_DIR = Path(__file__).resolve().parent

# On Vercel the project folder is read-only and only /tmp is writable
# (and /tmp is NOT permanent). Set SPENDLAB_DB to use a custom location,
# e.g. a persistent disk on another host.
IS_VERCEL = bool(os.environ.get("VERCEL"))
if os.environ.get("SPENDLAB_DB"):
    DB_PATH = Path(os.environ["SPENDLAB_DB"])
elif IS_VERCEL:
    DB_PATH = Path("/tmp/expense_tracker.db")
else:
    DB_PATH = BASE_DIR / "expense_tracker.db"

CATEGORIES = {
    "income": ["Salary", "Freelance", "Business", "Investment", "Other"],
    "expense": [
        "Food", "Transport", "Shopping", "Bills", "Education",
        "Health", "Entertainment", "Rent", "Travel", "Other"
    ]
}


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Columns added to `suggestions` for the smarter advisor engine.
SUGGESTION_EXTRA_COLUMNS = {
    "kind": "TEXT NOT NULL DEFAULT 'new'",
    "severity": "TEXT NOT NULL DEFAULT 'info'",
    "confidence": "INTEGER NOT NULL DEFAULT 0",
    "projected": "REAL NOT NULL DEFAULT 0",
    "last_month": "REAL NOT NULL DEFAULT 0",
}


def migrate(conn):
    """Add new columns to databases created by an older version."""
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(suggestions)")}
    for name, ddl in SUGGESTION_EXTRA_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE suggestions ADD COLUMN {name} {ddl}")


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL CHECK(type IN ('income','expense')),
                amount REAL NOT NULL CHECK(amount > 0),
                category TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                date TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_transactions_date
                ON transactions(date);

            CREATE TABLE IF NOT EXISTS budgets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                month TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT '*',
                amount REAL NOT NULL CHECK(amount > 0),
                UNIQUE(month, category)
            );

            CREATE TABLE IF NOT EXISTS savings_goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                target REAL NOT NULL CHECK(target > 0),
                saved REAL NOT NULL DEFAULT 0 CHECK(saved >= 0),
                deadline TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                month TEXT NOT NULL,
                category TEXT NOT NULL,
                old_budget REAL NOT NULL,
                suggested_budget REAL NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','accepted','rejected')),
                UNIQUE(month, category)
            );
        """)
        migrate(conn)
