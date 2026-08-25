"""The web layer: link modes, the control strip, the job runner, the pages.

The failures worth catching here are the quiet ones — a static export whose
links point at server routes and 404 on GitHub Pages, a job that dies without
saying so, a page that renders an empty table instead of admitting the data is
missing.
"""

import json
import time
from datetime import datetime

import pandas as pd
import pytest

from hub import (artifacts, card, components as c, export, leagues, ledger,
                 pages)


# -- league names ----------------------------------------------------------

def test_codes_become_names():
    assert leagues.label("SA") == "Serie A"
    assert leagues.label("PD") == "La Liga"
    assert leagues.label("ELC").startswith("Championship")


def test_names_shared_by_two_countries_carry_the_country():
    """Three leagues are called "Super League" and two "Championship". Without
    the country the dropdown has duplicate entries that pick different data."""
    assert leagues.label("PL") == "Premier League (England)"
    assert leagues.label("RUS-PREMIERL") == "Premier League (Russia)"
    assert leagues.label("ELC") == "Championship (England)"
    assert leagues.label("SCH") == "Championship (Scotland)"


def test_a_set_without_a_collision_keeps_the_short_name():
    """Disambiguating against every league on earth turns "Premier League" into
    three wrapped lines in a table column, to resolve an ambiguity that is not
    on the page."""
    labels = leagues.labels_for(["PL", "SA", "PD"])
    assert labels["PL"] == "Premier League"


def test_a_set_with_a_collision_gets_both_countries():
    labels = leagues.labels_for(["PL", "RUS-PREMIERL", "SA"])
    assert labels["PL"] == "Premier League (England)"
    assert labels["RUS-PREMIERL"] == "Premier League (Russia)"
    assert labels["SA"] == "Serie A"


def test_labels_for_passes_unknown_codes_through():
    assert leagues.labels_for(["PL", "ZZZ"])["ZZZ"] == "ZZZ"


def test_an_unknown_code_is_left_alone():
    """A new competition appearing in the data must show as its code, not as
    a blank cell or a crash."""
    assert leagues.label("XYZ-NEWLEAGUE") == "XYZ-NEWLEAGUE"


def test_every_sport_key_maps_back_to_exactly_one_competition():
    assert len(leagues.BY_SPORT) == len(leagues.SPORT_KEYS)


def test_every_mapped_league_has_a_name():
    assert not set(leagues.SPORT_KEYS) - set(leagues.NAMES)


def test_no_league_is_mapped_onto_a_cup():
    """The first draft pointed the National League at `soccer_england_efl_cup`
    — a live key for a different competition, which would have priced cup ties
    against National League strengths and never raised a thing."""
    cups = ("cup", "pokal", "libertadores", "sudamericana", "qualification",
            "nations_league", "leagues_cup")
    for code, sport in leagues.SPORT_KEYS.items():
        assert not any(word in sport for word in cups), f"{code} -> {sport}"


# -- links: the one thing both delivery modes share ------------------------

def test_server_links_are_routes_and_static_links_are_files():
    server, static = c.Links("server"), c.Links("static")
    assert server.href("index") == "/"
    assert server.href("fixtures") == "/fixtures"
    assert server.asset("style.css").startswith("/assets/style.css?v=")

    assert static.href("index") == "index.html"
    assert static.href("fixtures") == "fixtures.html"
    # No query string: a static export may be opened over file://.
    assert static.asset("style.css") == "assets/style.css"


def test_served_assets_are_versioned_by_their_own_timestamp(tmp_path, monkeypatch):
    """Otherwise an edited stylesheet stays cached for an hour and the page
    looks unchanged — which reads as a broken edit, not a cached one."""
    stylesheet = tmp_path / "style.css"
    stylesheet.write_text("body{}")
    monkeypatch.setattr(c, "STATIC_DIR", tmp_path)

    first = c.Links("server").asset("style.css")
    import os
    os.utime(stylesheet, (0, 1_700_000_000))
    assert c.Links("server").asset("style.css") != first
    assert c.Links("server").asset("style.css").endswith("?v=1700000000")


def test_only_the_server_is_interactive():
    assert c.Links("server").interactive is True
    assert c.Links("static").interactive is False


