"""Team-name keys, so two providers' spellings land on the same club.

Adapted from the sibling project's `valuebets/teams.py`. The rule that matters
is in `resolve`: a short form maps onto a longer one only when EXACTLY ONE
candidate matches, so "Manchester" resolves to neither City nor United and is
reported unknown instead. A fixture skipped is a nuisance; a fixture silently
priced with the wrong team's strengths is a bug you never notice.

Everything `normalize` folds — accents, affixes, abbreviations, aliases — is
applied to both providers, so a rule here can only ever change which names meet,
never which club a name means. That is why the tables below are safe to grow and
the uniqueness rule in `resolve` is not.
"""

import re
import unicodedata

AFFIXES = {
    "fc", "afc", "cf", "sc", "ac", "as", "ss", "ssc", "sv", "vfl", "vfb",
    "bsc", "fsv", "tsg", "rc", "cd", "ud", "sd", "club", "calcio", "1899",
    "1900", "1904", "1907", "09", "04", "05", "1846", "de", "futbol",
    "bc", "ca", "cp", "sad", "kv", "rcd", "us", "usl",
    # Left off the original list, and each one cost a club: "SK Rapid" never
    # reached "Rapid Wien", "Cercle Brugge KSV" was ambiguous between Cercle
    # and Club Brugge, and "Sarpsborg FK" never reached "Sarpsborg 08".
    "sk", "fk", "ksv",
}

# Letters NFKD will not take apart. A stroke through an L is not a combining
# mark, it is a different letter, so nothing decomposes and the character
# filter in `normalize` drops it: "Śląsk Wrocław" came out as "slask wroc aw"
# and matched nothing. Every Polish, Norwegian and Danish club carrying one was
# therefore invisible to the results file — priced at league average, and its
# bets left pending for good with nothing to say why.
TRANSLITERATE = str.maketrans({
    "ł": "l", "ø": "o", "đ": "d", "ð": "d", "þ": "th",
    "ı": "i", "ß": "ss", "æ": "ae", "œ": "oe",
})

# Abbreviations one provider expands and the other does not, folded token by
# token so a single entry reaches every club that uses one: "Atlanta Utd" and
# "Atlanta United", "Dep Riestra" and "Deportivo Riestra", "St Truiden",
# "Sint-Truiden" and "Union Saint-Gilloise". Checked against every team key in
# the results file first — none of these tokens means anything else in it.
TOKEN_ALIASES = {
    "utd": "united",
    "jrs": "juniors",
    "dep": "deportivo",
    "st": "saint",
    "sint": "saint",
    "dinamo": "dynamo",
}

ALIASES = {
    "nott m forest": "nottingham forest",
    "m gladbach": "borussia monchengladbach",
    "ein frankfurt": "eintracht frankfurt",
    "ath bilbao": "athletic bilbao",
    "ath madrid": "atletico madrid",
    "atl madrid": "atletico madrid",
    "paris sg": "paris saint germain",
    "psg": "paris saint germain",
    "espanol": "espanyol",
    "qpr": "queens park rangers",
    "sheffield weds": "sheffield wednesday",
    "west brom": "west bromwich albion",
    "hamburg": "hamburger",
    "man united": "manchester united",
    "man city": "manchester city",
    "spurs": "tottenham hotspur",
    "inter": "inter milan",
    "internazionale": "inter milan",
    "bayern munchen": "bayern munich",
    "athletic": "athletic bilbao",
    "athletic club": "athletic bilbao",
    "real betis balompie": "real betis",
    "1 fc koln": "fc koln",
    "koln": "fc koln",
    # Abbreviations the token rule cannot reach: "atl" is not a shorter form of
    # "atletico", it is a different string, so containment never matches.
    "atl tucuman": "atletico tucuman",
    "sp gijon": "sporting gijon",
    "atl san luis": "atletico san luis",

    # One club, two spellings, and nothing above can bridge them: an initialism
    # the results file never expands, a nickname only the price feed uses, a
    # name one side has not updated in a decade. Each of these was a fixture
    # priced at league average because the model had never heard of the team,
    # and a bet that stayed pending for good because no result could be found
    # for it. The direction is chosen so the containment rule in `resolve` can
    # still do its work: truncations and initialisms are expanded, so that a
    # feed sending the longer form matches too.
    "estudiantes": "estudiantes la plata",   # the feed spells Rio Cuarto out
    "estudiantes l p": "estudiantes la plata",
    "gimnasia l p": "gimnasia la plata",
    "peterboro": "peterborough",
    "bristol rvs": "bristol rovers",
    "for sittard": "fortuna sittard",
    "a lustenau": "austria lustenau",
    "austria wien": "austria vienna",
    "sp lisbon": "sporting lisbon",
    "vitoria sc": "guimaraes",               # not Vitoria of Brazil; see normalize
    "stade lavallois": "laval",
    "karlsruher": "karlsruhe",
    "tokyo verdy": "verdy",                  # "Tokyo" on its own is FC Tokyo
    "urawa red diamonds": "urawa reds",
    "djurgardens if": "djurgarden",
    "halmstads bk": "halmstad",
    "celta fortuna": "celta b",
    "atletico mineiro": "atletico mg",
    "atletico paranaense": "athletico pr",
    "levadiakos": "levadeiakos",
    "goztepe": "goztep",
    "amed": "amedspor",
    "erzurum bb": "erzurumspor",
    "basaksehir": "buyuksehyr",
    "shanghai sipg": "shanghai port",
    "akron tolyatti": "akron togliatti",
    "kryliya sovetov": "krylya sovetov",
    "d c united": "dc united",
    "wolves": "wolverhampton wanderers",
    "la galaxy": "los angeles galaxy",

    # Two that were not merely unmatched but matched WRONG, which is worse: the
    # club was priced as another club, never flagged as new, and its bets were
    # looked for among the other club's fixtures. The results file writes
    # "Ind. Rivadavia", so the feed's "Independiente Rivadavia" fell through to
    # the one candidate its name contains — Independiente of Avellaneda, a
    # different club in the same league. "Shenzhen Peng City" fell through to
    # "Shenzhen", a club dissolved in 2023; the results file knows the current
    # one by its pinyin, Xinpengcheng.
    "ind rivadavia": "independiente rivadavia",
    "shenzhen peng city": "shenzhen xinpengcheng",
}


