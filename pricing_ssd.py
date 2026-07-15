import re

SSD_INDICATOR_RE = re.compile(r"\bSSD\b|\bNVME\b|\bM\.?2\b", re.IGNORECASE)
NVME_RE = re.compile(r"\bNVME\b|\bM\.?2\b", re.IGNORECASE)
MSATA_RE = re.compile(r"\bMSATA\b", re.IGNORECASE)
SATA_RE = re.compile(r"\bSATA\b", re.IGNORECASE)
GEN_RE = re.compile(r"\b(?:GEN|PCIE)\s?([3-5])\b", re.IGNORECASE)
TB_RE = re.compile(r"\b(\d{1,2})\s?TB\b", re.IGNORECASE)
GB_RE = re.compile(r"\b(\d{2,5})\s?GB\b(?!/S)", re.IGNORECASE)  # exclude "12GB/s" transfer speeds


def normalize_model(title: str):
    """Return a normalized SSD key like 'NVME GEN4 1000GB'. Returns None for
    HDD-only listings (this category mixes SSD and HDD) or ones with no
    identifiable capacity."""
    t = title.upper()

    if not SSD_INDICATOR_RE.search(t):
        return None

    if NVME_RE.search(t):
        interface = "NVME"
    elif MSATA_RE.search(t):
        interface = "MSATA"
    elif SATA_RE.search(t):
        interface = "SATA"
    else:
        interface = "SSD"

    tb = TB_RE.search(t)
    if tb:
        capacity_gb = int(tb.group(1)) * 1000
    else:
        gb = GB_RE.search(t)
        if not gb:
            return None
        capacity_gb = int(gb.group(1))

    gen = GEN_RE.search(t)
    gen_part = f" GEN{gen.group(1)}" if gen else ""

    return f"{interface}{gen_part} {capacity_gb}GB"
