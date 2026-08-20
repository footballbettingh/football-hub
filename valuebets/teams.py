"""Reconcile team names between football-data.org and The Odds API.

The two providers disagree, so a naive merge on team name silently drops
most rows — and a backtest over the survivors is quietly biased toward
whichever clubs happen to have matching names. Confirmed live:

    football-data.org          The Odds API
    AFC Bournemouth            Bournemouth
    Brighton & Hove Albion FC  Brighton and Hove Albion
    Wolverhampton Wanderers FC Wolverhampton Wanderers
    Sunderland AFC             Sunderland

Strategy, in order:
1. normalise (strip club-type affixes, accents, punctuation) and match exactly
2. fall back to close string matching, above a similarity threshold
3. report whatever is left UNMATCHED — never silently drop it

Rule of thumb: a fuzzy match you didn't look at is a data bug waiting to
happen. `build_mapping` returns the unmatched names so the caller can fail
loudly, and `FUZZY_THRESHOLD` is deliberately strict.
"""

import re
import unicodedata
from difflib import SequenceMatcher

# Tokens that describe the *kind* of club, not which club. Stripped from
# either end of the name.
AFFIXES = {
    "fc", "afc", "cf", "sc", "ac", "as", "ss", "ssc", "sv", "vfl", "vfb",
    "bsc", "fsv", "tsg", "rc", "cd", "ud", "sd", "club", "calcio", "1899",
    "1900", "1904", "1907", "09", "04", "05", "1846", "de", "futbol",
    "bc", "ca", "cp", "sad", "kv", "rcd", "us", "usl",
}

# Cases normalisation can't reach — genuinely irregular abbreviations, not the
# regular "Newcastle" / "Newcastle United" kind, which `resolve()` handles by
# structure. Both providers' forms map onto the same canonical value, so neither
# source is privileged.
#
# The short forms here are football-data.co.uk's; the values are the fuller
# names The Odds API and football-data.org use.
ALIASES = {
    "nott m forest": "nottingham forest",
    "m gladbach": "borussia monchengladbach",
    "ein frankfurt": "eintracht frankfurt",
    "ath bilbao": "athletic bilbao",
    "ath madrid": "atletico madrid",
    "atl madrid": "atletico madrid",
    "paris sg": "paris saint germain",
    "espanol": "espanyol",
    "qpr": "queens park rangers",
    "sheffield weds": "sheffield wednesday",
    "sheffield united": "sheffield united",
    "west brom": "west bromwich albion",
    "hamburg": "hamburger",
    "man united": "manchester united",
    "man city": "manchester city",
    "newcastle": "newcastle united",
    "leeds": "leeds united",
    "ipswich": "ipswich town",
    "tottenham": "tottenham hotspur",
    "wolves": "wolverhampton wanderers",
    "bayern munich": "bayern munich",
    "inter": "inter milan",
    "internazionale": "inter milan",
    "ath paranaense": "athletico paranaense",
    "brighton hove albion": "brighton and hove albion",
    "brighton": "brighton and hove albion",
    "man city": "manchester city",
    "man united": "manchester united",
    "spurs": "tottenham hotspur",
    "wolves": "wolverhampton wanderers",
    "nottingham": "nottingham forest",
    "internazionale": "inter milan",
    "inter": "inter milan",
    "psg": "paris saint germain",
    "paris saint germain fc": "paris saint germain",
    "bayern munchen": "bayern munich",
    "borussia monchengladbach": "borussia monchengladbach",
    "atletico de madrid": "atletico madrid",
    "athletic": "athletic bilbao",
    "athletic club": "athletic bilbao",
    "real betis balompie": "real betis",
    "rb leipzig": "rb leipzig",
    "1 fc koln": "fc koln",
    "koln": "fc koln",
}

FUZZY_THRESHOLD = 0.87


def normalize(name: str) -> str:
    """Fold a club name to a comparable key."""
    if not name:
        return ""
    # Strip accents: Atlético -> Atletico, Köln -> Koln
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()

    tokens = text.split()
    # Drop affix tokens from both ends — "AFC Bournemouth" and
    # "Sunderland AFC" both need it, from opposite sides.
    while len(tokens) > 1 and tokens[0] in AFFIXES:
        tokens.pop(0)
    while len(tokens) > 1 and tokens[-1] in AFFIXES:
        tokens.pop()

    key = " ".join(tokens)
    return ALIASES.get(key, key)


def build_mapping(source_names, target_names, fuzzy=True):
    """Map every source name onto a target name.

    Returns (mapping, unmatched, fuzzy_matches) where:
      mapping       dict source_name -> target_name
      unmatched     list of source names with no confident match
      fuzzy_matches list of (source, target, score) resolved by similarity —
                    review these before trusting a run.
    """
    target_by_key = {}
    for name in target_names:
        target_by_key.setdefault(normalize(name), name)

    mapping, unmatched, fuzzy_matches = {}, [], []

    for name in source_names:
        key = normalize(name)
        if key in target_by_key:
            mapping[name] = target_by_key[key]
            continue

        if not fuzzy or not target_by_key:
            unmatched.append(name)
            continue

        best_key, best_score = max(
            ((k, SequenceMatcher(None, key, k).ratio()) for k in target_by_key),
            key=lambda pair: pair[1],
        )
        if best_score >= FUZZY_THRESHOLD:
            mapping[name] = target_by_key[best_key]
            fuzzy_matches.append((name, target_by_key[best_key], round(best_score, 3)))
        else:
            unmatched.append(name)

    return mapping, unmatched, fuzzy_matches


def resolve(name, candidates):
    """Map a team name onto one of `candidates` (already-normalised keys).

    Providers differ mostly by how much of the full club name they keep:
    "Newcastle" vs "Newcastle United", "Celta" vs "Celta Vigo", "Betis" vs
    "Real Betis". Rather than enumerate those, match on token containment —
    but ONLY when exactly one candidate matches.

    The uniqueness rule is what makes this safe. "Manchester" is a subset of
    both "Manchester City" and "Manchester United", so it resolves to neither
    and is reported unknown. A wrong team silently substituted is far worse
    than a fixture skipped.

    Returns the matching candidate key, or None.
    """
    key = normalize(name)
    if not key:
        return None
    if key in candidates:
        return key

    tokens = set(key.split())
    # candidate is a shorter form of `name`  (odds "newcastle united" -> history "newcastle")
    shorter = [c for c in candidates if set(c.split()) < tokens]
    if len(shorter) == 1:
        return shorter[0]
    # candidate is a longer form of `name`   (odds "celta" -> history "celta vigo")
    longer = [c for c in candidates if set(c.split()) > tokens]
    if len(longer) == 1:
        return longer[0]
    return None


def build_resolver(candidates):
    """Cache `resolve` over a fixed candidate set (one competition's teams)."""
    candidates = set(candidates)
    cache = {}

    def lookup(name):
        if name not in cache:
            cache[name] = resolve(name, candidates)
        return cache[name]

    return lookup


def canonicalize(df, columns=("home_team", "away_team")):
    """Rewrite team columns in place to their normalised form.

    Canonicalising *both* datasets is more robust than mapping one onto the
    other: no direction to get backwards, and new teams need no new entries.
    """
    out = df.copy()
    for col in columns:
        if col in out.columns:
            out[col] = out[col].map(normalize)
    return out