def test_unknown_link_mode_is_refused():
    with pytest.raises(ValueError):
        c.Links("github")


# -- layout ----------------------------------------------------------------

def test_layout_escapes_titles_and_embeds_data():
    html = c.layout(c.Links("static"), "Card <script>", "index", "<p>body</p>",
                    page_data={"rows": [1, 2]})
    assert "&lt;script&gt;" in html
    assert '"rows": [1, 2]' in html
    assert "assets/style.css" in html


def test_embedded_data_cannot_close_the_script_block():
    """A match named `</script>` would otherwise end the block early and spill
    the rest of the payload into the document as markup."""
    html = c.layout(c.Links("static"), "t", "index", "",
                    page_data={"match": "</script><h1>x</h1>"})
    assert "</script><h1>" not in html
    assert "<\\/script>" in html


def test_current_page_is_marked_for_screen_readers():
    html = c.layout(c.Links("server"), "Card", "fixtures", "")
    assert '<a href="/fixtures" aria-current="page">Fixtures</a>' in html


# -- the control strip -----------------------------------------------------

def _fake_status(stale=False, missing=False):
    return [{"key": "picks", "label": "The card", "job": "refresh-picks", "note": "",
             "exists": not missing, "built": "01 Jan 10:00", "age": "2 h ago",
             "stale_after": ["Upcoming fixture prices"] if stale else []}]


def test_an_artifact_older_than_what_it_was_built_from_is_stale(tmp_path):
    """The real mistake this catches: fetch new odds, forget to re-price, then
    read yesterday's card as if it were today's."""
    source = tmp_path / "odds.csv"
    derived = tmp_path / "picks.json"
    derived.write_text("{}")
    time.sleep(0.01)
    source.write_text("x")          # written AFTER the thing derived from it

    items = (artifacts.Artifact("odds", "Prices", source, "fetch-odds"),
             artifacts.Artifact("picks", "The card", derived, "refresh-picks",
                                ("odds",)))
    rows = {row["key"]: row for row in artifacts.status(items)}
    assert rows["picks"]["stale_after"] == ["Prices"]
    assert rows["odds"]["stale_after"] == []


def test_a_glob_artifact_takes_the_freshest_file(tmp_path, monkeypatch):
    """The odds are one file per league. Naming a single one would turn the
    chip red the day that league went out of season."""
    monkeypatch.setattr(artifacts, "DATA_DIR", tmp_path)
    (tmp_path / "odds_a.csv").write_text("x")
    (tmp_path / "odds_b.csv").write_text("x")

    row = artifacts.status((artifacts.Artifact("odds", "Prices", "odds_*.csv",
                                               "fetch-odds"),))[0]
    assert row["exists"] is True
    assert row["age"] == "just now"


def test_a_glob_that_matches_nothing_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "DATA_DIR", tmp_path)
    row = artifacts.status((artifacts.Artifact("odds", "Prices", "odds_*.csv",
                                               "fetch-odds"),))[0]
    assert row["exists"] is False


def test_a_missing_artifact_is_reported_as_missing_not_stale(tmp_path):
    items = (artifacts.Artifact("picks", "The card", tmp_path / "nope.json",
                                "refresh-picks"),)
    row = artifacts.status(items)[0]
    assert row["exists"] is False
    assert row["age"] == "never"


# -- the job runner --------------------------------------------------------

def _wait(runner, timeout=5.0):
    deadline = time.time() + timeout
    while runner.busy() and time.time() < deadline:
        time.sleep(0.01)
    assert not runner.busy(), "job did not finish"


# -- the card payload ------------------------------------------------------

def test_the_price_fetch_follows_the_discovered_plan(tmp_path, monkeypatch):
    """Otherwise pressing "Fetch new prices" after adding twenty leagues would
    silently refresh only the six that already had a file."""
    from hub import pipeline
    plan = tmp_path / "leagues.json"
    plan.write_text(json.dumps([{"code": "SA", "sport": "soccer_italy_serie_a",
                                 "name": "Serie A", "tracked": False}]))
    monkeypatch.setattr(pipeline, "LEAGUE_PLAN", plan)
    assert pipeline.league_plan() == ["soccer_italy_serie_a"]


