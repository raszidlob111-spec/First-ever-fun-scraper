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


def _min_profit_for_price(tiers: list, price: float):
    """Minimum profit required at this price, from a list of
    {max_price, min_profit_huf} tiers ordered ascending by max_price -- the
    last tier's max_price should be null/omitted to mean "no upper bound".
    Returns None if `tiers` is empty (no floor configured, i.e. every profit
    clears)."""
    if not tiers:
        return None
    for tier in tiers:
        max_price = tier.get("max_price")
        if max_price is None or price <= max_price:
            return tier["min_profit_huf"]
    return tiers[-1]["min_profit_huf"]


def _channel_for_discount_pct(channels: list, discount_pct: float, profit: float, price: float) -> dict:
    """Pick the channel whose min_discount_pct is the highest threshold the
    discount still clears, among channels whose optional min_profit_tiers is
    also cleared for this price. `channels` should cover down to 0 with no
    profit tiers so every alert matches something.

    Percentage below reference is the primary split: it lines up with the color
    tiers (>=25% is the red/dark-red band a trader flip can actually trust;
    below that is the noisier gold/orange band). But a fixed percentage alone
    conflates a small cheap item with a thin margin and a big expensive item
    with the same thin margin -- what's actually "worth bothering with" turned
    out not to be a fixed percentage OR a fixed Ft amount, but a Ft floor that
    steps up with price (roughly: parcel delivery covers cheap cards cheaply,
    so the floor is low up to ~230k; above that it's in-person only, and the
    floor climbs with price up to a plateau around 50k for anything over 900k).
    min_profit_tiers (currently only set on the high tier) encodes that step
    table directly rather than approximating it with one number.

    Deliberately does NOT account for bringing several cards on one trip (the
    per-card bar drops when a trip is already happening for another reason) --
    that depends on knowing what else is active at alert time, which isn't
    something a single listing's evaluation can see.

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
        min_profit = _min_profit_for_price(channel.get("min_profit_tiers", []), price)
        if min_profit is not None and profit < min_profit:
            continue
        if discount_pct >= threshold and (best is None or threshold > best.get("min_discount_pct", 0)):
            best = channel
    return best


def _send_batch(webhook_url: str, label: str, items: list) -> list:
    """Posts (listing, embed) pairs in chunks under Discord's per-message embed
    limit. Returns the `listing`s whose chunk actually posted successfully --
    a chunk that fails stays out of the returned list entirely, since a caller
    that marks something "alerted" without knowing the send failed would lose
    it forever (is_alerted() would skip it on every future cycle even though
    the notification never arrived)."""
    total = len(items)
    sent = []
    for i in range(0, total, MAX_EMBEDS_PER_MESSAGE):
        chunk = items[i : i + MAX_EMBEDS_PER_MESSAGE]
        payload = {
            "username": f"GPU Deal Watcher · {label}",
            "embeds": [embed for _listing, embed in chunk],
        }
        if i == 0:
            payload["content"] = f"Found **{total}** new underpriced listing(s):"

        try:
            _post_with_retry(webhook_url, payload)
            sent.extend(listing for listing, _embed in chunk)
        except requests.RequestException:
            log.exception("Failed to post Discord alert batch to %s (%d items) -- will retry next cycle",
                           label, len(chunk))

        if i + MAX_EMBEDS_PER_MESSAGE < total:
            time.sleep(DELAY_BETWEEN_MESSAGES)
    return sent


def send_deal_alerts(channels: list, alerts: list) -> list:
    """Post a batch of underpriced listings to Discord, routed by discount
    percentage (below the reference price) to whichever channel's
    min_discount_pct it clears.

    `channels` is a list of {label, webhook_url, min_discount_pct, min_profit_pct}
    dicts (min_profit_pct optional). `alerts` is a list of (listing, median,
    discount_pct, market_stats, basis) tuples, already in the desired display
    order -- that order is preserved within each channel. `market_stats` may be
    None for a model with no turnover history yet. `basis` names what `median`
    was computed from (e.g. "confirmed sales", "closed listings", "asking
    price"). No-op if no channels are configured or the alert list is empty.

    Returns the `listing`s that were actually successfully posted -- callers
    must only mark these as alerted (storage.mark_alerted), not the full input
    list, so a failed Discord send doesn't get treated as a delivered one and
    silently dropped from ever being retried.
    """
    if not channels or not alerts:
        return []

    by_channel = {}
    for listing, median, discount_pct, stats, basis in alerts:
        profit = median - listing["price"]
        channel = _channel_for_discount_pct(channels, discount_pct, profit, listing["price"])
        if channel is None:
            log.warning("No Discord channel configured to cover discount_pct=%.1f, dropping alert", discount_pct)
            continue
        embed = _build_embed(listing, median, discount_pct, profit, stats, basis)
        by_channel.setdefault(channel["webhook_url"], (channel["label"], []))[1].append((listing, embed))

    sent = []
    for webhook_url, (label, items) in by_channel.items():
        if not webhook_url:
            continue
        sent.extend(_send_batch(webhook_url, label, items))
    return sent


WANTED_MATCH_COLOR = 0x5865F2  # Discord blurple -- distinct from the price-deal color tiers,
                                # since this isn't a discount signal, it's a demand/supply match.


def _build_wanted_embed(match: dict) -> dict:
    wanted = match["wanted"]
    listings = match["listings"]
    lines = [
        f"[{l['title'][:80]}]({l['url']}) -- {l['price']:,} Ft ({l.get('location') or 'n/a'})"
        for l in listings
    ]
    return {
        "title": wanted["title"][:256],
        "url": wanted["url"],
        "color": WANTED_MATCH_COLOR,
        "fields": [
            {"name": "Category", "value": wanted.get("category_label") or "n/a", "inline": True},
            {"name": "Model", "value": wanted.get("model_key") or "n/a", "inline": True},
            {"name": "Seeker", "value": f"{wanted.get('seller') or 'n/a'} ({wanted.get('rating') or 'n/a'})", "inline": True},
            {"name": f"Matching listing(s) -- {len(listings)}", "value": "\n".join(lines) or "n/a", "inline": False},
        ],
    }


def send_wanted_matches(channel: dict, matches: list) -> dict:
    """Post wanted-ad/available-listing matches to Discord.

    `channel` is a single {label, webhook_url} dict -- no discount-tier routing
    here, this isn't a price signal, just one destination for all matches.
    `matches` is the list of {wanted, listings} dicts from
    storage.find_new_wanted_matches().

    Returns {wanted_ad_id: [sell_ad_id, ...]} for matches that actually posted
    successfully -- mirrors send_deal_alerts: callers must only mark these as
    notified (storage.mark_wanted_matched), not the full input, so a failed
    send stays eligible to retry next cycle rather than being silently
    dropped forever.
    """
    if not channel or not channel.get("webhook_url") or not matches:
        return {}

    webhook_url = channel["webhook_url"]
    label = channel.get("label", "Wanted Match")
    items = [(m, _build_wanted_embed(m)) for m in matches]

    sent = {}
    total = len(items)
    for i in range(0, total, MAX_EMBEDS_PER_MESSAGE):
        chunk = items[i : i + MAX_EMBEDS_PER_MESSAGE]
        payload = {
            "username": f"GPU Deal Watcher · {label}",
            "embeds": [embed for _match, embed in chunk],
        }
        if i == 0:
            payload["content"] = f"Found **{total}** wanted-ad match(es):"

        try:
            _post_with_retry(webhook_url, payload)
            for match, _embed in chunk:
                sent[match["wanted"]["ad_id"]] = [l["ad_id"] for l in match["listings"]]
        except requests.RequestException:
            log.exception("Failed to post wanted-match batch to %s (%d items) -- will retry next cycle",
                           label, len(chunk))

        if i + MAX_EMBEDS_PER_MESSAGE < total:
            time.sleep(DELAY_BETWEEN_MESSAGES)
    return sent