def normalize(name) -> str:
    """Fold a club name to a comparable key."""
    if not name or name != name:  # NaN-safe
        return ""
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().translate(TRANSLITERATE).replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()

    tokens = [TOKEN_ALIASES.get(token, token) for token in text.split()]
    # Aliases are consulted before the affixes come off as well as after, for
    # the club whose only distinguishing token IS an affix: "Vitória SC" is
    # Guimarães, but strip the SC and what is left is "vitoria", which is a
    # different club in Brazil. Folding the stripped form would merge the two.
    full = " ".join(tokens)
    if full in ALIASES:
        return ALIASES[full]

    while len(tokens) > 1 and tokens[0] in AFFIXES:
        tokens.pop(0)
    while len(tokens) > 1 and tokens[-1] in AFFIXES:
        tokens.pop()
    return ALIASES.get(" ".join(tokens), " ".join(tokens))


def _explained_by(candidate, tokens):
    """Whether every word of `candidate` is a word of the name, or the start of one.

    "ind rivadavia" is explained by "Independiente Rivadavia": "ind" is how the
    results file shortens the first word and the second is there as it is.
    Three letters at least, so a stray initial cannot explain anything.
    """
    return all(any(_covers(word, token) for token in tokens)
               for word in candidate.split())


def _covers(word, token):
    return word == token or (len(word) >= 3 and token.startswith(word))


def resolve(name, candidates):
    """Map a name onto one of `candidates`, or None if it is ambiguous."""
    key = normalize(name)
    if not key:
        return None
    if key in candidates:
        return key

    tokens = set(key.split())
    # The same words in a different order. "Hiroshima Sanfrecce" and "Sanfrecce
    # Hiroshima" are one club, and neither containment test below can reach it
    # because neither set is the smaller one.
    reordered = [c for c in candidates if set(c.split()) == tokens]
    if len(reordered) == 1:
        return reordered[0]

    shorter = [c for c in candidates if set(c.split()) < tokens]
    if len(shorter) == 1:
        # A unique shorter name is an answer only if nothing else could be. The
        # word it leaves over is the clue: "Independiente Rivadavia" contains
        # "independiente", but "rivadavia" is left over, and another candidate
        # — "ind rivadavia" — carries it and is accounted for word by word by
        # the name. Either club could be meant, so neither is chosen.
        leftover = tokens - set(shorter[0].split())
        rivals = [c for c in candidates if c != shorter[0]
                  and any(_covers(word, token) for word in c.split()
                          for token in leftover)
                  and _explained_by(c, tokens)]
        return None if rivals else shorter[0]
    longer = [c for c in candidates if set(c.split()) > tokens]
    if len(longer) == 1:
        return longer[0]
    return None


def build_resolver(candidates, names=()):
    """Cache `resolve` over one competition's team set.

    `names`, where given, is every name about to be looked up together — one
    league's fixtures on one card. Two different names landing on the same
    club is the failure `resolve` cannot see from one name at a time, and it
    is almost never two spellings of one club: it is a second club being read
    as the first, which is exactly how Independiente Rivadavia became
    Independiente. So wherever it happens, a name that got there by a fuzzy
    match is left unresolved: priced at league average and flagged as new,
    which a reader can see, rather than priced as a club it is not, which
    nobody can. Two spellings that normalise to the club itself are one club
    and are left alone.
    """
    candidates = set(candidates)
    cache = {}

    def lookup(name):
        if name not in cache:
            cache[name] = resolve(name, candidates)
        return cache[name]

    claimed = {}
    for name in set(names):
        club = lookup(name)
        if club is not None:
            claimed.setdefault(club, set()).add(name)
    for club, claimants in claimed.items():
        if len(claimants) > 1:
            for name in claimants:
                if normalize(name) != club:
                    cache[name] = None
    return lookup