def test_without_a_plan_the_fetch_falls_back_to_what_exists(tmp_path, monkeypatch):
    from hub import pipeline
    monkeypatch.setattr(pipeline, "LEAGUE_PLAN", tmp_path / "absent.json")
    monkeypatch.setattr(pipeline, "sports_tracked", lambda: ["soccer_epl"])
    assert pipeline.league_plan() == ["soccer_epl"]


def test_a_corrupt_plan_does_not_stop_a_fetch(tmp_path, monkeypatch):
    from hub import pipeline
    plan = tmp_path / "leagues.json"
    plan.write_text("{ not json")
    monkeypatch.setattr(pipeline, "LEAGUE_PLAN", plan)
    monkeypatch.setattr(pipeline, "sports_tracked", lambda: ["soccer_epl"])
    assert pipeline.league_plan() == ["soccer_epl"]


def test_payload_carries_readable_league_names():
    frame = pd.DataFrame([_selection(competition="PD"), _selection(competition="SA")])
    payload = card.to_payload(frame)
    assert payload["competitions"] == {"PD": "La Liga", "SA": "Serie A"}
    assert payload["selections"][0]["competition_name"] == "La Liga"


def _selection(**overrides):
    row = {
        "date": pd.Timestamp("2026-08-14"), "competition": "PL", "match": "a v b",
        "home_team": "a", "away_team": "b", "key": "1x2_home", "group": "1x2",
        "selection": "Home win", "prob": 0.5, "fair_odds": 2.0, "odds": 2.1,
        "edge": 0.05, "hit_rate": 0.5, "hit_rate_n": 10, "new_team": False,
        "validated": True, "implied_resid": 0.0,
    }
    row.update(overrides)
    return row


def test_payload_replaces_nan_with_null_and_rounds():
    """JSON has no NaN. Embedding one produces a page that dies on load with a
    syntax error and no clue as to why."""
    frame = pd.DataFrame([{
        "date": pd.Timestamp("2026-08-14"), "competition": "PL", "match": "a v b",
        "home_team": "a", "away_team": "b", "key": "1x2_home", "group": "1x2",
        "selection": "Home win", "prob": 0.123456789, "fair_odds": 8.1000001,
        "odds": float("nan"), "edge": float("nan"), "hit_rate": 0.5,
        "hit_rate_n": 10, "new_team": False, "validated": True,
        "implied_resid": 0.0,
    }])
    payload = card.to_payload(frame)
    row = payload["selections"][0]
    assert row["odds"] is None and row["edge"] is None
    assert row["prob"] == 0.1235
    assert row["date"] == "2026-08-14"
    json.dumps(payload)              # must not raise


# -- pages -----------------------------------------------------------------

EMPTY = {"picks": None, "reliability": None, "evidence": None, "data": None,
         "ledger": None, "status": [], "ready": {}}


def test_every_page_in_the_nav_is_routable_and_buildable():
    """A page listed in the nav but missing from the server's routes appears in
    the menu and 404s when clicked."""
    from hub import server
    listed = {page for page, _, _ in c.PAGES}
    assert listed == set(pages.BUILDERS)
    assert listed == set(server.ROUTES.values())


@pytest.mark.parametrize("page", sorted(pages.BUILDERS))
def test_every_page_renders_without_any_data(page):
    """Missing artifacts must produce an explanation and the name of the button
    that fixes it — not an empty table that reads like a result."""
    html = pages.render(page, c.Links("server"), dict(EMPTY))
    assert html.startswith("<!doctype html>")
    # The wordmark is drawn in two pieces, so the brand is checked the way the
    # page actually spells it rather than as one string.
    assert "FOOTBALL" in html and "BETTING HUB" in html


@pytest.mark.parametrize("page,button", [
    ("card", "Refresh the card"),
    ("fixtures", "Refresh the card"),
    ("history", "refresh the card"),
    ("reliability", "Recalibrate"),
    ("evidence", "Rebuild the evidence"),
])
def test_an_empty_page_names_the_button_that_fills_it(page, button):
    html = pages.render(page, c.Links("server"), dict(EMPTY))
    assert button in html


