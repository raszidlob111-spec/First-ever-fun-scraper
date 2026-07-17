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

CREATE TABLE IF NOT EXISTS scrape_runs (
    id BIGSERIAL PRIMARY KEY,
    category_key TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    listing_count INTEGER NOT NULL,
    complete BOOLEAN NOT NULL
);
"""

# Applied on every startup; each statement is a no-op once it has run.
MIGRATIONS = [
    "ALTER TABLE seen_ads ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active'",
    "ALTER TABLE seen_ads ADD COLUMN IF NOT EXISTS gone_at TIMESTAMPTZ",
    "ALTER TABLE seen_ads ADD COLUMN IF NOT EXISTS posted_at TIMESTAMPTZ",
    "ALTER TABLE seen_ads ADD COLUMN IF NOT EXISTS arrival_confirmed BOOLEAN NOT NULL DEFAULT TRUE",
    # hardverapro's own "jegelve" (frozen/reserved) marker: a seller sets this once a
    # buyer is lined up. It's sticky (OR'd across cycles in upsert_seen) so a listing
    # that goes active -> reserved -> active again (deal fell through, relisted) still
    # remembers it had a live buyer at some point -- that's a materially stronger
    # closure signal than "the ad just vanished", which could equally mean withdrawn
    # or expired.
    "ALTER TABLE seen_ads ADD COLUMN IF NOT EXISTS was_reserved BOOLEAN NOT NULL DEFAULT FALSE",
    "CREATE INDEX IF NOT EXISTS idx_seen_ads_market ON seen_ads (category_key, model_key, status)",
    "CREATE INDEX IF NOT EXISTS idx_seen_ads_gone_at ON seen_ads (gone_at)",
    "CREATE INDEX IF NOT EXISTS idx_scrape_runs_cat ON scrape_runs (category_key, started_at DESC)",
]

# The ads already in the table when this column was added arrived in one flood on
# the watcher's very first cycle, so their first_seen is "when we started looking",
# not "when it was posted" -- their lifetime is unknowable and must not pollute the
# time-to-close median.
BACKFILL_ARRIVAL = """
UPDATE seen_ads SET arrival_confirmed = FALSE
WHERE arrival_confirmed
  AND first_seen IS NOT NULL
  AND first_seen::timestamptz < (
      SELECT MIN(first_seen::timestamptz) + interval '15 minutes'
      FROM seen_ads WHERE first_seen IS NOT NULL
  )
