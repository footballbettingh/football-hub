"""The Telegram notification.

This is the only part of the project that speaks to the outside world on the
project's behalf, so the failures worth testing are the ones that would either
send something wrong or stop a refresh that had otherwise worked: markup broken
by a club name, a repeat of yesterday's pick, and credentials that are simply
not there yet.
"""

import pytest

from hub import notify


class _FakeResponse:
    def __init__(self, body, status=200):
        self._body, self.status_code, self.text = body, status, str(body)

    def json(self):
        return self._body


def payload(**overrides):
    """picks.json, reduced to what the message actually reads."""
    best = {
        "band": "main", "day": "2026-08-20", "date": "2026-08-20",
        "competition": "PD", "competition_name": "La Liga",
        "match": "Rayo Vallecano v Alaves", "selection": "Not both teams to score",
        "key": "btts_no", "prob": 0.611, "fair_odds": 1.6366, "odds": None,
        "edge": None, "hit_rate": 0.6409, "hit_rate_n": 1774,
    }
    best.update(overrides.pop("best_pick", {}))
    out = {
        "best_pick": best,
        "best_band": [1.6, 2.2],
        "acca_default": "4",
        "slate": [
            dict(best, band="safe", selection="Over 7.5 corners",
                 match="Sheffield Wednesday v Bradford City", prob=0.7318,
                 fair_odds=1.3666),
            best,
        ],
        "accumulators": {"4": {
            "probability": 0.3317, "fair_odds": 3.0145, "offered_odds": None,
            "selections": [
                {"date": "2026-08-22", "match": "Estoril v Rio Ave FC",
                 "selection": "Away team under 1.5 goals", "prob": 0.76,
                 "fair_odds": 1.32}]}},
    }
    out.update(overrides)
    return out


# -- the message -----------------------------------------------------------

def test_the_message_is_three_lines_and_answers_only_what_the_bet_is():
    """Read on a lock screen. Everything that is not the bet belongs on the
    page that has room for it."""
    text = notify.format_picks(payload())
    assert len(text.splitlines()) == 3
    for fragment in ("Rayo Vallecano v Alaves", "La Liga", "2026-08-20",
                     "Not both teams to score", "61.1%", "1.64"):
        assert fragment in text, fragment
    for absent in ("64.1%", "1,774", "band", "Accumulator",
                   "Sheffield Wednesday", "Record"):
        assert absent not in text, absent


def test_full_restores_the_long_version():
    """The flagship is one of three, and the record is what keeps the claim
    honest — still available, just not on the lock screen."""
    text = notify.format_picks(payload(), full=True)
    assert "Sheffield Wednesday" in text and "safe" in text
    assert "Accumulator" in text and "64.1%" in text and "band 1.6" in text


def test_other_days_do_not_leak_into_today():
    card = payload()
    card["slate"] = [dict(card["slate"][0], day="2026-08-25",
                          match="Tomorrow FC v Later United")]
    assert "Tomorrow FC" not in notify.format_picks(card, full=True)


def test_offered_price_appears_only_when_a_bookmaker_quoted_one():
    assert "offered" not in notify.format_picks(payload())
    quoted = notify.format_picks(payload(best_pick={"odds": 1.91, "edge": 0.07}))
    assert "offered <b>1.91</b>" in quoted and "+7.0%" in quoted


def test_message_escapes_html_so_a_club_name_cannot_break_the_markup():
    """parse_mode=HTML means an unescaped < is a 400 from Telegram."""
    text = notify.format_picks(payload(best_pick={"match": "A <b>& B</b> v C"}))
    assert "A &lt;b&gt;&amp; B&lt;/b&gt; v C" in text


def test_message_without_a_pick_says_so_instead_of_crashing():
    assert "No best pick" in notify.format_picks({"best_pick": None})


def test_record_line_reports_the_ledger_against_its_own_claim():
    text = notify.format_picks(payload(), {
        "settled": 12, "wins": 10, "losses": 2, "pending": 15,
        "hit_rate": 0.8333, "expected": 0.6141}, full=True)
    assert "Record 10-2" in text and "83.3%" in text and "61.4%" in text


def test_record_line_is_absent_before_anything_is_settled():
    text = notify.format_picks(payload(), {"settled": 0, "wins": 0, "losses": 0},
                               full=True)
    assert "Record" not in text