def test_the_card_page_shows_the_selections_it_was_given():
    payload = {
        "built": "2026-08-12T10:00:00", "n_fixtures": 1, "n_selections": 2,
        "first_date": "2026-08-14", "last_date": "2026-08-14",
        "competitions": {"PL": "Premier League"},
        "groups": {"ou": "Total goals"},
        "ceilings": {"ou": 0.99}, "calibrated_on": 56856,
        "selections": [
            {"date": "2026-08-14", "competition": "PL", "match": "a v b",
             "home_team": "a", "away_team": "b", "key": "ou0.5_over", "group": "ou",
             "selection": "Over 0.5 goals", "prob": 0.93, "fair_odds": 1.08,
             "odds": None, "edge": None, "hit_rate": 0.93, "hit_rate_n": 900,
             "new_team": False, "validated": True, "implied_resid": 0.0},
            {"date": "2026-08-14", "competition": "PL", "match": "a v b",
             "home_team": "a", "away_team": "b", "key": "ou4.5_under", "group": "ou",
             "selection": "Under 4.5 goals", "prob": 0.30, "fair_odds": 3.3,
             "odds": None, "edge": None, "hit_rate": None, "hit_rate_n": 0,
             "new_team": False, "validated": True, "implied_resid": 0.0},
        ],
    }
    html = pages.render("card", c.Links("server"), dict(EMPTY, picks=payload))
    assert "Over 0.5 goals" in html
    # Below the floor, so it never reaches the browser at all.
    assert "Under 4.5 goals" not in html


@pytest.mark.parametrize("page", sorted(pages.BUILDERS))
def test_every_page_marks_itself_in_the_nav(page):
    """The current tab has to be the page you are on.

    Card got this wrong the moment it moved off the root: it went on declaring
    itself "index", so clicking Card lit up Home. Nothing else noticed, because
    a wrong `current` renders perfectly valid HTML.
    """
    import re
    links = c.Links("server")
    html = pages.render(page, links, dict(EMPTY))
    marked = re.findall(r'<a href="([^"]+)"[^>]*aria-current="page"', html)
    assert marked == [links.href(page)], f"{page} highlighted {marked}"


def test_the_root_is_the_landing_page_and_the_card_moved():
    """A public site's front door introduces the thing; the card is one click
    in. Pinning both directions so a future rename cannot silently swap them."""
    landing = pages.render("index", c.Links("static"), dict(EMPTY))
    assert "CALIBRATED FOOTBALL PROBABILITIES" in landing
    assert 'href="card.html"' in landing
    # One <h1> per page: the hero brings its own, so the standard head is off.
    assert landing.count("<h1") == 1


def test_the_landing_page_states_the_record_from_the_ledger():
    """Not from prose. A landing page quoting a figure nothing generated is
    how a site ends up advertising a record it no longer has."""
    frame = pd.DataFrame([
        {"day": "2026-08-14", "band": "main", "match": "a v b",
         "selection": "Over 2.5 goals", "prob": 0.62, "fair_odds": 1.61,
         "odds": 1.75, "outcome": "won", "pnl": 0.75, "competition": "PL",
         "key": "ou2.5_over", "issued": "2026-08-13T10:00:00"},
        {"day": "2026-08-15", "band": "main", "match": "c v d",
         "selection": "Over 2.5 goals", "prob": 0.60, "fair_odds": 1.67,
         "odds": None, "outcome": "lost", "pnl": None, "competition": "PL",
         "key": "ou2.5_over", "issued": "2026-08-14T10:00:00"},
    ])
    html = pages.render("index", c.Links("static"), dict(EMPTY, ledger=frame))
    assert "Daily pick record" in html
    assert "1\u20131" in html or "1–1" in html
    # Stated at its true size rather than as the headline figure: a
    # handful of settled bets is not the claim this site rests on, and
    # the strip is built to say so.
    assert "and young" in html


def test_the_landing_page_survives_having_nothing_to_show():
    """It is the first page a stranger loads, so it must not be the one that
    explodes on a machine that has fetched nothing yet."""
    html = pages.render("index", c.Links("static"), dict(EMPTY))
    assert html.startswith("<!doctype html>")
    assert "No pick on the board" in html


