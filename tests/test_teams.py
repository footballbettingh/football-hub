"""Two providers' spellings landing on the same club.

Every case here was found the same way: a bet sat pending in the ledger for
weeks because the fixture had been priced under one spelling and played under
another, and nothing in the record said why. So the tests are written as pairs
— the price feed's name and the results file's name — and assert they meet.

The last group is the more important one. A resolver that matches everything is
worse than one that matches most things, because a fixture graded against the
wrong match is a result you will believe.
"""

import pandas as pd
import pytest

from confidence.teams import build_resolver, normalize, resolve
from hub import ledger


@pytest.mark.parametrize("priced, played", [
    # Letters NFKD leaves alone. Without the transliteration these lost the
    # letter entirely: "wroc aw", "wis a p ock", "bod glimt".
    ("Śląsk Wrocław", "Slask Wroclaw"),
    ("Wisła Płock", "Wisla Plock"),
    ("Widzew Łódź", "Widzew Lodz"),
    ("Bodø/Glimt", "Bodo Glimt"),
    # Abbreviations folded token by token.
    ("Atlanta United", "Atlanta Utd"),
    ("Argentinos Juniors", "Argentinos Jrs"),
    ("Deportivo Riestra", "Dep Riestra"),
    ("Sint-Truiden", "St Truiden"),
    ("Saint Etienne", "St Etienne"),
    ("Dinamo Moscow", "Dynamo Moscow"),
    # Spelled out one side, initialled the other.
    ("Gimnasia La Plata", "Gimnasia L.P."),
    ("Fortuna Sittard", "For Sittard"),
    ("Sporting Lisbon", "Sp Lisbon"),
    # Nicknames, former names, and one club that changed its name in 2014.
    ("Celta Fortuna", "Celta B"),
    ("Shanghai SIPG FC", "Shanghai Port"),
    ("Basaksehir", "Buyuksehyr"),
    ("Urawa Red Diamonds", "Urawa Reds"),
    # Two that were worse than unmatched: each landed on a DIFFERENT club.
    ("Independiente Rivadavia", "Ind. Rivadavia"),
    ("Shenzhen Peng City FC", "Shenzhen Xinpengcheng"),
])
def test_the_two_feeds_meet(priced, played):
    assert normalize(priced) == normalize(played)


def test_a_truncation_is_expanded_so_containment_can_still_reach_it():
    """The results file writes "Peterboro". Expanding that to the full name,
    rather than folding the feed's name down to it, is what lets "Peterborough
    United" and a bare "Peterborough" both land on the same club."""
    assert normalize("Peterboro") == "peterborough"
    assert resolve("Peterborough United", {"peterborough"}) == "peterborough"
    assert resolve("Peterborough", {"peterborough"}) == "peterborough"


def test_affixes_that_were_missing_from_the_list():
    assert normalize("Rapid Wien") == "rapid wien"
    assert resolve("Rapid Wien", {"rapid", "salzburg"}) == "rapid"
    assert resolve("Cercle Brugge KSV", {"cercle brugge", "brugge"}) == "cercle brugge"
    assert resolve("Sarpsborg FK", {"sarpsborg 08", "brann"}) == "sarpsborg 08"


def test_word_order_is_not_a_different_club():
    assert resolve("Hiroshima Sanfrecce FC", {"sanfrecce hiroshima"}) == "sanfrecce hiroshima"


def test_an_affix_can_be_the_distinguishing_token():
    """Vitória SC is Guimarães. Strip the SC and what is left is Vitória,
    which is a different club in Brazil — so the alias has to be read before
    the affixes come off, and must not follow the stripped name."""
    assert normalize("Vitória SC") == normalize("Guimaraes")
    assert normalize("Vitoria") != normalize("Guimaraes")


def test_ambiguity_still_refuses_to_guess():
    assert resolve("Manchester", {"manchester city", "manchester united"}) is None
    # "Tokyo" is FC Tokyo; Tokyo Verdy is a second club in the same league, so
    # the containment rule would have had two answers.
    assert resolve("Tokyo Verdy", {"tokyo", "verdy"}) == "verdy"
    # The feed names Río Cuarto in full, so a bare "Estudiantes" is La Plata.
    candidates = {"estudiantes la plata", "estudiantes rio cuarto"}
    assert resolve("Estudiantes", candidates) == "estudiantes la plata"
    assert resolve("Estudiantes de Río Cuarto", candidates) == "estudiantes rio cuarto"


def test_a_wrong_letter_does_not_become_a_wrong_club():
    """The transliteration must fold letters, not drop words. Dropping is what
    turned Wisła Kraków and Wisła Płock into names that shared a token with
    everything and matched nothing."""
    assert normalize("Wisła Kraków") == "wisla krakow"
    assert normalize("Wisła Płock") == "wisla plock"
    assert resolve("Wisła Płock", {"wisla krakow", "wisla plock"}) == "wisla plock"


# -- a name containing another club's name ---------------------------------

def test_a_name_that_contains_another_club_is_not_that_club():
    """"Independiente Rivadavia" contains "independiente", which is a second
    club in the same league — the results file calls Rivadavia "Ind.
    Rivadavia". The word the short match leaves over, "rivadavia", belongs to
    another candidate the name accounts for word by word, so either could be
    meant and neither is chosen. Written with the raw keys, so it holds without
    the alias that fixes this particular pair."""
    assert resolve("Independiente Rivadavia",
                   {"independiente", "ind rivadavia"}) is None


def test_a_common_word_left_over_does_not_block_a_match():
    """The rival has to be explained by the name, not merely share a word with
    it. Manchester City shares "city" with Leicester City and is not a reading
    of it; the same goes for West Bromwich Albion and Brighton."""
    assert resolve("Leicester City", {"leicester", "manchester city"}) == "leicester"
    assert resolve("Brighton and Hove Albion",
                   {"brighton", "west bromwich albion"}) == "brighton"


def test_two_names_on_one_card_cannot_become_one_club():
    """The failure one name at a time cannot show. With only Independiente
    among the candidates, Rivadavia's name falls through to it — but the card
    carries both names, and two names landing on one club is a second club
    being read as the first. The fuzzy one is left unresolved: priced at league
    average and flagged new, which a reader can see."""
    lookup = build_resolver({"independiente", "talleres cordoba"},
                            names={"Independiente", "Independiente Rivadavia"})
    assert lookup("Independiente") == "independiente"
    assert lookup("Independiente Rivadavia") is None


def test_two_spellings_of_one_club_are_still_one_club():
    lookup = build_resolver({"atlanta united"}, names={"Atlanta United", "Atlanta Utd"})
    assert lookup("Atlanta United") == lookup("Atlanta Utd") == "atlanta united"


def test_a_bet_is_not_graded_against_the_other_clubs_match():
    """The danger in a wrong match is not a bet left pending, it is a bet
    graded. Rivadavia's bet was looked for among Independiente's fixtures, and
    Independiente hosting the same opponent that week would have settled it on
    a match it had nothing to do with — a result nobody could tell was wrong."""
    history = pd.DataFrame({
        "date": pd.to_datetime(["2026-08-22", "2026-08-15"]),
        "competition": ["ARG", "ARG"],
        "home": ["independiente", "ind rivadavia"],
        "away": ["talleres cordoba", "lanus"],
        "home_goals": [1, 2], "away_goals": [0, 2],
    })
    bet = {"competition": "ARG", "home": "independiente rivadavia",
           "away": "talleres cordoba", "day": "2026-08-22"}
    assert ledger._result_for(bet, history) is None
