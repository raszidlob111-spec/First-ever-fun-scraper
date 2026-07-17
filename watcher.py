import json
import logging
import statistics
import sys
import time
from datetime import datetime, timezone

import discord_notify
import pricing
import pricing_cpu
import pricing_ram
import pricing_ssd
import scraper
import storage

NORMALIZERS = {
    "gpu": pricing.normalize_model,
    "cpu": pricing_cpu.normalize_model,
    "ram": pricing_ram.normalize_model,
    "ssd": pricing_ssd.normalize_model,
}


def load_config(path: str = "config.json") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def setup_logging(log_path: str) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )


def run_cycle(conn, config: dict) -> None:
    log = logging.getLogger("watcher")
    cycle_start = datetime.now(timezone.utc)

    # A category with no scrape history is one we've never watched before, so every
    # ad in it was already posted when we arrived -- neither its age nor the closure
    # of anything missing from it can be dated. Decided before recording this run.
    bootstrap = {
        cat["key"] for cat in config["categories"]
        if not storage.has_scrape_history(conn, cat["key"])
    }

    raw_listings = []
    scrapes = {}
    for cat in config["categories"]:
        cat_listings, complete = scraper.fetch_all_listings(
            cat["url"],
            config["user_agent"],
            config["request_delay_seconds"],
            config["max_pages"],
        )
        for listing in cat_listings:
            listing["category_key"] = cat["key"]
            listing["category_label"] = cat["label"]
        scrapes[cat["key"]] = ({l["ad_id"] for l in cat_listings}, complete)
        log.info(
            "Scraped %d listings from %s%s",
            len(cat_listings), cat["label"], "" if complete else " (INCOMPLETE)",
        )
        raw_listings.extend(cat_listings)

    valid = []
    for listing in raw_listings:
        if pricing.is_excluded(listing["title"]):
            continue
        normalize_fn = NORMALIZERS[listing["category_key"]]
        model_key = normalize_fn(listing["title"])
        if not model_key:
            continue
        price, reserved = pricing.parse_price(listing["price_text"])
        if price is None or reserved:
            continue
        posted_at, posted_display = pricing.parse_posted_at(listing.get("posted_raw"))
        listing["model_key"] = model_key
        listing["price"] = price
        listing["posted_at"] = posted_at
        listing["posted_display"] = posted_display
        valid.append(listing)

    by_group = {}
    for listing in valid:
        group_key = (listing["category_key"], listing["model_key"])
        by_group.setdefault(group_key, []).append(listing["price"])

    medians = {
        group: statistics.median(prices)
        for group, prices in by_group.items()
        if len(prices) >= config["min_sample_size"]
    }

    threshold = config["discount_threshold"]
    flagged = []
    for listing in valid:
        group_key = (listing["category_key"], listing["model_key"])
        median = medians.get(group_key)
        if median is None:
            continue
        if listing["price"] <= median * (1 - threshold):
            if not storage.is_alerted(conn, listing["ad_id"]):
                flagged.append((listing, median))

    # Oldest first, newest last -- on Discord the last embed in a message sits at
    # the bottom, i.e. closest to the reader's eye, so the newest finds end up there.
    def _posted_sort_key(entry):
        dt = entry[0].get("posted_at")
        return (0, datetime.min) if dt is None else (1, dt.replace(tzinfo=None))

    flagged.sort(key=_posted_sort_key)

    storage.upsert_seen(conn, valid, bootstrap_categories=bootstrap)

    # Close out ads the site no longer lists, then bank this run as the baseline the
    # next cycle checks itself against. Compared against every id we scraped (not just
    # the priced ones) so a reserved or unparseable ad isn't mistaken for a sale.
    closed_total = 0
    for cat in config["categories"]:
        present, complete = scrapes[cat["key"]]
        closed = storage.mark_gone(
            conn, cat["key"], present, cycle_start, complete,
            backfill=cat["key"] in bootstrap,
        )
        if closed and cat["key"] in bootstrap:
            log.info("Backfilled %d already-closed %s ads (undated)", closed, cat["label"])
        elif closed:
            log.info("Closed %d %s ads no longer listed", closed, cat["label"])
        closed_total += closed
        storage.record_scrape_run(conn, cat["key"], cycle_start, len(present), complete)

    market = storage.get_market_stats(conn)

    alerts = []
    for listing, median in flagged:
        discount_pct = round((1 - listing["price"] / median) * 100, 1)
        profit = median - listing["price"]
        stats = market.get((listing["category_key"], listing["model_key"]))
        log.info(
            "UNDERPRICED [%s/%s] %s -- %s Ft (median %s Ft, profit %s Ft, %s%% below) -- posted=%s seller=%s rating=%s loc=%s -- %s",
            listing["category_label"],
            listing["model_key"],
            listing["title"],
            f"{listing['price']:,}",
            f"{median:,.0f}",
            f"{profit:,.0f}",
            discount_pct,
            listing.get("posted_display"),
            listing.get("seller"),
            listing.get("rating"),
            listing.get("location"),
            listing["url"],
        )
        alerts.append((listing, median, discount_pct, stats))
        storage.mark_alerted(conn, listing)

    discord_notify.send_deal_alerts(config.get("discord_channels"), alerts)

    # Reads leave the connection "idle in transaction", and this one is about to sleep
    # for the whole interval -- release the snapshot so it doesn't pin locks and stall
    # vacuum (or a deploy's ALTER TABLE) while it waits.
    conn.rollback()

    log.info(
        "Cycle done: %d scraped, %d priced listings, %d models with enough samples, "
        "%d new deals flagged, %d ads closed.",
        len(raw_listings), len(valid), len(medians), len(flagged), closed_total,
    )


def run_once(config: dict) -> int:
    """One cycle, then exit -- the shape a Railway cron job needs.

    Railway expects a cron service to "terminate as soon as that task is finished,
    leaving no open resources", and to close database connections explicitly, so the
    connection is closed here rather than left to interpreter shutdown. Returns an
    exit code: a failed cycle exits non-zero so the run shows up as failed rather
    than silently succeeding. There's no retry -- the next scheduled run is the
    retry, which also keeps a failing cycle from hammering the site.
    """
    log = logging.getLogger("watcher")
    conn = storage.init_db()
    try:
        run_cycle(conn, config)
        return 0
    except Exception:
        log.exception("Cycle failed")
        return 1
    finally:
        conn.close()


def run_forever(config: dict) -> None:
    conn = storage.init_db()
    log = logging.getLogger("watcher")
    log.info("interval=%s min", config["interval_minutes"])
    while True:
        try:
            run_cycle(conn, config)
        except Exception:
            log.exception("Cycle failed")
        time.sleep(config["interval_minutes"] * 60)


def main() -> None:
    config = load_config()
    setup_logging(config["log_path"])
    log = logging.getLogger("watcher")

    labels = ", ".join(c["label"] for c in config["categories"])
    once = "--once" in sys.argv
    log.info("Starting price watcher [%s] (%s, threshold=%s%%)",
              labels, "single cycle" if once else "continuous",
              config["discount_threshold"] * 100)

    if once:
        sys.exit(run_once(config))
    run_forever(config)


if __name__ == "__main__":
    main()
