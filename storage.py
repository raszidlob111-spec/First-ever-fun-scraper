import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_ads (
    ad_id TEXT PRIMARY KEY,
    title TEXT,
    model_key TEXT,
    price INTEGER,
    url TEXT,
    seller TEXT,
    location TEXT,
    first_seen TEXT,
    last_seen TEXT,
    alerted INTEGER DEFAULT 0
);
"""


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def is_alerted(conn: sqlite3.Connection, ad_id: str) -> bool:
    row = conn.execute("SELECT alerted FROM seen_ads WHERE ad_id = ?", (ad_id,)).fetchone()
    return bool(row and row[0])


def mark_alerted(conn: sqlite3.Connection, listing: dict) -> None:
    conn.execute("UPDATE seen_ads SET alerted = 1 WHERE ad_id = ?", (listing["ad_id"],))
    conn.commit()


def upsert_seen(conn: sqlite3.Connection, listings: list) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for l in listings:
        conn.execute(
            """
            INSERT INTO seen_ads (ad_id, title, model_key, price, url, seller, location, first_seen, last_seen, alerted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(ad_id) DO UPDATE SET
                title=excluded.title,
                model_key=excluded.model_key,
                price=excluded.price,
                url=excluded.url,
                seller=excluded.seller,
                location=excluded.location,
                last_seen=excluded.last_seen
            """,
            (
                l["ad_id"], l["title"], l.get("model_key"), l.get("price"),
                l["url"], l.get("seller"), l.get("location"), now, now,
            ),
        )
    conn.commit()
