"""The web layer: link modes, the control strip, the job runner, the pages.

The failures worth catching here are the quiet ones — a static export whose
links point at server routes and 404 on GitHub Pages, a job that dies without
saying so, a page that renders an empty table instead of admitting the data is
missing.
"""

import json
import time

import pandas as pd
import pytest

from hub import artifacts, card, components as c, export, jobs, leagues, pages


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


def _fake_jobs():
    return [jobs.Job("a", "Cheap thing", "does a thing", lambda progress: None, "~1s"),
            jobs.Job("b", "Costly thing", "spends money", lambda progress: None,
                     "~1s", cost="spends credits")]


def test_static_control_strip_has_no_buttons():
    """A button that cannot do anything is worse than no button."""
    html = c.control_strip(c.Links("static"), _fake_status(), _fake_jobs())
    assert "<button" not in html
    assert "Snapshot" in html


def test_server_control_strip_flags_what_costs_money():
    html = c.control_strip(c.Links("server"), _fake_status(), _fake_jobs())
    assert html.count("<button") == 2
    assert 'data-job="b"' in html
    assert "danger" in html
    assert "spends credits" in html


def test_control_strip_shows_staleness_and_absence_differently():
    fresh = c.control_strip(c.Links("server"), _fake_status(), [])
    stale = c.control_strip(c.Links("server"), _fake_status(stale=True), [])
    missing = c.control_strip(c.Links("server"), _fake_status(missing=True), [])
    assert 'chip fresh' in fresh
    assert "chip stale" in stale and "behind upcoming fixture prices" in stale
    assert "chip missing" in missing and "never built" in missing


# -- artifact freshness ----------------------------------------------------

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

def test_a_job_runs_and_its_output_reaches_the_log(tmp_path):
    def work(progress):
        print("printed by the library")
        progress("reported directly")
        return {"rows": 3}

    runner = jobs.Runner([jobs.Job("w", "Work", "", work, "~1s")],
                         status_path=tmp_path / "status.json")
    ok, _ = runner.start("w")
    assert ok
    _wait(runner)

    snapshot = runner.snapshot()
    text = "\n".join(snapshot["lines"])
    assert "printed by the library" in text
    assert "reported directly" in text
    assert snapshot["last"]["w"]["ok"] is True
    assert snapshot["last"]["w"]["result"] == {"rows": 3}


def test_a_failing_job_says_so_instead_of_hanging(tmp_path):
    def boom(progress):
        raise ValueError("no data")

    runner = jobs.Runner([jobs.Job("b", "Boom", "", boom, "~1s")],
                         status_path=tmp_path / "status.json")
    runner.start("b")
    _wait(runner)

    snapshot = runner.snapshot()
    assert snapshot["running"] is None          # the button must come back
    assert snapshot["last"]["b"]["ok"] is False
    assert "no data" in snapshot["last"]["b"]["error"]
    assert any("ValueError" in line for line in snapshot["lines"])


def test_two_jobs_cannot_run_at_once(tmp_path):
    """They write the same CSVs. Concurrent runs fail in ways that look like
    modelling bugs rather than scheduling ones."""
    release = []

    def slow(progress):
        while not release:
            time.sleep(0.01)

    runner = jobs.Runner([jobs.Job("s", "Slow", "", slow, "~1s"),
                          jobs.Job("t", "Other", "", lambda progress: None, "~1s")],
                         status_path=tmp_path / "status.json")
    runner.start("s")
    ok, message = runner.start("t")
    assert not ok and "Slow" in message
    release.append(True)
    _wait(runner)


def test_unknown_job_is_refused(tmp_path):
    runner = jobs.Runner([], status_path=tmp_path / "status.json")
    ok, message = runner.start("drop-everything")
    assert not ok and "Unknown job" in message


def test_the_log_only_returns_lines_the_client_has_not_seen(tmp_path):
    def chatty(progress):
        for i in range(5):
            progress(f"line {i}")

    runner = jobs.Runner([jobs.Job("c", "Chatty", "", chatty, "~1s")],
                         status_path=tmp_path / "status.json")
    runner.start("c")
    _wait(runner)

    first = runner.snapshot(0)
    assert len(first["lines"]) == first["next"]
    assert runner.snapshot(first["next"])["lines"] == []


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
    assert "Settled record" in html
    assert "1\u20131" in html or "1–1" in html


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
