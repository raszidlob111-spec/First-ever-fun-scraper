import logging
import time

import requests

log = logging.getLogger("discord")

MAX_EMBEDS_PER_MESSAGE = 10  # Discord webhook limit
DELAY_BETWEEN_MESSAGES = 1.5  # seconds, stay under Discord's per-webhook rate limit

# Color scales with how good the deal is -- gold for a solid deal, escalating to
# purple for the standout ones.
COLOR_TIERS = [
    (40, 0x992D22),  # deep red/purple -- exceptional
    (25, 0xED4245),  # red -- great
    (15, 0xE67E22),  # orange -- good
    (0, 0xFEE75C),   # gold -- qualifies
]


def _color_for_discount(discount_pct: float) -> int:
    for cutoff, color in COLOR_TIERS:
        if discount_pct >= cutoff:
            return color
    return COLOR_TIERS[-1][1]


def _build_embed(listing: dict, median: float, discount_pct: float, profit: float) -> dict:
    embed = {
        "title": listing["title"][:256],
        "url": listing["url"],
        "color": _color_for_discount(discount_pct),
        "fields": [
            {"name": "Price", "value": f"{listing['price']:,} Ft", "inline": True},
            {"name": "Model median", "value": f"{median:,.0f} Ft", "inline": True},
            {"name": "Profit", "value": f"{profit:,.0f} Ft", "inline": True},
            {"name": "Below median", "value": f"{discount_pct}%", "inline": True},
            {"name": "Category", "value": listing.get("category_label") or "n/a", "inline": True},
            {"name": "Model", "value": listing["model_key"], "inline": True},
            {"name": "Posted", "value": listing.get("posted_display") or "n/a", "inline": True},
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


def _channel_for_profit(channels: list, profit: float) -> dict:
    """Pick the channel whose min_profit_huf is the highest threshold the
    profit still clears. `channels` should cover down to 0 so every profit
    matches something."""
    best = None
    for channel in channels:
        threshold = channel.get("min_profit_huf", 0)
        if profit >= threshold and (best is None or threshold > best.get("min_profit_huf", 0)):
            best = channel
    return best


def _send_batch(webhook_url: str, label: str, embeds: list, total: int) -> None:
    for i in range(0, len(embeds), MAX_EMBEDS_PER_MESSAGE):
        chunk = embeds[i : i + MAX_EMBEDS_PER_MESSAGE]
        payload = {"username": f"GPU Deal Watcher · {label}", "embeds": chunk}
        if i == 0:
            payload["content"] = f"Found **{total}** new underpriced listing(s):"

        try:
            _post_with_retry(webhook_url, payload)
        except requests.RequestException:
            log.exception("Failed to post Discord alert batch to %s (%d items)", label, len(chunk))

        if i + MAX_EMBEDS_PER_MESSAGE < len(embeds):
            time.sleep(DELAY_BETWEEN_MESSAGES)


def send_deal_alerts(channels: list, alerts: list) -> None:
    """Post a batch of underpriced listings to Discord, routed by profit
    (median - price) to whichever channel's min_profit_huf it clears.

    `channels` is a list of {label, webhook_url, min_profit_huf} dicts.
    `alerts` is a list of (listing, median, discount_pct) tuples, already in
    the desired display order -- that order is preserved within each channel.
    No-op if no channels are configured or the alert list is empty.
    """
    if not channels or not alerts:
        return

    by_channel = {}
    for listing, median, discount_pct in alerts:
        profit = median - listing["price"]
        channel = _channel_for_profit(channels, profit)
        if channel is None:
            log.warning("No Discord channel configured to cover profit=%.0f Ft, dropping alert", profit)
            continue
        by_channel.setdefault(channel["webhook_url"], (channel["label"], []))[1].append(
            _build_embed(listing, median, discount_pct, profit)
        )

    for webhook_url, (label, embeds) in by_channel.items():
        if not webhook_url:
            continue
        _send_batch(webhook_url, label, embeds, len(embeds))
