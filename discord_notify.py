import logging
import time

import requests

log = logging.getLogger("discord")

EMBED_COLOR = 0x57F287  # green
MAX_EMBEDS_PER_MESSAGE = 10  # Discord webhook limit
DELAY_BETWEEN_MESSAGES = 1.5  # seconds, stay under Discord's per-webhook rate limit


def _build_embed(listing: dict, median: float, discount_pct: float) -> dict:
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
    return embed


def _post_with_retry(webhook_url: str, payload: dict) -> None:
    resp = requests.post(webhook_url, json=payload, timeout=10)
    if resp.status_code == 429:
        retry_after = 1.0
        try:
            retry_after = float(resp.json().get("retry_after", 1.0))
        except ValueError:
            pass
        log.warning("Discord rate limited, retrying after %.1fs", retry_after)
        time.sleep(retry_after + 0.1)
        resp = requests.post(webhook_url, json=payload, timeout=10)
    resp.raise_for_status()


def send_deal_alerts(webhook_url: str, alerts: list) -> None:
    """Post a batch of underpriced listings to a Discord webhook.

    `alerts` is a list of (listing, median, discount_pct) tuples. Up to
    MAX_EMBEDS_PER_MESSAGE are packed into a single message to stay within
    Discord's per-webhook rate limit; no-op if no URL is set or list is empty.
    """
    if not webhook_url or not alerts:
        return

    for i in range(0, len(alerts), MAX_EMBEDS_PER_MESSAGE):
        chunk = alerts[i : i + MAX_EMBEDS_PER_MESSAGE]
        embeds = [_build_embed(listing, median, discount_pct) for listing, median, discount_pct in chunk]
        payload = {"username": "GPU Deal Watcher", "embeds": embeds}
        if i == 0:
            payload["content"] = f"Found **{len(alerts)}** new underpriced GPU listing(s):"

        try:
            _post_with_retry(webhook_url, payload)
        except requests.RequestException:
            log.exception("Failed to post Discord alert batch (%d items)", len(chunk))

        if i + MAX_EMBEDS_PER_MESSAGE < len(alerts):
            time.sleep(DELAY_BETWEEN_MESSAGES)