def test_the_short_message_never_reads_the_ledger():
    """It has nowhere to put the record, so loading it would be work for
    nothing — and one more thing that could fail on the way to a send."""
    def explode():
        raise AssertionError("ledger read for the short message")

    import hub.ledger as ledger_mod
    original, ledger_mod.load = ledger_mod.load, explode
    try:
        notify.notify(payload(), dry_run=True)
    finally:
        ledger_mod.load = original


# -- when to send ----------------------------------------------------------

def test_fingerprint_ignores_price_drift_but_not_the_bet():
    """Prices move between refreshes. That is not news; a different bet is."""
    base = notify.fingerprint(payload())
    assert notify.fingerprint(payload(best_pick={"prob": 0.63, "fair_odds": 1.59})) == base
    assert notify.fingerprint(payload(best_pick={"key": "btts_yes"})) != base
    assert notify.fingerprint(payload(best_pick={"day": "2026-08-21"})) != base


def test_notify_stays_quiet_only_when_asked_and_only_when_unchanged(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send_message",
                        lambda text, **kw: sent.append(text) or ["42"])
    monkeypatch.setattr(notify, "configured", lambda *a, **k: True)
    monkeypatch.setattr(notify, "ledger_summary", lambda: None)
    state = tmp_path / "state.json"

    first = notify.notify(payload(), only_if_changed=True, state_path=state)
    again = notify.notify(payload(best_pick={"prob": 0.62}),
                          only_if_changed=True, state_path=state)
    forced = notify.notify(payload(best_pick={"prob": 0.62}), state_path=state)

    assert (first["status"], again["status"], forced["status"]) == (
        "sent", "unchanged", "sent")
    assert len(sent) == 2


def test_notify_reports_missing_credentials_instead_of_failing_the_run(monkeypatch, tmp_path):
    """A refresh that cannot notify must still have rebuilt the card."""
    monkeypatch.setattr(notify.config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(notify.config, "TELEGRAM_CHAT_ID", "")
    monkeypatch.setattr(notify, "ledger_summary", lambda: None)
    assert notify.notify(payload(), state_path=tmp_path / "s.json")["status"] == "unconfigured"


def test_missing_card_is_reported_rather_than_raising(monkeypatch, tmp_path):
    monkeypatch.setattr(notify, "load_picks", lambda: None)
    assert notify.notify(state_path=tmp_path / "s.json")["status"] == "no-card"


def test_dry_run_never_touches_the_network(monkeypatch, tmp_path):
    monkeypatch.setattr(notify.requests, "post", lambda *a, **k: pytest.fail("posted"))
    monkeypatch.setattr(notify, "ledger_summary", lambda: None)
    result = notify.notify(payload(), dry_run=True, state_path=tmp_path / "s.json")
    assert result["status"] == "dry-run" and "Rayo Vallecano" in result["text"]


def test_a_broken_ledger_cannot_stop_the_notification():
    """The record is a footnote. It must not be able to veto the message."""
    def explode():
        raise OSError("best_picks.csv is locked")

    import hub.ledger as ledger_mod
    original, ledger_mod.load = ledger_mod.load, explode
    try:
        assert notify.ledger_summary() is None
    finally:
        ledger_mod.load = original


# -- the wire --------------------------------------------------------------

def test_chat_ids_splits_and_trims_so_one_stray_comma_is_harmless():
    assert notify.chat_ids("111, -1002 ,") == ["111", "-1002"]
    assert notify.chat_ids("") == []


def test_send_message_posts_once_per_chat(monkeypatch):
    calls = []

    def fake_post(url, json=None, **kw):
        calls.append((url, json))
        return _FakeResponse({"ok": True, "result": {"message_id": 1}})

    monkeypatch.setattr(notify.requests, "post", fake_post)
    reached = notify.send_message("hi", token="T", chats="111,-1002")

    assert reached == ["111", "-1002"]
    assert [c[1]["chat_id"] for c in calls] == ["111", "-1002"]
    assert all("/botT/sendMessage" in c[0] for c in calls)


def test_telegram_refusal_surfaces_the_reason(monkeypatch):
    """A channel that never made the bot an admin answers 403 with the reason."""
    monkeypatch.setattr(notify.requests, "post", lambda *a, **k: _FakeResponse(
        {"ok": False, "description": "Forbidden: need administrator rights"},
        status=403))
    with pytest.raises(notify.NotifyError, match="administrator rights"):
        notify.send_message("hi", token="T", chats="-1002")
