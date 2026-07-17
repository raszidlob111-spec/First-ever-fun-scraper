import os
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

import counties

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_ads (
    ad_id TEXT PRIMARY KEY,
    title TEXT,
    category_key TEXT,
    category_label TEXT,
    model_key TEXT,
    price INTEGER,
    url TEXT,
    seller TEXT,
    rating TEXT,
    location TEXT,
    county TEXT,
    posted_display TEXT,
    image_url TEXT,
    first_seen TEXT,
    last_seen TEXT,
    alerted INTEGER DEFAULT 0
);
"""


def init_db(database_url: str = None) -> psycopg.Connection:
    dsn = database_url or os.environ["DATABASE_URL"]
    conn = psycopg.connect(dsn, row_factory=dict_row)
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def is_alerted(conn: psycopg.Connection, ad_id: str) -> bool:
    row = conn.execute("SELECT alerted FROM seen_ads WHERE ad_id = %s", (ad_id,)).fetchone()
    return bool(row and row["alerted"])


def mark_alerted(conn: psycopg.Connection, listing: dict) -> None:
    conn.execute("UPDATE seen_ads SET alerted = 1 WHERE ad_id = %s", (listing["ad_id"],))
    conn.commit()


def upsert_seen(conn: psycopg.Connection, listings: list) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for l in listings:
        county = counties.resolve_county(l.get("location"))
        conn.execute(
            """
            INSERT INTO seen_ads (
                ad_id, title, category_key, category_label, model_key, price, url,
                seller, rating, location, county, posted_display, image_url,
                first_seen, last_seen, alerted
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0)
            ON CONFLICT (ad_id) DO UPDATE SET
                title=excluded.title,
                category_key=excluded.category_key,
                category_label=excluded.category_label,
                model_key=excluded.model_key,
                price=excluded.price,
                url=excluded.url,
                seller=excluded.seller,
                rating=excluded.rating,
                location=excluded.location,
                county=excluded.county,
                posted_display=excluded.posted_display,
                image_url=excluded.image_url,
                last_seen=excluded.last_seen
            """,
            (
                l["ad_id"], l["title"], l.get("category_key"), l.get("category_label"),
                l.get("model_key"), l.get("price"), l["url"], l.get("seller"), l.get("rating"),
                l.get("location"), county, l.get("posted_display"), l.get("image_url"),
                now, now,
            ),
        )
    conn.commit()


def get_categories(conn: psycopg.Connection):
    rows = conn.execute(
        "SELECT DISTINCT category_key, category_label FROM seen_ads WHERE category_key IS NOT NULL"
    ).fetchall()
    return [dict(r) for r in rows]


def get_model_summary(conn: psycopg.Connection, category_key: str = None, q: str = None):
    sql = """
        SELECT category_key, category_label, model_key,
               COUNT(*) AS count,
               MIN(price) AS min_price,
               MAX(price) AS max_price
        FROM seen_ads
        WHERE model_key IS NOT NULL AND price IS NOT NULL
    """
    params = []
    if category_key:
        sql += " AND category_key = %s"
        params.append(category_key)
    if q:
        sql += " AND model_key ILIKE %s"
        params.append(f"%{q}%")
    sql += " GROUP BY category_key, category_label, model_key ORDER BY count DESC"

    rows = [dict(r) for r in conn.execute(sql, params)]

    # Median isn't a builtin aggregate here -- compute it per group in Python.
    for row in rows:
        prices = [
            r["price"] for r in conn.execute(
                "SELECT price FROM seen_ads WHERE category_key = %s AND model_key = %s AND price IS NOT NULL",
                (row["category_key"], row["model_key"]),
            )
        ]
        prices.sort()
        n = len(prices)
        row["median_price"] = prices[n // 2] if n % 2 else (prices[n // 2 - 1] + prices[n // 2]) / 2

    return rows


def get_listings(conn: psycopg.Connection, category_key: str = None, model_key: str = None,
                  county: str = None, q: str = None, limit: int = 300):
    sql = "SELECT * FROM seen_ads WHERE price IS NOT NULL"
    params = []
    if category_key:
        sql += " AND category_key = %s"
        params.append(category_key)
    if model_key:
        sql += " AND model_key = %s"
        params.append(model_key)
    if county:
        sql += " AND county = %s"
        params.append(county)
    if q:
        sql += " AND title ILIKE %s"
        params.append(f"%{q}%")
    sql += " ORDER BY price ASC LIMIT %s"
    params.append(limit)

    return [dict(r) for r in conn.execute(sql, params)]


def get_listing(conn: psycopg.Connection, ad_id: str):
    row = conn.execute("SELECT * FROM seen_ads WHERE ad_id = %s", (ad_id,)).fetchone()
    return dict(row) if row else None


def get_similar_by_county(conn: psycopg.Connection, category_key: str, model_key: str, exclude_ad_id: str = None):
    sql = """
        SELECT * FROM seen_ads
        WHERE category_key = %s AND model_key = %s AND price IS NOT NULL
    """
    params = [category_key, model_key]
    if exclude_ad_id:
        sql += " AND ad_id != %s"
        params.append(exclude_ad_id)

    rows = [dict(r) for r in conn.execute(sql, params)]
    # Postgres's default collation can misorder accented Hungarian county
    # names, so sort in Python using the same accent-folding used to resolve
    # counties in the first place.
    rows.sort(key=lambda r: (counties._fold(r["county"] or ""), r["price"]))
    return rows
