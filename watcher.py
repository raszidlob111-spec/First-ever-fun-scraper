import json
import logging
import statistics
import sys
import time

import discord_notify
import pricing
import scraper
import storage


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

    raw_listings = scraper.fetch_all_listings(
        config["category_url"],
        config["user_agent"],
        config["request_delay_seconds"],
        config["max_pages"],
    )

    valid = []
    for listing in raw_listings:
        if pricing.is_excluded(listing["title"]):
            continue
        model_key = pricing.normalize_model(listing["title"])
        if not model_key:
            continue
        price, reserved = pricing.parse_price(listing["price_text"])
        if price is None or reserved:
            continue
        listing["model_key"] = model_key
        listing["price"] = price
        valid.append(listing)

    by_model = {}
    for listing in valid:
        by_model.setdefault(listing["model_key"], []).append(listing["price"])

    medians = {
        model: statistics.median(prices)
        for model, prices in by_model.items()
        if len(prices) >= config["min_sample_size"]
    }

    threshold = config["discount_threshold"]
    flagged = []
    for listing in valid:
        median = medians.get(listing["model_key"])
        if median is None:
            continue
        if listing["price"] <= median * (1 - threshold):
            if not storage.is_alerted(conn, listing["ad_id"]):
                flagged.append((listing, median))

    alerts = []
    for listing, median in flagged:
        discount_pct = round((1 - listing["price"] / median) * 100, 1)
        log.info(
            "UNDERPRICED [%s] %s -- %s Ft (median %s Ft, %s%% below) -- seller=%s rating=%s loc=%s -- %s",
            listing["model_key"],
            listing["title"],
            f"{listing['price']:,}",
            f"{median:,.0f}",
            discount_pct,
            listing.get("seller"),
            listing.get("rating"),
            listing.get("location"),
            listing["url"],
        )
        alerts.append((listing, median, discount_pct))
        storage.mark_alerted(conn, listing)

    discord_notify.send_deal_alerts(config.get("discord_webhook_url"), alerts)

    storage.upsert_seen(conn, valid)

    log.info(
        "Cycle done: %d scraped, %d priced GPU listings, %d models with enough samples, %d new deals flagged.",
        len(raw_listings), len(valid), len(medians), len(flagged),
    )


def main() -> None:
    config = load_config()
    setup_logging(config["log_path"])
    conn = storage.init_db(config["db_path"])
    log = logging.getLogger("watcher")

    log.info("Starting GPU price watcher (interval=%s min, threshold=%s%%)",
              config["interval_minutes"], config["discount_threshold"] * 100)

    while True:
        try:
            run_cycle(conn, config)
        except Exception:
            log.exception("Cycle failed")
        time.sleep(config["interval_minutes"] * 60)


if __name__ == "__main__":
    main()
