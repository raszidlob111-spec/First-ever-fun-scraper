import re

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


def normalize_model(title: str):
    """Return a normalized 'model key' like 'RTX 3060 12GB', or None if no GPU model found."""
    t = title.upper()

    m = MODEL_RE.search(t)
    if m:
        prefix, num, suffix = m.group(1).upper(), m.group(2), (m.group(3) or "").upper()
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
