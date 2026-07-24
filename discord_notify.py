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


def _liquidity_value(stats: dict) -> str:
    """One line on how readily this model moves, so a fat margin on something that
    never sells is visibly different from a fat margin on something that does.

    hardverapro archives sold, withdrawn and expired ads identically, so these are
    closures rather than confirmed sales -- "closed" counts everything, "confirmed"
    is the subset that passed through the site's own "jegelve" (reserved) marker
    first, i.e. actually had a buyer lined up."""
    if not stats or not stats.get("closed_30d"):
        return "no closures tracked yet"

    parts = [f"{stats['closed_30d']} closed/30d"]
    confirmed = stats.get("confirmed_closed_30d")
    if confirmed:
        parts.append(f"{confirmed} confirmed")
    days = stats.get("median_days_to_close")
    if days is not None:
        # Sub-day is the hottest signal there is -- "~0.1 days" would bury it.
        parts.append(f"~{days * 24:.0f}h to close" if days < 1 else f"~{days:.1f} days to close")
    share = stats.get("sell_through_30d")
    if share is not None:
        parts.append(f"{share * 100:.0f}% of supply cleared")
    return " · ".join(parts)


def _build_embed(listing: dict, median: float, discount_pct: float, profit: float,
                  stats: dict = None, basis: str = "asking price") -> dict:
    embed = {
        "title": listing["title"][:256],
        "url": listing["url"],
        "color": _color_for_discount(discount_pct),
        "fields": [
            {"name": "Price", "value": f"{listing['price']:,} Ft", "inline": True},
            {"name": f"Low-tier median ({basis})", "value": f"{median:,.0f} Ft", "inline": True},
            {"name": "Profit", "value": f"{profit:,.0f} Ft", "inline": True},
            {"name": "Below median", "value": f"{discount_pct}%", "inline": True},
            {"name": "Category", "value": listing.get("category_label") or "n/a", "inline": True},
            {"name": "Model", "value": listing["model_key"], "inline": True},
            {"name": "Brand", "value": listing.get("manufacturer") or "n/a", "inline": True},
            # Not inline: gets its own full-width row, mid-embed where it's read.
            {"name": "Liquidity", "value": _liquidity_value(stats), "inline": False},
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


def _channel_for_discount_pct(channels: list, discount_pct: float) -> dict:
    """Pick the channel whose min_discount_pct is the highest threshold the
    discount still clears. `channels` should cover down to 0 so every alert
    matches something.

    Percentage below reference, not absolute Ft profit, is the split: it lines
    up with the color tiers (>=25% is the red/dark-red band a trader flip can
    actually trust; below that is the noisier gold/orange band), whereas profit
    alone conflated a small cheap item with a thin margin and a big expensive
    item with a thin margin.

    Naming note, since this bit us once: "cheap" in the channel names (config.json's
    "Moonbag"/"Pennies") refers to cheap/small *profit*, not a cheap *price* -- the
    high-threshold channel (>=25%, the good tier) is "Moonbag", the low one
    (10-25%, small margin, not worth the bother) is "Pennies". Higher
    min_discount_pct always means the better tier, regardless of what a channel
    happens to be called.
    """
    best = None
    for channel in channels:
        threshold = channel.get("min_discount_pct", 0)
        if discount_pct >= threshold and (best is None or threshold > best.get("min_discount_pct", 0)):
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
    """Post a batch of underpriced listings to Discord, routed by discount
    percentage (below the reference price) to whichever channel's
    min_discount_pct it clears.

    `channels` is a list of {label, webhook_url, min_discount_pct} dicts.
    `alerts` is a list of (listing, median, discount_pct, market_stats, basis)
    tuples, already in the desired display order -- that order is preserved
    within each channel. `market_stats` may be None for a model with no turnover
    history yet. `basis` names what `median` was computed from (e.g. "confirmed
    sales", "closed listings", "asking price"). No-op if no channels are
    configured or the alert list is empty.
    """
    if not channels or not alerts:
        return

    by_channel = {}
    for listing, median, discount_pct, stats, basis in alerts:
        profit = median - listing["price"]
        channel = _channel_for_discount_pct(channels, discount_pct)
        if channel is None:
            log.warning("No Discord channel configured to cover discount_pct=%.1f, dropping alert", discount_pct)
            continue
        by_channel.setdefault(channel["webhook_url"], (channel["label"], []))[1].append(
            _build_embed(listing, median, discount_pct, profit, stats, basis)
        )

    for webhook_url, (label, embeds) in by_channel.items():
        if not webhook_url:
            continue
        _send_batch(webhook_url, label, embeds, len(embeds))
