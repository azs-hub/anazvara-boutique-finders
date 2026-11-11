"""SQLite persistence for historically discovered boutiques.

The database file lives at ``data/boutiques.db`` and must survive between
runs so later searches can exclude already-known boutiques.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from deduplication import identity_keys
from scraper import BoutiqueRecord

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "boutiques.db"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS boutiques (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    city TEXT,
    address TEXT,
    website TEXT,
    instagram TEXT,
    email TEXT,
    phone TEXT,
    source_url TEXT,
    date_discovered TEXT NOT NULL,
    website_domain TEXT,
    instagram_username TEXT,
    phone_normalized TEXT,
    name_city_key TEXT
);
"""

# Unique indexes skip NULL keys so incomplete records can still be stored.
CREATE_INDEXES_SQL = (
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_boutiques_website_domain
    ON boutiques(website_domain)
    WHERE website_domain IS NOT NULL;
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_boutiques_instagram_username
    ON boutiques(instagram_username)
    WHERE instagram_username IS NOT NULL;
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_boutiques_phone_normalized
    ON boutiques(phone_normalized)
    WHERE phone_normalized IS NOT NULL;
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_boutiques_name_city
    ON boutiques(name_city_key)
    WHERE name_city_key IS NOT NULL;
    """,
)


class BoutiqueDatabase:
    """Thin SQLite wrapper used by the application and smoke tests."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        """Open a connection with row access by column name."""
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> Path:
        """Create the boutiques table and unique indexes if they do not exist."""
        with self.connect() as connection:
            connection.execute(CREATE_TABLE_SQL)
            for statement in CREATE_INDEXES_SQL:
                connection.execute(statement)
            connection.commit()
        return self.db_path

    def is_known(self, record: BoutiqueRecord | dict) -> bool:
        """Return True if any normalized identity key already exists."""
        keys = identity_keys(record)
        clauses: list[str] = []
        values: list[str] = []
        mapping = {
            "website_domain": keys["website_domain"],
            "instagram_username": keys["instagram_username"],
            "phone_normalized": keys["phone_normalized"],
            "name_city_key": keys["name_city_key"],
        }
        for column, value in mapping.items():
            if value:
                clauses.append(f"{column} = ?")
                values.append(value)
        if not clauses:
            return False
        sql = f"SELECT 1 FROM boutiques WHERE {' OR '.join(clauses)} LIMIT 1"
        with self.connect() as connection:
            row = connection.execute(sql, values).fetchone()
        return row is not None

    def insert_if_new(self, record: BoutiqueRecord | dict) -> int | None:
        """Insert ``record`` when it is not a duplicate.

        Returns:
            The new row id, or ``None`` if the boutique was already known.
        """
        if isinstance(record, BoutiqueRecord):
            data = record.to_dict()
        else:
            data = dict(record)
        if self.is_known(data):
            return None
        keys = identity_keys(data)
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO boutiques (
                    name, city, address, website, instagram, email, phone,
                    source_url, date_discovered, website_domain,
                    instagram_username, phone_normalized, name_city_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data.get("name"),
                    data.get("city"),
                    data.get("address"),
                    data.get("website"),
                    data.get("instagram"),
                    data.get("email"),
                    data.get("phone"),
                    data.get("source_url"),
                    data.get("date_discovered"),
                    keys["website_domain"],
                    keys["instagram_username"],
                    keys["phone_normalized"],
                    keys["name_city_key"],
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def fetch_all(self) -> list[dict[str, str | int | None]]:
        """Return all stored boutiques ordered by id."""
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    id, name, city, address, website, instagram, email, phone,
                    source_url, date_discovered
                FROM boutiques
                ORDER BY id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def count(self) -> int:
        """Return the number of stored boutiques."""
        with self.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS n FROM boutiques").fetchone()
        return int(row["n"])