"""

# A cycle that scraped far fewer ads than usual was probably truncated by a failed
# page fetch rather than by 30% of the market selling out in ten minutes. Refuse to
# read closures out of a run that shrank by more than this much.
GONE_MIN_COUNT_RATIO = 0.7

# How many prior runs to median when deciding whether a run looks truncated.
BASELINE_RUNS = 10


def connect(database_url: str = None) -> psycopg.Connection:
    """A plain connection, for readers that trust the schema to already exist."""
    dsn = database_url or os.environ["DATABASE_URL"]
    return psycopg.connect(dsn, row_factory=dict_row)


def init_db(database_url: str = None) -> psycopg.Connection:
    """Connect and bring the schema up to date. Call once per process at startup --
    the migrations are cheap but not free, so request handlers should use connect()."""
    conn = connect(database_url)
    conn.execute(SCHEMA)
    for statement in MIGRATIONS:
        conn.execute(statement)
    conn.execute(BACKFILL_ARRIVAL)
    conn.commit()
    return conn


def is_alerted(conn: psycopg.Connection, ad_id: str) -> bool:
    row = conn.execute("SELECT alerted FROM seen_ads WHERE ad_id = %s", (ad_id,)).fetchone()
    return bool(row and row["alerted"])


def mark_alerted(conn: psycopg.Connection, listing: dict) -> None:
    conn.execute("UPDATE seen_ads SET alerted = 1 WHERE ad_id = %s", (listing["ad_id"],))
    conn.commit()


def has_scrape_history(conn: psycopg.Connection, category_key: str) -> bool:
    """Whether we've ever seen this category in full.

    Only complete runs count. A category whose first runs all failed has still never
    been fully observed, so it stays in bootstrap until one succeeds -- otherwise the
    backfill would be skipped and the first good run would date every ad that piled up
    beforehand as closing at that instant, inventing a spike of closures.
    """
    row = conn.execute(
        "SELECT 1 FROM scrape_runs WHERE category_key = %s AND complete LIMIT 1",
        (category_key,),
    ).fetchone()
    return row is not None


def record_scrape_run(conn: psycopg.Connection, category_key: str, started_at: datetime,
                       listing_count: int, complete: bool) -> None:
    conn.execute(
        "INSERT INTO scrape_runs (category_key, started_at, listing_count, complete) "
        "VALUES (%s, %s, %s, %s)",
        (category_key, started_at, listing_count, complete),
    )
    conn.commit()


def _baseline_count(conn: psycopg.Connection, category_key: str):
    """Median listing_count of recent trustworthy runs, or None if there's no history."""
    rows = conn.execute(
        "SELECT listing_count FROM scrape_runs "
        "WHERE category_key = %s AND complete ORDER BY started_at DESC LIMIT %s",
        (category_key, BASELINE_RUNS),
    ).fetchall()
    counts = sorted(r["listing_count"] for r in rows)
    if not counts:
        return None
    n = len(counts)
    return counts[n // 2] if n % 2 else (counts[n // 2 - 1] + counts[n // 2]) / 2


def mark_gone(conn: psycopg.Connection, category_key: str, present_ad_ids: set,
               gone_at: datetime, complete: bool, backfill: bool = False) -> int:
    """Close out every active or reserved ad in this category that the site no
    longer lists.

    hardverapro archives an ad when it leaves the index -- whether it sold, was
    withdrawn, or expired looks identical from outside -- so this records a
    *closure*, not a confirmed sale. A closure out of 'reserved' is much more
    likely a real sale than one straight out of 'active' (was_reserved carries
    that distinction forward), but both still count as closures here.

    `present_ad_ids` must be every ad id scraped this cycle, including ones the
    watcher filtered out for pricing purposes (unparseable price, ...): those are
    still live listings, and treating them as gone would invent closures every
    cycle.

    `backfill=True` closes the ads out without a timestamp: on the first cycle for
    a category, everything missing has been gone for an unknown length of time, and
    stamping it all with now() would fake a spike of closures. Returns the number of
    ads closed, or 0 if the run looked too short to trust.
    """
    if not complete:
        return 0

    baseline = _baseline_count(conn, category_key)
    if baseline is not None and len(present_ad_ids) < baseline * GONE_MIN_COUNT_RATIO:
        return 0

    cur = conn.execute(
        "UPDATE seen_ads SET status = 'gone', gone_at = %s "
        "WHERE category_key = %s AND status IN ('active', 'reserved') AND NOT (ad_id = ANY(%s))",
        (None if backfill else gone_at, category_key, list(present_ad_ids)),
    )
    conn.commit()
    return cur.rowcount


def upsert_seen(conn: psycopg.Connection, listings: list, bootstrap_categories=frozenset()) -> None:
    """Insert/refresh the ads seen this cycle.

    An ad first seen during a category's very first cycle was already on the site
    when we started watching, so we never saw it arrive and its age is a lower bound
    -- `bootstrap_categories` marks those as arrival_confirmed = FALSE so the
    time-to-close median only uses ads we watched from birth. An ad that reappears
    after being closed goes back to active/reserved (whichever it is this cycle),
    which quietly undoes a false closure.

    A listing with `reserved=True` (hardverapro's "jegelve" marker) is stored with
    status='reserved' rather than 'active' -- it's still not gone, but it's no
    longer available to buy, so it shouldn't be priced against as an active asking
    price. was_reserved is sticky: once True it stays True across cycles even if
    the ad goes back to 'active', so a later closure still remembers it had a buyer.
    """
    now = datetime.now(timezone.utc).isoformat()
    for l in listings:
        county = counties.resolve_county(l.get("location"))
        status = "reserved" if l.get("reserved") else "active"
        conn.execute(
            """
            INSERT INTO seen_ads (
                ad_id, title, category_key, category_label, model_key, price, url,
                seller, rating, location, county, posted_display, posted_at, image_url,
                first_seen, last_seen, alerted, status, gone_at, arrival_confirmed, was_reserved
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0,
                    %s, NULL, %s, %s)
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
                posted_at=excluded.posted_at,
                image_url=excluded.image_url,
                last_seen=excluded.last_seen,
                status=excluded.status,
                gone_at=NULL,
                was_reserved=seen_ads.was_reserved OR excluded.was_reserved
            """,
            (
                l["ad_id"], l["title"], l.get("category_key"), l.get("category_label"),
                l.get("model_key"), l.get("price"), l["url"], l.get("seller"), l.get("rating"),
                l.get("location"), county, l.get("posted_display"), l.get("posted_at"),
                l.get("image_url"), now, now,
                status,
                l.get("category_key") not in bootstrap_categories,
                bool(l.get("reserved")),
            ),
        )
    conn.commit()


def get_categories(conn: psycopg.Connection):
    rows = conn.execute(
        "SELECT DISTINCT category_key, category_label FROM seen_ads WHERE category_key IS NOT NULL"
    ).fetchall()
    return [dict(r) for r in rows]


MARKET_SQL = """
    SELECT
        category_key,
        category_label,
        model_key,
        COUNT(*) FILTER (WHERE status = 'active') AS count,
        COUNT(*) FILTER (WHERE status = 'reserved') AS reserved_now,
        MIN(price) FILTER (WHERE status = 'active') AS min_price,
        MAX(price) FILTER (WHERE status = 'active') AS max_price,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY price::double precision)
            FILTER (WHERE status = 'active') AS median_price,
        COUNT(*) FILTER (WHERE status = 'gone' AND gone_at >= now() - interval '7 days')
            AS closed_7d,
        COUNT(*) FILTER (WHERE status = 'gone' AND gone_at >= now() - interval '30 days')
            AS closed_30d,
        COUNT(*) FILTER (WHERE status = 'gone' AND was_reserved AND gone_at >= now() - interval '30 days')
            AS confirmed_closed_30d,
        MAX(gone_at) AS last_closed_at,
        percentile_cont(0.5) WITHIN GROUP (
            ORDER BY EXTRACT(EPOCH FROM (gone_at - first_seen::timestamptz)) / 86400.0
        ) FILTER (WHERE status = 'gone' AND gone_at IS NOT NULL AND arrival_confirmed)
            AS median_days_to_close,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY price::double precision)
            FILTER (WHERE status = 'gone' AND gone_at >= now() - interval '30 days')
            AS median_closed_price,
        -- Same as median_closed_price but restricted to closures that passed through
        -- 'reserved' first (a real buyer, per hardverapro's own "jegelve" marker) --
        -- the more trustworthy of the two when there's enough sample to use it.
        percentile_cont(0.5) WITHIN GROUP (ORDER BY price::double precision)
            FILTER (WHERE status = 'gone' AND was_reserved AND gone_at >= now() - interval '30 days')
            AS median_confirmed_closed_price
    FROM seen_ads
    WHERE model_key IS NOT NULL AND price IS NOT NULL
"""


def get_model_summary(conn: psycopg.Connection, category_key: str = None, q: str = None,
                       order: str = "count"):
    """Per-model price spread plus how fast that model turns over.

    Prices describe the *active* market -- ads that have already closed shouldn't
    set the asking price you compare against. Closure counts and the time-to-close
    median describe how liquid the model is, i.e. whether a flip will actually move.
    """
    sql = MARKET_SQL
    params = []
    if category_key:
        sql += " AND category_key = %s"
        params.append(category_key)
    if q:
        sql += " AND model_key ILIKE %s"
        params.append(f"%{q}%")

    sql += """
        GROUP BY category_key, category_label, model_key
        HAVING COUNT(*) FILTER (WHERE status = 'active') > 0
            OR COUNT(*) FILTER (WHERE status = 'reserved') > 0
            OR COUNT(*) FILTER (WHERE status = 'gone' AND gone_at IS NOT NULL) > 0
    """

    rows = [dict(r) for r in conn.execute(sql, params)]

    for row in rows:
        # Share of the model's recent supply that actually cleared. Undefined until
        # something has happened either way, and noisy on tiny samples -- the UI
        # shows the raw counts next to it so a 1-of-2 doesn't read as a hot market.
        pool = row["closed_30d"] + row["count"]
        row["sell_through_30d"] = row["closed_30d"] / pool if pool else None
        row["sample_size"] = pool

    if order == "hot":
        rows.sort(key=lambda r: (r["sell_through_30d"] or 0, r["closed_30d"]), reverse=True)
    else:
        rows.sort(key=lambda r: r["count"], reverse=True)

    return rows


def get_market_stats(conn: psycopg.Connection):
    """Every model's turnover stats, keyed by (category_key, model_key)."""
    rows = get_model_summary(conn)
    return {(r["category_key"], r["model_key"]): r for r in rows}


def get_listings(conn: psycopg.Connection, category_key: str = None, model_key: str = None,
                  county: str = None, q: str = None, limit: int = 300):
    sql = "SELECT * FROM seen_ads WHERE price IS NOT NULL AND status = 'active'"
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


def get_similar_by_county(conn: psycopg.Connection, category_key: str, model_key: str,
                           exclude_ad_id: str = None):
    sql = """
        SELECT * FROM seen_ads
        WHERE category_key = %s AND model_key = %s AND price IS NOT NULL AND status = 'active'
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


def get_recent_closures(conn: psycopg.Connection, category_key: str = None, limit: int = 100):
    sql = """
        SELECT * FROM seen_ads
        WHERE status = 'gone' AND gone_at IS NOT NULL AND price IS NOT NULL
    """
    params = []
    if category_key:
        sql += " AND category_key = %s"
        params.append(category_key)
    sql += " ORDER BY gone_at DESC LIMIT %s"
    params.append(limit)
    return [dict(r) for r in conn.execute(sql, params)]
