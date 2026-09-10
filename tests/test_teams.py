"""Two providers' spellings landing on the same club.

Every case here was found the same way: a bet sat pending in the ledger for
weeks because the fixture had been priced under one spelling and played under
another, and nothing in the record said why. So the tests are written as pairs
— the price feed's name and the results file's name — and assert they meet.

The last group is the more important one. A resolver that matches everything is
worse than one that matches most things, because a fixture graded against the
wrong match is a result you will believe.
"""

import pytest

from confidence.teams import normalize, resolve


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
