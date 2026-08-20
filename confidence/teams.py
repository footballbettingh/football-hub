"""Team-name keys, so two providers' spellings land on the same club.

Adapted from the sibling project's `valuebets/teams.py`. The rule that matters
is in `resolve`: a short form maps onto a longer one only when EXACTLY ONE
candidate matches, so "Manchester" resolves to neither City nor United and is
reported unknown instead. A fixture skipped is a nuisance; a fixture silently
priced with the wrong team's strengths is a bug you never notice.
"""

import re
import unicodedata

AFFIXES = {
    "fc", "afc", "cf", "sc", "ac", "as", "ss", "ssc", "sv", "vfl", "vfb",
    "bsc", "fsv", "tsg", "rc", "cd", "ud", "sd", "club", "calcio", "1899",
    "1900", "1904", "1907", "09", "04", "05", "1846", "de", "futbol",
    "bc", "ca", "cp", "sad", "kv", "rcd", "us", "usl",
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
}


def normalize(name) -> str:
    """Fold a club name to a comparable key."""
    if not name or name != name:  # NaN-safe
        return ""
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()

    tokens = text.split()
    while len(tokens) > 1 and tokens[0] in AFFIXES:
        tokens.pop(0)
    while len(tokens) > 1 and tokens[-1] in AFFIXES:
        tokens.pop()
    return ALIASES.get(" ".join(tokens), " ".join(tokens))


def resolve(name, candidates):
    """Map a name onto one of `candidates`, or None if it is ambiguous."""
    key = normalize(name)
    if not key:
        return None
    if key in candidates:
        return key

    tokens = set(key.split())
    shorter = [c for c in candidates if set(c.split()) < tokens]
    if len(shorter) == 1:
        return shorter[0]
    longer = [c for c in candidates if set(c.split()) > tokens]
    if len(longer) == 1:
        return longer[0]
    return None


def build_resolver(candidates):
    """Cache `resolve` over one competition's team set."""
    candidates = set(candidates)
    cache = {}

    def lookup(name):
        if name not in cache:
            cache[name] = resolve(name, candidates)
        return cache[name]

    return lookup
