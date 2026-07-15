import re

# Best-effort settlement -> county (vármegye) lookup. Not exhaustive -- covers
# the towns most likely to show up as a seller's location on the marketplace.
# Szabolcs-Szatmár-Bereg gets the deepest coverage since it's the region of
# interest; other counties list their main towns.
COUNTY_CITIES = {
    "Bács-Kiskun": ["Kecskemét", "Baja", "Kiskunhalas", "Kiskunfélegyháza", "Kalocsa",
                    "Kiskőrös", "Jánoshalma", "Solt", "Kiskunmajsa", "Kunszentmiklós"],
    "Baranya": ["Pécs", "Komló", "Mohács", "Siklós", "Szigetvár", "Pécsvárad"],
    "Békés": ["Békéscsaba", "Gyula", "Orosháza", "Szarvas", "Békés",
              "Mezőkovácsháza", "Sarkad", "Gyomaendrőd"],
    "Borsod-Abaúj-Zemplén": ["Miskolc", "Kazincbarcika", "Ózd", "Sátoraljaújhely", "Szerencs",
                             "Tiszaújváros", "Mezőkövesd", "Encs", "Edelény", "Sárospatak",
                             "Tokaj", "Mezőcsát"],
    "Csongrád-Csanád": ["Szeged", "Hódmezővásárhely", "Szentes", "Makó", "Csongrád",
                        "Kistelek", "Mórahalom", "Szegvár"],
    "Fejér": ["Székesfehérvár", "Dunaújváros", "Bicske", "Mór", "Sárbogárd",
              "Gárdony", "Martonvásár"],
    "Győr-Moson-Sopron": ["Győr", "Sopron", "Mosonmagyaróvár", "Csorna", "Kapuvár",
                          "Fertőd", "Győrújbarát"],
    "Hajdú-Bihar": ["Debrecen", "Hajdúböszörmény", "Hajdúszoboszló", "Berettyóújfalu",
                    "Balmazújváros", "Nyíradony", "Püspökladány", "Hajdúnánás", "Derecske"],
    "Heves": ["Eger", "Gyöngyös", "Hatvan", "Füzesabony", "Heves", "Hevesaranyos"],
    "Jász-Nagykun-Szolnok": ["Szolnok", "Jászberény", "Karcag", "Törökszentmiklós",
                             "Mezőtúr", "Kunszentmárton", "Tiszaföldvár", "Jászapáti"],
    "Komárom-Esztergom": ["Tatabánya", "Esztergom", "Komárom", "Tata", "Kisbér", "Oroszlány"],
    "Nógrád": ["Salgótarján", "Balassagyarmat", "Bátonyterenye", "Pásztó", "Rétság", "Bakonybánk"],
    "Pest": ["Érd", "Szentendre", "Vác", "Gödöllő", "Cegléd", "Dunakeszi", "Szigetszentmiklós",
             "Nagykőrös", "Monor", "Ráckeve", "Vecsés", "Gyál", "Halásztelek", "Üröm", "Solymár",
             "Pilisjászfalu", "Nagykovácsi", "Dabas", "Aszód", "Nagykáta", "Veresegyház",
             "Szigethalom", "Pilisvörösvár", "Tököl"],
    "Somogy": ["Kaposvár", "Siófok", "Nagyatád", "Marcali", "Barcs", "Fonyód", "Balatonföldvár"],
    "Szabolcs-Szatmár-Bereg": ["Nyíregyháza", "Mátészalka", "Kisvárda", "Vásárosnamény",
                               "Csenger", "Fehérgyarmat", "Nyírbátor", "Ibrány", "Tiszavasvári",
                               "Kemecse", "Nagykálló", "Napkor", "Záhony", "Demecser", "Balkány",
                               "Nagyecsed", "Nyírtelek", "Sonkád", "Baktalórántháza", "Mándok",
                               "Vaja", "Nyírbogdány", "Nyíribrony", "Rakamaz", "Tiszalök"],
    "Tolna": ["Szekszárd", "Dombóvár", "Paks", "Bonyhád", "Tamási"],
    "Vas": ["Szombathely", "Sárvár", "Kőszeg", "Celldömölk", "Körmend"],
    "Veszprém": ["Veszprém", "Ajka", "Pápa", "Várpalota", "Balatonfüred", "Tapolca",
                 "Zirc", "Mezőnyárád"],
    "Zala": ["Zalaegerszeg", "Nagykanizsa", "Keszthely", "Lenti", "Letenye"],
}

BUDAPEST = "Budapest"
UNKNOWN = "Ismeretlen"

_ACCENTS = {"á": "a", "é": "e", "í": "i", "ó": "o", "ö": "o", "ő": "o", "ú": "u", "ü": "u", "ű": "u"}


def _fold(text: str) -> str:
    text = text.lower()
    for accented, plain in _ACCENTS.items():
        text = text.replace(accented, plain)
    return text


_CITY_TO_COUNTY = {
    _fold(city): county for county, cities in COUNTY_CITIES.items() for city in cities
}

_KERULET_RE = re.compile(r"kerulet", re.IGNORECASE)


def resolve_county(location: str) -> str:
    """Best-effort county for a (possibly multi-branch, comma-separated) location
    string. Returns the county of the first place name it can resolve."""
    if not location:
        return UNKNOWN

    for part in location.split(","):
        part = _fold(part.strip())
        if not part:
            continue
        if "budapest" in part or _KERULET_RE.search(part):
            return BUDAPEST
        if part in _CITY_TO_COUNTY:
            return _CITY_TO_COUNTY[part]
        for city_folded, county in _CITY_TO_COUNTY.items():
            if city_folded in part:
                return county

    return UNKNOWN


def all_counties():
    return sorted(set(COUNTY_CITIES.keys()) | {BUDAPEST})
