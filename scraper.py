import time
import logging

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("scraper")


def fetch_all_listings(category_url: str, user_agent: str, delay_seconds: float, max_pages: int):
    """Paginate through the category (index.html?offset=N).

    Returns (listings, complete). `complete` is True only when we reached the real
    end of the category. Callers infer sold/withdrawn ads from what's *missing*
    between cycles, so a run cut short by a failed fetch or by the max_pages ceiling
    must not be mistaken for "the rest of the market disappeared".
    """
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})

    listings = []
    seen_ids_this_cycle = set()
    offset = 0
    for page_num in range(max_pages):
        url = category_url if offset == 0 else f"{category_url}?offset={offset}"
        try:
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
        except requests.RequestException:
            log.exception("Failed to fetch %s", url)
            return listings, False

        cards = parse_listing_cards(resp.text)
        if not cards:
            return listings, True

        new_on_page = 0
        for card in cards:
            if card["ad_id"] in seen_ids_this_cycle:
                continue
            seen_ids_this_cycle.add(card["ad_id"])
            listings.append(card)
            new_on_page += 1

        # A page with nothing new (e.g. only the pinned/boosted ads repeating) means we've
        # reached the end of the real listings.
        if new_on_page == 0 and page_num > 0:
            return listings, True

        offset += 100
        time.sleep(delay_seconds)

    # Ran out of pages before running out of listings -- the tail is unseen.
    log.warning("Hit max_pages=%d on %s; listing tail not scraped", max_pages, category_url)
    return listings, False


def parse_listing_cards(html: str):
    soup = BeautifulSoup(html, "lxml")
    results = []

    for li in soup.select("li.media[data-uadid]"):
        ad_id = li.get("data-uadid")
        title_a = li.select_one(".uad-col-title h1 a")
        if not title_a:
            continue
        title = title_a.get_text(strip=True)
        url = title_a.get("href", "")

        price_div = li.select_one(".uad-col-price .uad-price")
        price_text = price_div.get_text(" ", strip=True) if price_div else ""

        seller_a = li.select_one(".uad-user-text a")
        seller = seller_a.get_text(strip=True) if seller_a else None

        rating_span = li.select_one(".uad-rating")
        rating = rating_span.get_text(strip=True) if rating_span else None

        city_div = li.select_one(".uad-cities")
        location = city_div.get_text(strip=True) if city_div else None

        time_div = li.select_one(".uad-col-info .uad-time")
        posted_raw = time_div.get_text(" ", strip=True) if time_div else None

        img_tag = li.select_one(".uad-col-image img")
        image_url = img_tag.get("src") if img_tag else None
        if image_url and image_url.startswith("//"):
            image_url = "https:" + image_url

        results.append(
            {
                "ad_id": ad_id,
                "title": title,
                "url": url,
                "price_text": price_text,
                "seller": seller,
                "rating": rating,
                "location": location,
                "image_url": image_url,
                "posted_raw": posted_raw,
            }
        )

    return results
