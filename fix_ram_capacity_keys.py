"""One-off fix for RAM listings mis-keyed by the old capacity-detection bug in
pricing_ram.py's _find_capacity_gb -- recomputes model_key for every stored RAM
row from its already-stored title, using the current (fixed) normalizer.

Title text never changes, so this is always losslessly re-derivable from what's
already in the table -- no re-scraping needed.

Run once, manually. Deliberately NOT wired into storage.init_db(): re-scanning
every RAM row on every process start would be wasteful once this has already
run. Safe to re-run later (e.g. after a future normalizer fix) -- it only
touches rows whose recomputed key actually differs from what's stored.

Usage:
    DATABASE_URL=postgresql://... python fix_ram_capacity_keys.py
"""
import logging

import pricing_ram
import storage

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("fix_ram_capacity_keys")


def main() -> None:
    conn = storage.connect()
    rows = conn.execute(
        "SELECT ad_id, title, model_key FROM seen_ads WHERE category_key = 'ram'"
    ).fetchall()

    changed = 0
    for row in rows:
        new_key = pricing_ram.normalize_model(row["title"])
        if new_key == row["model_key"]:
            continue
        conn.execute(
            "UPDATE seen_ads SET model_key = %s WHERE ad_id = %s",
            (new_key, row["ad_id"]),
        )
        log.info("%-24s -> %-24s  %s", row["model_key"], new_key, row["title"])
        changed += 1

    conn.commit()
    conn.close()
    log.info("Done: %d of %d RAM rows recomputed.", changed, len(rows))


if __name__ == "__main__":
    main()
