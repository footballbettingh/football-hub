"""Readable league names, and which of them a price feed can reach.

Two separate questions that used to be tangled together:

* **What is this competition called?** `NAMES` answers it for all forty codes in
  the dataset, including the ones no odds feed covers. Display never depends on
  an API being reachable.
* **Can we get upcoming prices for it?** `SPORT_KEYS` is a best guess at The
  Odds API's key, and it is *never trusted blindly*: `discover` asks the API
  which leagues are actually in season (a free call) and reports what matched.
  A wrong key here costs a skipped league and a warning, not a wasted credit.

The card can only price a fixture whose competition also exists in history —
team strengths are ratios against a league average, so a Championship side
priced off Premier League parameters is not a worse forecast, it is a
meaningless one.
"""

# code -> (competition name, country)
NAMES = {
    # football-data.co.uk main section
    "PL": ("Premier League", "England"),
    "ELC": ("Championship", "England"),
    "EL1": ("League One", "England"),
    "EL2": ("League Two", "England"),
    "ENL": ("National League", "England"),
    "SPL": ("Premiership", "Scotland"),
    "SCH": ("Championship", "Scotland"),
    "SC2": ("League One", "Scotland"),
    "SC3": ("League Two", "Scotland"),
    "BL1": ("Bundesliga", "Germany"),
    "BL2": ("2. Bundesliga", "Germany"),
    "SA": ("Serie A", "Italy"),
    "SB": ("Serie B", "Italy"),
    "PD": ("La Liga", "Spain"),
    "SD": ("Segunda División", "Spain"),
    "FL1": ("Ligue 1", "France"),
    "FL2": ("Ligue 2", "France"),
    "DED": ("Eredivisie", "Netherlands"),
    "PPL": ("Primeira Liga", "Portugal"),
    "BJL": ("Pro League", "Belgium"),
    "TSL": ("Süper Lig", "Turkey"),
    "GSL": ("Super League", "Greece"),

    # football-data.co.uk extra section (codes are derived, names are not)
    "ARG-LIGAPROF": ("Liga Profesional", "Argentina"),
    "ARG-COPADELA": ("Copa de la Liga", "Argentina"),
    "AUT-BUNDESLI": ("Bundesliga", "Austria"),
    "BRA-SERIEA": ("Série A", "Brazil"),
    "CHN-SUPERLEA": ("Super League", "China"),
    "DNK-SUPERLIG": ("Superliga", "Denmark"),
    "FIN-VEIKKAUS": ("Veikkausliiga", "Finland"),
    "IRL-PREMIERD": ("Premier Division", "Ireland"),
    "JPN-J1LEAGUE": ("J1 League", "Japan"),
    "MEX-LIGAMX": ("Liga MX", "Mexico"),
    "NOR-ELITESER": ("Eliteserien", "Norway"),
    "POL-EKSTRAKL": ("Ekstraklasa", "Poland"),
    "ROU-SUPERLIG": ("SuperLiga", "Romania"),
    "RUS-PREMIERL": ("Premier League", "Russia"),
    "SWE-ALLSVENS": ("Allsvenskan", "Sweden"),
    "SWZ-SUPERLEA": ("Super League", "Switzerland"),
    "SWZ-CHALLENG": ("Challenge League", "Switzerland"),
    "USA-MLS": ("MLS", "USA"),
}

# code -> The Odds API sport key. Every entry below was checked against a live
# /sports listing on 12 Aug 2026 by comparing the API's own title with the name
# above — the first draft mapped ENL onto `soccer_england_efl_cup`, which is a
# real and in-season key for a completely different competition, and would have
# priced cup ties against National League team strengths without erroring once.
#
# Leagues in the dataset with no key here (National League, Scottish League One
# and Two, the Scottish Championship, Swiss and Irish top flights, Argentina's
# Copa de la Liga, Romania) simply cannot be priced. That is a gap, not a bug.
SPORT_KEYS = {
    "PL": "soccer_epl",
    "ELC": "soccer_efl_champ",
    "EL1": "soccer_england_league1",
    "EL2": "soccer_england_league2",
    "SPL": "soccer_spl",
    "BL1": "soccer_germany_bundesliga",
    "BL2": "soccer_germany_bundesliga2",
    "SA": "soccer_italy_serie_a",
    "SB": "soccer_italy_serie_b",
    "PD": "soccer_spain_la_liga",
    "SD": "soccer_spain_segunda_division",
    "FL1": "soccer_france_ligue_one",
    "FL2": "soccer_france_ligue_two",
    "DED": "soccer_netherlands_eredivisie",
    "PPL": "soccer_portugal_primeira_liga",
    "BJL": "soccer_belgium_first_div",
    "TSL": "soccer_turkey_super_league",
    "GSL": "soccer_greece_super_league",
    "ARG-LIGAPROF": "soccer_argentina_primera_division",
    "AUT-BUNDESLI": "soccer_austria_bundesliga",
    "BRA-SERIEA": "soccer_brazil_campeonato",
    "CHN-SUPERLEA": "soccer_china_superleague",
    "DNK-SUPERLIG": "soccer_denmark_superliga",
    "FIN-VEIKKAUS": "soccer_finland_veikkausliiga",
    "IRL-PREMIERD": "soccer_league_of_ireland",
    "JPN-J1LEAGUE": "soccer_japan_j_league",
    "MEX-LIGAMX": "soccer_mexico_ligamx",
    "NOR-ELITESER": "soccer_norway_eliteserien",
    "POL-EKSTRAKL": "soccer_poland_ekstraklasa",
    "RUS-PREMIERL": "soccer_russia_premier_league",
    "SWE-ALLSVENS": "soccer_sweden_allsvenskan",
    "SWZ-SUPERLEA": "soccer_switzerland_superleague",
    "USA-MLS": "soccer_usa_mls",
}

BY_SPORT = {sport: code for code, sport in SPORT_KEYS.items()}


def name(code, with_country=False):
    """'PL' -> 'Premier League', or the code itself if we have no name for it."""
    entry = NAMES.get(code)
    if not entry:
        return code
    label, country = entry
    # Six competitions share a name across countries — three "Super League",
    # two "Championship", two "Premier League". The country is what makes the
    # dropdown usable.
    return f"{label} ({country})" if with_country else label


def label(code):
    """Standalone label: name plus country wherever the name is shared.

    Used where there is no set to compare against — a log line, a CLI table.
    Prefer `labels_for` when rendering a list, because it only spends the extra
    words when they are actually needed.
    """
    entry = NAMES.get(code)
    if not entry:
        return code
    ambiguous = sum(1 for other in NAMES.values() if other[0] == entry[0]) > 1
    return name(code, with_country=ambiguous)


def labels_for(codes):
    """Labels disambiguated against THIS set, not against every league on earth.

    Six competitions share a name across countries, so the global rule turns
    the Premier League into "Premier League (England)" on a card that has never
    heard of the Russian one — three lines of wrapped text in a table column to
    resolve an ambiguity that is not present. Adding the country only when the
    set really contains a collision keeps the common case short.
    """
    codes = list(codes)
    plain = {}
    for code in codes:
        entry = NAMES.get(code)
        plain.setdefault(entry[0] if entry else code, []).append(code)
    return {code: name(code, with_country=len(plain[NAMES[code][0]]) > 1)
            if code in NAMES else code
            for code in codes}