def test_the_history_page_renders_a_mixed_ledger():
    """Settled, void and pending rows together — the mixture is where a NaN
    slips through and takes the page down."""
    from hub import ledger
    frame = pd.DataFrame([
        {"day": "2026-08-14", "competition": "PL", "competition_name": "Premier League",
         "match": "a v b", "selection": "Over 2.5 goals", "prob": 0.62,
         "fair_odds": 1.61, "odds": 1.75, "home_goals": 2, "away_goals": 1,
         "outcome": "won", "pnl": 0.75, "played_on": "2026-08-14"},
        {"day": "2026-08-15", "competition": "SA", "competition_name": "Serie A",
         "match": "c v d", "selection": "Home win", "prob": 0.58,
         "fair_odds": 1.72, "odds": None, "home_goals": 0, "away_goals": 1,
         "outcome": "lost", "pnl": None, "played_on": "2026-08-15"},
        {"day": "2026-08-16", "competition": "PD", "competition_name": "La Liga",
         "match": "e v f", "selection": "Draw no bet", "prob": 0.61,
         "fair_odds": 1.64, "odds": 1.70, "outcome": "pending"},
    ]).reindex(columns=ledger.COLUMNS)

    html = pages.render("history", c.Links("server"), dict(EMPTY, ledger=frame))
    assert "a v b" in html and "Pending" in html
    assert "2–1" in html                      # the score of the settled match
    assert "nan" not in html.lower()


def test_fixture_rows_carry_the_match_as_an_attribute():
    """The expand-a-row behaviour matches on this attribute; matching on the
    rendered cell would break on the first accent or tag."""
    payload = {
        "built": "2026-08-12T10:00:00", "n_fixtures": 1, "n_selections": 1,
        "competitions": {"PD": "La Liga"}, "groups": {}, "ceilings": {},
        "calibrated_on": 0,
        "selections": [
            {"date": "2026-08-14", "competition": "PD", "match": "Alavés v Getafe",
             "home_team": "Alavés", "away_team": "Getafe", "key": "1x2_home",
             "group": "1x2", "selection": "Home win", "prob": 0.62, "fair_odds": 1.6,
             "odds": None, "edge": None, "hit_rate": None, "hit_rate_n": 0,
             "new_team": True, "validated": True, "implied_resid": 0.0},
        ],
    }
    html = pages.render("fixtures", c.Links("server"), dict(EMPTY, picks=payload))
    assert 'data-match="Alav&#xe9;s v Getafe"' in html or \
           'data-match="Alavés v Getafe"' in html
    assert "new team" in html


# -- static export ---------------------------------------------------------

def test_export_writes_a_self_contained_site(tmp_path, monkeypatch):
    monkeypatch.setattr(pages, "build_context", lambda: dict(EMPTY))
    out = export.export(tmp_path / "site")

    for page in pages.BUILDERS:
        assert (out / f"{page}.html").exists()
    for asset in export.ASSETS:
        assert (out / "assets" / asset).exists()

    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'href="fixtures.html"' in html
    assert 'href="/fixtures"' not in html          # would 404 on Pages
    assert "<button class=\"run" not in html


# -- the card shows what was recorded --------------------------------------

def _slate_pick(**overrides):
    row = {
        "band": "main", "band_low": 1.6, "band_high": 2.2,
        "day": "2026-08-26", "date": "2026-08-26", "candidates": 11,
        "competition": "PD", "competition_name": "La Liga",
        "home": "real madrid", "away": "sociedad",
        "match": "Real Madrid v Real Sociedad", "key": "ou2.5_over",
        "group": "ou", "selection": "Over 2.5 goals", "prob": 0.614,
        "fair_odds": 1.63, "odds": 1.53, "edge": -0.06, "hit_rate": 0.658,
        "hit_rate_n": 36216, "new_team": False, "score": 0.614,
    }
    row.update(overrides)
    return row


def _recorded(path, **overrides):
    row = {
        "day": "2026-08-26", "band": "main", "competition": "PD",
        "competition_name": "La Liga", "home": "real madrid",
        "away": "sociedad", "match": "Real Madrid v Real Sociedad",
        "key": "ou3.5_under", "group": "ou", "selection": "Under 3.5 goals",
        "prob": 0.613, "fair_odds": 1.631, "odds": None, "hit_rate": 0.658,
        "hit_rate_n": 36563,
    }
    row.update(overrides)
    ledger.record(row, path, today="2026-08-24")


