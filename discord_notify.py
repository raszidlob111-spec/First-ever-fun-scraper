import logging

import requests

log = logging.getLogger("discord")

EMBED_COLOR = 0x57F287  # green


def send_deal_alert(webhook_url: str, listing: dict, median: float, discount_pct: float) -> None:
    """Post an embed for one underpriced listing to a Discord webhook. No-op if no URL is set."""
    if not webhook_url:
        return

    embed = {
        "title": listing["title"][:256],
        "url": listing["url"],
        "color": EMBED_COLOR,
        "fields": [
            {"name": "Price", "value": f"{listing['price']:,} Ft", "inline": True},
            {"name": "Model median", "value": f"{median:,.0f} Ft", "inline": True},
            {"name": "Below median", "value": f"{discount_pct}%", "inline": True},
            {"name": "Model", "value": listing["model_key"], "inline": True},
            {"name": "Seller", "value": f"{listing.get('seller') or 'n/a'} ({listing.get('rating') or 'n/a'})", "inline": True},
            {"name": "Location", "value": listing.get("location") or "n/a", "inline": True},
        ],
    }
    if listing.get("image_url"):
        embed["thumbnail"] = {"url": listing["image_url"]}

    payload = {"username": "GPU Deal Watcher", "embeds": [embed]}

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
    except requests.RequestException:
        log.exception("Failed to post Discord alert for ad %s", listing.get("ad_id"))
