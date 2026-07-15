import re

INTEL_ULTRA_RE = re.compile(r"\bULTRA\s?([3579])\s?(\d{3})([A-Z]{0,2})?\b", re.IGNORECASE)
INTEL_I_RE = re.compile(r"\bI([3579])[\s-]?(\d{4,5})([A-Z]{0,3})?\b", re.IGNORECASE)
RYZEN_RE = re.compile(r"\bRYZEN\s?(?:[3579]\s+)?(\d{3,4})(X3D|XT|X|GE|G|U|H|HX)?\b", re.IGNORECASE)


def normalize_model(title: str):
    """Return a normalized CPU model key like 'I5 12400F' or 'RYZEN 5600X'.

    Requires exactly one distinct model match -- titles with no recognizable
    model, or with several different models mentioned (bundle/lot listings),
    return None so they don't get compared against a single median.
    """
    t = title.upper()

    matches = set()
    for m in INTEL_ULTRA_RE.finditer(t):
        matches.add(f"ULTRA {m.group(1)} {m.group(2)}{m.group(3) or ''}".strip())
    for m in INTEL_I_RE.finditer(t):
        matches.add(f"I{m.group(1)} {m.group(2)}{m.group(3) or ''}".strip())
    for m in RYZEN_RE.finditer(t):
        matches.add(f"RYZEN {m.group(1)}{m.group(2) or ''}".strip())

    if len(matches) != 1:
        return None

    return matches.pop()
