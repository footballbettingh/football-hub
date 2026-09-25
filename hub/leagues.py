"""Readable league names, and which of them a price feed can reach.

Two separate questions that used to be tangled together:

* **What is this competition called?** `NAMES` answers it for all forty codes in
  the dataset, including the ones no odds feed covers. Display never depends on
  an API being reachable.
* **Can we get upcoming prices for it?** `SPORT_KEYS` is a best guess at The
  Odds API's key, and it is *never trusted blindly*: `discover` asks the API
  which leagues are actually in season (a free call) and reports what matched.
  A wrong key here costs a skipped league and a warning, not a wasted credit.
* **Are its results still arriving?** `quiet` answers that, and it is the
  question the other two do not cover: a league can be priceable and in season
  and still have a results feed that has stopped, which makes every bet on it
  unsettleable.

The card can only price a fixture whose competition also exists in history —
team strengths are ratios against a league average, so a Championship side
priced off Premier League parameters is not a worse forecast, it is a
meaningless one.
"""

import pandas as pd

from confidence import data as cf_data

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


# How long a match that has been played may wait for its result before the
# league stops being priced.
#
# Not a guess. Every league in season sits between two and ten days behind,
# even through an international break, because the sources publish weekly.
# Russia's Premier League stopped publishing on 2 August 2026 and was 39 days
# behind by the time anyone noticed — while The Odds API went on listing it as
# in season and selling prices for it. Every bet taken on it in between could
# never be graded, and unlike a fixture that was abandoned it cannot even be
# recorded as having no result: a feed that says nothing is not evidence that
# nothing happened. So the only fix is to stop taking the bet.
#
# Measured from a match, not from the last result. It used to be three weeks
# since the newest result, which cannot tell a dead feed from a league that is
# not playing: every league reads as dead in mid-August, with its last result
# in May, so the first round of every season went unpriced — and so did the
# first round back from every winter break. 289 breaks of over three weeks in
# five seasons of history.
RESULTS_STALE_DAYS = 14

# Leagues kept on the card anyway, because you have taken on grading them
# yourself. A bet on one of these can only ever settle from a score typed into
# `data/manual_results.csv` after you have looked it up, and every row it
# grades that way is labelled as hand-entered on the History page.
#
# Empty by default, and it wants to stay a short list. Each entry is a standing
# promise to go and check results by hand for as long as the feed stays dead —
# and a bet nobody gets round to checking does not fail loudly, it just sits at
# pending for good, which is the exact hole all of this was built to close.
GRADED_BY_HAND: set[str] = set()


def quiet(history, today=None, listed=None):
    """Competitions whose results have stopped arriving.

    `history` is played matches — `confidence.data.load_history` output, where
    rows with no score are already gone, so the newest date is a match that
    happened rather than one that is merely scheduled. `listed` is every
    fixture the price feed has listed, kicked off or not — see
    `listed_fixtures`.

    A league is quiet when it has played and the results file has not heard:
    a match the price feed listed kicked off more than `RESULTS_STALE_DAYS`
    ago, and nothing in the results is from that day or later. Evidence, the
    way `ledger._answer_is_in` is evidence, rather than a count of days since
    the last result — a league between seasons, or in a winter break, has
    played nothing and so is owed nothing. With no listed fixtures there is no
    evidence either way, and nothing is quiet.

    The price of being wrong is a round of picks skipped for a feed that was
    merely very late, and the league comes back on its own the day results
    resume — which is the right way round, because the other kind of mistake
    does not heal.
    """
    if history is None or len(history) == 0 or listed is None or len(listed) == 0:
        return set()
    # Feed rows only. A result typed in by hand is a fact about one match, not
    # evidence that the source has started publishing again — counting it would
    # put the league straight back on the card on the strength of the very row
    # that was needed because the league is not covered.
    history = cf_data.from_feed(history)
    if len(history) == 0:
        return set()
    now = (pd.Timestamp(today) if today is not None
           else pd.Timestamp.now(tz="UTC").tz_localize(None))
    latest = history.groupby("competition")["date"].max()

    kickoff = _kickoffs(listed)
    played = listed.assign(kickoff=kickoff)[
        kickoff < now - pd.Timedelta(days=RESULTS_STALE_DAYS)]
    out = set()
    for code, block in played.groupby("competition"):
        last = latest.get(code)
        if last is None:
            continue                     # no history: never priced anyway
        # A day of slack, because the price feed dates a kick-off in UTC and
        # the results file in local time: an evening match in Buenos Aires is
        # tomorrow in UTC, and its result would otherwise look a day short.
        if (block["kickoff"].dt.normalize() - pd.Timedelta(days=1) > last).any():
            out.add(code)
    return out


def _kickoffs(listed):
    """When each listed fixture kicked off, in UTC with the zone dropped.

    The kick-off time where the feed gives one, the start of the day where it
    does not — which errs a few hours early, and only on fixtures that are
    already two weeks old.
    """
    day = pd.to_datetime(listed["date"], errors="coerce")
    if "commence_time" not in listed:
        return day
    exact = pd.to_datetime(listed["commence_time"], errors="coerce", utc=True)
    return exact.dt.tz_localize(None).fillna(day)


def listed_fixtures(source_dir=None):
    """Every fixture the price feed has listed, played ones included, or None.

    The price files are appended to and never pruned, which is what makes them
    evidence: a match they listed that has since kicked off is a match whose
    result is owed. None where there are no price files at all.
    """
    try:
        return cf_data.load_fixtures(source_dir, include_started=True)
    except SystemExit:
        return None


def skipped(history, today=None, listed=None):
    """The quiet leagues the card should actually drop.

    `quiet` states a fact about the feed; this applies the decision. A league
    you have opted to grade by hand is still quiet — nothing has started
    publishing again — but it stays on the card, and the results are on you.
    """
    return quiet(history, today, listed) - GRADED_BY_HAND
