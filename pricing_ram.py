import re

DDR_RE = re.compile(r"\bDDR\s?(\d)L?\b", re.IGNORECASE)
KIT_CAPACITY_RE = re.compile(r"\b(\d)\s?[xX]\s?(\d{1,3})\s?GB\b", re.IGNORECASE)
SINGLE_CAPACITY_RE = re.compile(r"\b(\d{1,3})\s?GB\b", re.IGNORECASE)
SPEED_RE = re.compile(r"\b(\d{3,5})\s?MHZ\b", re.IGNORECASE)
LAPTOP_RE = re.compile(r"SODIMM|NOTEBOOK|LAPTOP", re.IGNORECASE)


def _find_capacity_gb(t: str):
    """Leftmost capacity mention wins: a stated total ("32GB (2x16GB)") comes
    before its kit breakdown, while a kit-only mention ("2x8GB ...") has
    nothing earlier to lose to."""
    candidates = []
    for m in KIT_CAPACITY_RE.finditer(t):
        candidates.append((m.start(), int(m.group(1)) * int(m.group(2))))
    for m in SINGLE_CAPACITY_RE.finditer(t):
        candidates.append((m.start(), int(m.group(1))))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]


def normalize_model(title: str):
    """Return a normalized RAM key like 'DDR4 16GB 3200MHz' (SO- prefixed for
    laptop/SODIMM modules so they aren't compared against desktop DIMM prices).

    For a genuine multi-kit lot listing (several different capacities offered
    together) this can still misattribute price to the wrong capacity -- an
    accepted tradeoff for keeping this a simple regex instead of a real title
    parser.
    """
    t = title.upper()

    ddr = DDR_RE.search(t)
    if not ddr:
        return None

    capacity_gb = _find_capacity_gb(t)
    if capacity_gb is None:
        return None

    key = f"DDR{ddr.group(1)} {capacity_gb}GB"

    speed = SPEED_RE.search(t)
    if speed:
        key += f" {speed.group(1)}MHz"

    if LAPTOP_RE.search(t):
        key = "SO-" + key

    return key