def test_a_day_already_in_the_ledger_is_shown_as_recorded(tmp_path):
    """The bug this fixes: the card re-prices every fixture on every build, so
    two days after a pick was written down it happily prefers a different
    selection for the same day. Only one of them is graded, and it is the one
    in the ledger — so the front page named one bet and History named another.
    """
    path = tmp_path / "best_picks.csv"
    _recorded(path)
    payload = {"slate": [_slate_pick()], "best_pick": _slate_pick()}

    changed = card._apply_ledger(payload, path=path)

    assert payload["best_pick"]["selection"] == "Under 3.5 goals"
    assert payload["slate"][0]["selection"] == "Under 3.5 goals"
    assert [c["day"] for c in changed] == ["2026-08-26"]
    # The count of what qualified is about today's card, not about the bet.
    assert payload["best_pick"]["candidates"] == 11


def test_a_day_not_yet_recorded_keeps_the_price_just_computed(tmp_path):
    path = tmp_path / "best_picks.csv"
    payload = {"slate": [_slate_pick()], "best_pick": _slate_pick()}

    assert card._apply_ledger(payload, path=path) == []
    assert payload["best_pick"]["selection"] == "Over 2.5 goals"


def test_a_recorded_band_survives_falling_off_the_card(tmp_path):
    """A band that has nothing in its price range today must not quietly drop
    the pick already written down for that day — it is still going to be
    graded."""
    path = tmp_path / "best_picks.csv"
    _recorded(path, band="value", key="tt0.5_away_under",
              selection="Away team under 0.5 goals", prob=0.429, fair_odds=2.33)
    payload = {"slate": [_slate_pick()], "best_pick": _slate_pick()}

    card._apply_ledger(payload, path=path)

    bands = [pick["band"] for pick in payload["slate"]]
    assert bands == ["main", "value"]
    assert "candidates" not in payload["slate"][1]


def test_the_recorded_pick_renders_with_no_qualifying_count(tmp_path):
    """`candidates` is absent for a band that is off the card, and the section
    used to interpolate it unconditionally."""
    path = tmp_path / "best_picks.csv"
    _recorded(path)
    payload = {"slate": [_slate_pick(band="safe", band_low=1.3, band_high=1.6)],
               "best_pick": None}
    card._apply_ledger(payload, path=path)

    html = pages._best_pick_section({"best_pick": payload["best_pick"],
                                     "best_band": [1.6, 2.2]})
    assert "Under 3.5 goals" in html
    assert "that qualified" not in html


def test_a_pick_written_down_on_an_earlier_day_says_so():
    """Three days of horizon means most of the card was recorded a build or two
    ago, price included. Unlabelled, those numbers read as this morning's."""
    html = pages._best_pick_section({
        "best_band": [1.6, 2.2],
        "best_pick": {**_slate_pick(), "recorded_at": "2026-08-24T11:03:39"}})
    assert "Written down on Mon 24 Aug at 11:03" in html


def test_a_pick_recorded_today_is_not_labelled_as_old():
    stamp = datetime.now().isoformat(timespec="seconds")
    html = pages._best_pick_section({
        "best_band": [1.6, 2.2],
        "best_pick": {**_slate_pick(), "recorded_at": stamp}})
    assert "Written down on" not in html


# -- and the same for the accumulator --------------------------------------

def _slip(**overrides):
    row = {
        "legs": 4, "target_odds": 3.0, "min_leg_odds": 1.3161,
        "probability": 0.319, "fair_odds": 3.14, "offered_odds": None,
        "weakest_leg": 0.743, "first_day": "2026-08-26", "last_day": "2026-08-28",
        "selections": [
            {"date": "2026-08-26", "competition": "PD", "competition_name": "La Liga",
             "home": "real madrid", "away": "sociedad",
             "match": "Real Madrid v Real Sociedad", "key": "corners7.5_over",
             "selection": "Over 7.5 corners", "prob": 0.759, "fair_odds": 1.32,
             "odds": None},
            {"date": "2026-08-27", "competition": "PD", "competition_name": "La Liga",
             "home": "celta", "away": "osasuna", "match": "Celta Vigo v CA Osasuna",
             "key": "dc_1x", "selection": "Home or draw (1X)", "prob": 0.756,
             "fair_odds": 1.32, "odds": None},
        ],
    }
    row.update(overrides)
    return row


