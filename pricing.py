import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

HU_TZ = ZoneInfo("Europe/Budapest")

MAX_AGE_YEARS = 9

MODEL_RE = re.compile(
    r"\b(RTX|GTX|GT|RX|ARC)\s?(\d{3,4})\s?(TI|SUPER|XTX|XT|GRE)?\b",
    re.IGNORECASE,
)

QUADRO_RE = re.compile(r"\bQUADRO\s?([A-Z]?\d{3,4})\b", re.IGNORECASE)

VRAM_RE = re.compile(r"\b(\d{1,2})\s?GB\b", re.IGNORECASE)

PRICE_RE = re.compile(r"([\d\s.,]+)\s*(M)?\s*Ft", re.IGNORECASE)

EXCLUDE_KEYWORDS = [
    "hibas", "serult", "alkatresznek",
    "bontott", "bontasra", "nem mukodik", "csak csere",
    "cserelnem", "elcserelnem", "csereajanlat",
    "ures doboz", "csak doboz", "dobozok",
]


def _fold(text: str) -> str:
    replacements = {
        "á": "a", "é": "e", "í": "i", "ó": "o",
        "ö": "o", "ő": "o", "ú": "u", "ü": "u", "ű": "u",
    }
    for accented, plain in replacements.items():
        text = text.replace(accented, plain)
    return text


def is_excluded(title: str) -> bool:
    t = _fold(title.lower())
    return any(kw in t for kw in EXCLUDE_KEYWORDS)


def estimate_release_year(prefix: str, num: int):
    """Rough release year for a GPU generation, based on common numbering conventions."""
    prefix = prefix.upper()

    if prefix == "GTX":
        if num < 1000:
            return 2014  # 900 series
        if num < 1600:
            return 2016  # 10 series: 1050/1060/1070/1080
        return 2019      # 16 series: 1650/1660

    if prefix == "RTX":
        if num < 3000:
            return 2018
        if num < 4000:
            return 2020
        if num < 5000:
            return 2022
        return 2025

    if prefix == "RX":
        if num < 500:
            return 2016  # RX 400 series
        if num < 1000:
            return 2017  # RX 500 series
        if num < 6000:
            return 2019  # RX 5000 series (4-digit)
        if num < 7000:
            return 2020  # RX 6000 series
        if num < 9000:
            return 2022  # RX 7000 series
        return 2025      # RX 9000 series

    if prefix == "ARC":
        return 2022

    return None


def is_too_old(prefix: str, num: str, current_year: int = None) -> bool:
    """GT-branded cards are always entry-level/old; everything else is checked against
    an estimated release year and a rolling max-age cutoff."""
    if prefix.upper() == "GT":
        return True

    year = estimate_release_year(prefix, int(num))
    if year is None:
        return False

    cutoff = (current_year or datetime.now().year) - MAX_AGE_YEARS
    return year < cutoff


def normalize_model(title: str):
    """Return a normalized 'model key' like 'RTX 3060 12GB', or None if no (recent enough) GPU model found."""
    t = title.upper()

    m = MODEL_RE.search(t)
    if m:
        prefix, num, suffix = m.group(1).upper(), m.group(2), (m.group(3) or "").upper()
        if is_too_old(prefix, num):
            return None
        key = f"{prefix} {num} {suffix}".strip()
    else:
        q = QUADRO_RE.search(t)
        if not q:
            return None
        key = f"QUADRO {q.group(1).upper()}"

    vm = VRAM_RE.search(t)
    if vm:
        key += f" {vm.group(1)}GB"

    return key


def parse_price(raw: str):
    """Return (price_int_or_None, is_reserved_bool) from a price cell's text."""
    if not raw:
        return None, False

    raw = raw.replace("\xa0", " ")
    reserved = "jegelve" in raw.lower()

    m = PRICE_RE.search(raw)
    if not m:
        return None, reserved

    num_str = m.group(1).strip()
    is_million = bool(m.group(2))

    if is_million:
        num_str = num_str.replace(" ", "").replace(",", ".")
        try:
            value = float(num_str) * 1_000_000
        except ValueError:
            return None, reserved
        return int(value), reserved

    num_str = num_str.replace(" ", "").replace(".", "").replace(",", "")
    if not num_str.isdigit():
        return None, reserved

    return int(num_str), reserved


def parse_posted_at(raw: str, now: datetime = None):
    """Parse the site's listing-time text ("ma HH:MM" / "tegnap HH:MM" / "YYYY-MM-DD")
    into (datetime_or_None, display_string). Falls back to (None, raw) for anything
    else (e.g. "Előresorolva" on boosted listings, which show no real timestamp)."""
    if not raw:
        return None, None

    raw = raw.strip()
    now = now or datetime.now(HU_TZ)
    lower = raw.lower()

    if lower.startswith("ma "):
        try:
            hh, mm = (int(x) for x in raw[3:].strip().split(":"))
        except ValueError:
            return None, raw
        dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        return dt, dt.strftime("%Y-%m-%d %H:%M")

    if lower.startswith("tegnap "):
        try:
            hh, mm = (int(x) for x in raw[7:].strip().split(":"))
        except ValueError:
            return None, raw
        dt = (now - timedelta(days=1)).replace(hour=hh, minute=mm, second=0, microsecond=0)
        return dt, dt.strftime("%Y-%m-%d %H:%M")

    try:
        dt = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=HU_TZ)
        return dt, dt.strftime("%Y-%m-%d")
    except ValueError:
        return None, raw
