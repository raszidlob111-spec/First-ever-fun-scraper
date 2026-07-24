import re

DDR_RE = re.compile(r"\bDDR\s?(\d)L?\b", re.IGNORECASE)
KIT_CAPACITY_RE = re.compile(r"\b(\d)\s?[xX]\s?(\d{1,3})\s?GB\b", re.IGNORECASE)
KIT_LABEL_RE = re.compile(r"\b(\d{1,3})\s?GB\s?KIT\b", re.IGNORECASE)
SINGLE_CAPACITY_RE = re.compile(r"\b(\d{1,3})\s?GB\b", re.IGNORECASE)
SPEED_RE = re.compile(r"\b(\d{3,5})\s?MHZ\b", re.IGNORECASE)
# "SO-?DIMM" (optional hyphen) since "so-dimm" is a common real-world spelling
# that a bare "SODIMM" literal misses -- confirmed against a real listing
# ("Transcend DDR4 so-dimm 3200 8GB ram") that was silently priced against
# desktop RAM instead of other laptop sticks before this.
LAPTOP_RE = re.compile(r"SO-?DIMM|NOTEBOOK|LAPTOP", re.IGNORECASE)

# Recognizable gaming/heatsink product lines. Unlike GPUs, RAM price doesn't split
# cleanly by parent brand -- Kingston, Corsair, G.Skill etc. all trade in the same
# range once branded -- what actually separates the market is whether it's a
# known line at all (see the DDR4 16GB 3200MHz sample: no-name/OEM clustered
# 12-22k Ft, any recognizable line clustered 20-40k Ft regardless of which one).
# Bucketed by line name rather than parent brand since some lines span brand
# transitions (Kingston acquired HyperX and still sells "Fury"/"Predator"-branded
# parts under both names for the same physical product).
KNOWN_LINE_RE = re.compile(
    r"\b(FURY|VENGEANCE|DOMINATOR|BALLISTIX|PREDATOR|TRIDENT(?:\s?Z)?|RIPJAWS|AEGIS|SNIPER(?:\s?X)?|XPOWER|RENEGADE|T-?FORCE)\b",
    re.IGNORECASE,
)

# A shop offering several different capacities in one listing ("8/ 16/ 32GB") has
# one price covering the cheapest of them, not the one _find_capacity_gb would
# pick -- comparing that price against the priciest capacity's median invents a
# huge fake discount. No way to recover which capacity the price actually means,
# so these get excluded rather than guessed at. Anchored with \b before the first
# digit so it can't start matching mid-token -- without it, "DDR4 / 32 GB" reads
# the trailing "4" off of "DDR4" as if it were a second capacity option ("4GB or
# 32GB"), falsely excluding an otherwise unambiguous single-capacity listing.
# Only catches the bare-number-chain phrasing ("8/16/32GB") -- see
# _has_slash_separated_capacities for the "each option restates GB" phrasing
# ("32GB DDR5 / 16GB DDR5 / 8GB DDR5") this doesn't match at all.
MULTI_CAPACITY_RE = re.compile(r"\b(?:\d{1,3}\s*/\s*){1,}\d{1,3}\s?GB", re.IGNORECASE)


def _has_slash_separated_capacities(t: str) -> bool:
    """True if the title lists 2+ distinct capacities as slash-separated
    alternatives even when each one restates its own "GB" and other words sit
    between them ("32GB DDR5 / 16GB DDR5 / 8GB DDR5") -- MULTI_CAPACITY_RE alone
    only catches the bare-number-chain phrasing ("8/16/32GB"), since here every
    number already has its own GB suffix with no bare digit/slash chain to
    match. A "/" between two *different* capacity values is the signal; the
    same value repeated (a total restated with its own breakdown) isn't."""
    matches = list(SINGLE_CAPACITY_RE.finditer(t))
    for a, b in zip(matches, matches[1:]):
        if a.group(1) == b.group(1):
            continue
        if "/" in t[a.end():b.start()]:
            return True
    return False


def _find_capacity_gb(t: str):
    """A kit spec ("2x16GB") or an explicit "NNGB KIT" label states the total
    capacity deliberately and wins regardless of where it sits in the title --
    a title can casually mention a single stick's capacity ("16GB") before its
    real kit breakdown shows up later ("... (2x16GB 32GB)"), which used to fool
    a leftmost-wins heuristic into picking the wrong number. Multiple kit specs
    that disagree on capacity (a genuine multi-variant lot) return None rather
    than guessing. Falls back to the leftmost bare "NNGB" mention only when no
    kit spec exists at all."""
    kit_caps = {int(m.group(1)) * int(m.group(2)) for m in KIT_CAPACITY_RE.finditer(t)}
    if kit_caps:
        return kit_caps.pop() if len(kit_caps) == 1 else None

    labeled = KIT_LABEL_RE.search(t)
    if labeled:
        return int(labeled.group(1))

    candidates = [(m.start(), int(m.group(1))) for m in SINGLE_CAPACITY_RE.finditer(t)]
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]


def normalize_model(title: str):
    """Return a normalized RAM key like 'DDR4 16GB 3200MHz' (SO- prefixed for
    laptop/SODIMM modules so they aren't compared against desktop DIMM prices).

    Returns None for a multi-capacity lot listing ("8/16/32GB") -- see
    MULTI_CAPACITY_RE -- since the single listed price can't be reliably
    attributed to just one of the offered capacities.
    """
    t = title.upper()

    ddr = DDR_RE.search(t)
    if not ddr:
        return None

    if MULTI_CAPACITY_RE.search(t) or _has_slash_separated_capacities(t):
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


def detect_manufacturer(title: str):
    """Return the recognizable product line (e.g. 'FURY', 'VENGEANCE'), or None
    if the title reads as generic/OEM/no-name RAM -- that split, not parent
    brand, is what the closed-sale data actually separates on."""
    m = KNOWN_LINE_RE.search(title.upper())
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1).upper()).strip()