def _payload_with(slip, default="4"):
    return {"accumulators": {"2": _slip(legs=2), default: slip},
            "acca_default": default}


def test_the_accumulator_shown_is_the_one_that_was_recorded(tmp_path):
    """Same failure as the slate, on a shorter fuse: the slip is keyed on the
    day it was issued, so a second build after new odds land is refused by the
    ledger and used by the page."""
    path = tmp_path / "best_accas.csv"
    ledger.record_acca(_slip(), path)

    repriced = _slip(selections=[
        {**_slip()["selections"][0], "key": "ou1.5_over",
         "selection": "Over 1.5 goals"}, _slip()["selections"][1]])
    payload = _payload_with(repriced)

    swapped = card._apply_acca_ledger(payload, path=path)

    shown = payload["accumulators"][payload["acca_default"]]
    assert [leg["selection"] for leg in shown["selections"]] == [
        "Over 7.5 corners", "Home or draw (1X)"]
    assert "Over 1.5 goals" in swapped["repriced"]
    assert "Over 7.5 corners" in swapped["recorded"]


def test_the_other_leg_counts_stay_live(tmp_path):
    """Only the default is ever handed to `record_acca`, so only the default
    can claim to be in the record."""
    path = tmp_path / "best_accas.csv"
    ledger.record_acca(_slip(), path)
    payload = _payload_with(_slip())

    card._apply_acca_ledger(payload, path=path)

    assert payload["accumulators"]["2"]["legs"] == 2
    assert "recorded_at" not in payload["accumulators"]["2"]


def test_a_day_with_no_slip_recorded_keeps_the_one_just_computed(tmp_path):
    path = tmp_path / "best_accas.csv"
    payload = _payload_with(_slip())

    assert card._apply_acca_ledger(payload, path=path) is None
    assert "recorded_at" not in payload["accumulators"]["4"]


def test_re_pricing_the_same_legs_is_not_a_disagreement(tmp_path):
    """Prices and probabilities drift between builds and that is not a
    different accumulator. A swapped leg is."""
    path = tmp_path / "best_accas.csv"
    ledger.record_acca(_slip(), path)
    drifted = _slip(probability=0.324)
    drifted["selections"][0]["prob"] = 0.761
    payload = _payload_with(drifted)

    assert card._apply_acca_ledger(payload, path=path) is None
    # Nothing to report, but the recorded numbers are still the ones shown:
    # the record was made at 0.319, and that is what it will be graded on.
    shown = payload["accumulators"]["4"]
    assert shown["probability"] == pytest.approx(0.319)
    assert shown["selections"][0]["prob"] == pytest.approx(0.759)


def test_a_slip_recorded_at_another_leg_count_is_still_the_one_shown(tmp_path):
    """If ACCA_LEGS changes after a slip is written down, the recorded one is
    still the bet that gets graded — so it is still the one to show."""
    path = tmp_path / "best_accas.csv"
    ledger.record_acca(_slip(legs=3), path)
    payload = _payload_with(_slip(), default="4")

    card._apply_acca_ledger(payload, path=path)

    assert payload["acca_default"] == "3"
    assert payload["accumulators"]["3"]["legs"] == 3


def test_the_card_says_which_slip_size_is_in_the_record():
    html = pages._accumulator_section({
        "acca_target": 3.0, "acca_default": "4",
        "accumulators": {"4": {**_slip(), "recorded_at": "2026-08-26T09:12:04"}}})
    assert "The 4-leg slip is the one written into the record" in html
    assert "went down at 09:12" in html


def test_a_recorded_slip_survives_the_card_being_unable_to_build_one(tmp_path):
    """Nothing clearing the target today does not unrecord this morning's slip
    — it is still going to be graded, so it is still what the page shows."""
    path = tmp_path / "best_accas.csv"
    ledger.record_acca(_slip(), path)
    payload = {"accumulators": {}, "acca_default": "4"}

    card._apply_acca_ledger(payload, path=path)

    assert payload["accumulators"]["4"]["legs"] == 4
    assert pages._accumulator_section({**payload, "acca_target": 3.0})
