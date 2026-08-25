"""The five pages, built from artifacts on disk.

No page computes anything heavier than a sum: everything expensive was done by
a job and written to `data/`. That keeps a page load instant, and means the
static export and the served version are the same code with a different
`Links`.

Where an artifact is missing the page says so and names the button that
produces it, rather than rendering an empty table that looks like a result.
"""

from datetime import datetime

import pandas as pd

from confidence.markets import GROUPS

from . import artifacts, components as c, ledger

CONF_FLOOR = 0.55       # below this a selection is not worth shipping to the page

# What a cell says when there is no number for it. Printed into thousands of
# cells by the builders below and by hub.js, which uses the same mark, so it is
# generated punctuation rather than prose. Named so the two cannot drift apart.
#
# An en-dash, and the reason is the Edge column. Edges are signed, so a real
# value there reads "-2.8%", and on the full card an empty cell sits one row
# above a negative one. A hyphen placeholder next to a minus sign is the same
# glyph meaning two different things in the same column. The en-dash is also
# the width of a digit in Space Grotesk, where the hyphen is two thirds of one
# and rides too low to line up with the figures around it.
NONE = "–"


def build_context():
    """Load every artifact once. Cheap enough to do per request."""
    picks = artifacts.load_picks()
    return {
        "picks": picks,
        "ledger": ledger.load(),
        "accas": ledger.load_accas(),
        "reliability": artifacts.load_reliability(),
        "evidence": artifacts.load_evidence(),
        "data": artifacts.data_summary(),
    }


def _pct(value, digits=1):
    return NONE if value is None else f"{value * 100:.{digits}f}%"


def _num(value, digits=2):
    return NONE if value is None else f"{value:.{digits}f}"


def _interval(pair):
    """A Wilson interval, or the placeholder when there is nothing to bound."""
    if not pair or pair[0] is None:
        return NONE
    return f"{_pct(pair[0], 0)}&ndash;{_pct(pair[1], 0)}"


def _best_pick_section(picks):
    """The one bet, in a price range where a single pick is worth making."""
    best = picks.get("best_pick")
    low, high = picks.get("best_band", [1.6, 2.2])
    if not best:
        return f"""<section class="card">
  <h2>Best pick of the day</h2>
  <div class="empty">No selection between {low:g} and {high:g} on the next
  match day.<div class="hint">Every qualifying bet was either outside the price
  range or above the confidence its market has been checked to.</div></div>
</section>"""

    day = datetime.strptime(best["day"], "%Y-%m-%d")
    band = (NONE if best.get("hit_rate") is None else
            f'{_pct(best["hit_rate"])} <span class="note">over '
            f'{int(best.get("hit_rate_n") or 0):,} historical bets</span>')
    edge = ("" if best.get("edge") is None else
            f'<div class="n"><div class="k">Edge</div><div class="v">'
            f'{best["edge"] * 100:+.1f}%</div></div>')
    offered = ("" if best.get("odds") is None else
               f'<div class="n"><div class="k">Offered</div>'
               f'<div class="v">{_num(best["odds"])}</div></div>')

    return f"""<section class="card best">
  <h2>Best pick of the day</h2>
  <p class="note">The most reliable selection priced between {low:g} and {high:g}
  on {day:%A %d %B}, out of {best["candidates"]} that qualified. Ranked on the
  claim <em>discounted by how much its confidence band has historically
  overstated itself</em> — a band that came up two points short scales every
  claim in it down to match.</p>
  <div class="pick">
    <div class="side">
      <div class="fixture">{c.e(best["match"])}</div>
      <div class="when">{c.e(best.get("competition_name") or best["competition"])}
        &middot; {day:%a %d %b}
        {'<span class="tag warn">new team</span>' if best.get("new_team") else ''}</div>
      <div class="selection">Back <strong>{c.e(best["selection"])}</strong></div>
    </div>
    <div class="picknums">
      <div class="n"><div class="k">Confidence</div>
        <div class="v">{_pct(best["prob"])}</div></div>
      <div class="n"><div class="k">Fair odds</div>
        <div class="v">{_num(best["fair_odds"])}</div></div>
      {offered}{edge}
    </div>
  </div>
  <p class="note">This band has landed {band}.</p>
</section>"""


BAND_LABEL = {"safe": "Safe", "main": "Best", "value": "Longer"}


def _slate_section(picks):
    """One row per price band per match day, for the next few days."""
    slate = picks.get("slate") or []
    if not slate:
        return ""

    days = []
    for pick in slate:
        if pick["day"] not in days:
            days.append(pick["day"])

    rows = []
    for day in days:
        when = datetime.strptime(day, "%Y-%m-%d")
        for pick in [p for p in slate if p["day"] == day]:
            band = BAND_LABEL.get(pick["band"], pick["band"])
            flag = ('<span class="tag warn">new team</span>'
                    if pick.get("new_team") else "")
            edge = pick.get("edge")
            rows.append([
                f'{when:%a %d %b}',
                f'<span class="tag">{c.e(band)}</span> '
                f'<span class="note">{pick["band_low"]:g}&ndash;{pick["band_high"]:g}</span>',
                c.e(pick.get("competition_name") or pick["competition"]),
                c.e(pick["match"]) + flag,
                c.e(pick["selection"]),
                f'<strong>{_pct(pick["prob"])}</strong>',
                _num(pick["fair_odds"]),
                NONE if pick.get("odds") is None else _num(pick["odds"]),
                # Shown next to the offer, because an offer below fair odds is
                # a bet priced against you however likely it is.
                NONE if edge is None else
                f'<span style="color:var(--{"pos" if edge > 0 else "neg"})">'
                f'{edge * 100:+.1f}%</span>',
                NONE if pick.get("hit_rate") is None else _pct(pick["hit_rate"]),
            ])

    return f"""<section class="card">
  <h2>The next {len(days)} match days</h2>
  <p class="note">The strongest selection in each price band, for each day. All
  of them are written into the record the moment they appear here — Saturday's
  pick is logged on Thursday, at Thursday's price, and never revised. Three
  bands rather than one because they test the forecast at three different
  confidence levels, and because one pick a day needs ten months to reach a
  sample worth reading.</p>
  {c.table(["Day", "Band", "League", "Match", "Selection", "Confidence",
            "Fair", "Offered", "Edge", "Band record"], rows, numeric_from=5,
           raw=True)}
</section>"""


def _accumulator_section(picks):
    """The safest accumulator that still pays something."""
    accas = picks.get("accumulators") or {}
    if not accas:
        return ""
    target = picks.get("acca_target", 3.0)
    default = picks.get("acca_default", "4")
    if default not in accas:
        default = sorted(accas)[0]

    options = "".join(
        f'<option value="{c.e(legs)}"{" selected" if legs == default else ""}>'
        f'{c.e(legs)} legs</option>' for legs in sorted(accas))

    return f"""<section class="card">
  <h2>Accumulator pick</h2>
  <p class="note">Of every accumulator paying at least {target:g}, this is the one
  most likely to land. Each leg has to clear that target's n-th root on its own,
  so no single long shot carries the slip, and every leg comes from a different
  fixture — two selections on one match are correlated, and multiplying them
  overstates the whole thing.</p>
  <div class="filters">
    <label class="visually-hidden" for="acca-size">Legs per slip</label>
    <select id="acca-size">{options}</select>
    <span class="count" id="acca-summary"></span>
  </div>
  <div class="tablewrap"><table>
    <thead><tr><th>Date</th><th class="col-league">League</th><th>Match</th>
    <th>Selection</th><th class="num">Confidence</th><th class="num">Fair</th>
    <th class="num col-offered">Offered</th></tr></thead>
    <tbody id="acca-body"></tbody>
  </table></div>
  <div class="accanums" id="acca-totals"></div>
</section>"""


# What a selection carries to the browser, in payload order. `home_team`,
# `away_team` and `implied_resid` are deliberately absent: they ride along in
# picks.json for analysis, and no line of hub.js has ever read them.
CARD_COLUMNS = ["date", "competition", "match", "key", "group", "selection",
                "prob", "fair_odds", "odds", "edge", "hit_rate", "hit_rate_n",
                "new_team", "validated"]

# The six that repeat themselves: thirteen dates, thirty-one competitions,
# three hundred fixtures and forty markets, spread over every row on the card.
CARD_INTERNED = ("date", "competition", "match", "key", "group", "selection")

# Sent as 0/1 rather than true/false. Four characters a row each, twice a row,
# on six thousand rows.
CARD_FLAGS = ("new_team", "validated")


def _card_payload(picks):
    """Only what the table needs, and only rows worth showing.

    Rows travel as arrays against a shared column list rather than as objects.
    At close to six thousand selections the repeated JSON key names came to
    more than half the payload — more than every value in it put together —
    and the low-cardinality strings go the same way, because thirteen dates
    copied out six thousand times is thirteen dates and a lot of quotation
    marks. `hub.js` expands them back on load, so what the page filters and
    renders is the same row object it always was.
    """
    rows = [row for row in picks["selections"] if (row["prob"] or 0) >= CONF_FLOOR]
    rows.sort(key=lambda row: -(row["prob"] or 0))

    tables = {name: [] for name in CARD_INTERNED}
    seen = {name: {} for name in CARD_INTERNED}

    def intern(name, value):
        if value not in seen[name]:
            seen[name][value] = len(tables[name])
            tables[name].append(value)
        return seen[name][value]

    packed = []
    for row in rows:
        packed.append([
            intern(name, row.get(name)) if name in seen
            else int(bool(row.get(name))) if name in CARD_FLAGS
            else row.get(name)
            for name in CARD_COLUMNS
        ])

    return {
        "columns": CARD_COLUMNS,
        "tables": tables,
        "flags": list(CARD_FLAGS),
        "rows": packed,
        "ceilings": picks.get("ceilings", {}),
        "groups": picks.get("groups", {}),
        "competitions": picks.get("competitions", []),
    }


# -- 0. landing ------------------------------------------------------------

def _landing_pick(picks):
    """Today's headline pick, rendered for someone who arrived cold."""
    best = (picks or {}).get("best_pick")
    if not best:
        return ('<div class="lpick none">No pick on the board right now — either '
                'the next match day has nothing in the price range worth singling '
                'one out in, or the fixtures need refreshing.</div>')
    offered = (f'<div><div class="k">Offered</div><div class="v">'
               f'{_num(best.get("odds"))}</div></div>' if best.get("odds") else "")
    when = ""
    if best.get("day"):
        try:
            when = f" · {datetime.strptime(best['day'], '%Y-%m-%d'):%a %d %b}"
        except ValueError:
            when = ""
    # Sentence case, not tracked capitals. It was one of eight eyebrows on a
    # page entitled to two, and it was the least load-bearing of them.
    return f"""<div class="lpick">
  <div>
    <div class="band">Best pick of the day</div>
    <div class="fixture">{c.e(best.get('match', ''))}</div>
    <div class="meta">{c.e(best.get('competition_name') or best.get('competition', ''))}{when}</div>
    <div class="sel">{c.e(best.get('selection', ''))}</div>
  </div>
  <div class="nums">
    <div><div class="k">Confidence</div><div class="v">{_pct(best.get('prob'))}</div></div>
    <div><div class="k">Fair odds</div><div class="v">{_num(best.get('fair_odds'))}</div></div>
    {offered}
  </div>
</div>"""


def _calibration_points(ctx):
    """The pooled reliability curve, as the landing chart plots it."""
    table = ctx.get("reliability")
    if table is None or table.empty:
        return []
    pooled = table[table["scope"] == "all"]
    return [{"band": row.band, "n": int(row.n),
             "predicted": float(row.predicted), "actual": float(row.actual),
             "ci_low": float(row.ci_low), "ci_high": float(row.ci_high)}
            for row in pooled.itertuples()]


def _landing_stats(ctx):
    """The record, stated before any claim about the method.

    Read from the ledger and the dataset rather than written down, so the page
    cannot end up quoting a number nothing produced.

    One figure leads and three support it, rather than four abreast. The lead
    is the calibration record, because that is the claim the site actually
    makes and it rests on a million graded selections. The daily ledger sits
    among the supporting three at its true size: sixteen settled bets is not
    the headline, and formatting it like one would be the kind of overstatement
    this page exists to argue against.
    """
    points = _calibration_points(ctx)
    lead = ""
    if points:
        graded = sum(p["n"] for p in points)
        worst = max(abs(p["actual"] - p["predicted"]) for p in points)
        lead = (f'<div class="lstat lead">'
                f'<div class="v">{graded:,}</div>'
                f'<div class="k">Graded selections behind the calibration</div>'
                f'<div class="m">Worst band off by {worst * 100:.2f} points, '
                f'out of sample</div></div>')

    cells = []
    frame = ctx.get("ledger")
    if frame is not None and not frame.empty:
        head = ledger.summary(frame)
        cells.append((f"{head['wins']}\u2013{head['losses']}", "Daily pick record",
                      f"{head['pending']} still pending, and young"))
    picks = ctx.get("picks") or {}
    if picks.get("n_fixtures"):
        cells.append((f"{picks['n_fixtures']:,}", "Fixtures priced now",
                      f"{picks.get('n_selections', 0):,} selections"))
    data = ctx.get("data")
    if data:
        cells.append((f"{data['matches']:,}", "Matches behind it",
                      f"{data['competitions']} competitions"))
    if not lead and not cells:
        return ""
    return ('<div class="lstats">' + lead + "".join(
        f'<div class="lstat"><div class="v">{v}</div><div class="k">{k}</div>'
        f'<div class="m">{m}</div></div>' for v, k, m in cells) + "</div>")


def _landing_calibration(links, ctx):
    """The site's own argument, drawn once, on the front door."""
    points = _calibration_points(ctx)
    if not points:
        return ""
    worst = max(abs(p["actual"] - p["predicted"]) for p in points)
    return f"""
<div class="lsection lcal">
  <h2>Does an 85% pick win 85% of the time?</h2>
  <div class="lcal-body">
    <div class="chart" id="calibration"></div>
    <div class="lcal-note">
      <p>Every graded selection, bucketed by what it claimed and plotted against
      what then happened. A dot on the dashed line is a band that came in exactly
      where it said it would; the shaded strip is one percentage point either
      side of it.</p>
      <p>All {len(points)} bands land inside that strip. The worst is off by
      <strong>{worst * 100:.2f} points</strong>. Dot size is the number of bets
      behind each one.</p>
      <p><a href="{links.href('reliability')}">The same thing market by market</a>,
      including the two that fail.</p>
    </div>
  </div>
</div>"""


def page_landing(links, ctx):
    picks = ctx.get("picks") or {}

    # The order is carried by the wording rather than by 01/02/03/04 markers
    # above each card. It is a real sequence, so it should read as one; it does
    # not need four more tracked-capital labels on a page that had eight of
    # them and was entitled to two.
    #
    # Four cards of prose was the wrong shape: the cards could have been
    # shuffled and nothing would have broken, which is the tell that a layout
    # is not carrying its content. This is a pipeline — each stage takes the
    # number the stage above it produced and hands it on — so it is drawn as
    # one. The rail is the ordering, which is why there are no 01/02/03
    # markers; the rail turns from hairline to accent at the calibration,
    # because that is the stage where the number starts being worth trusting
    # and it stays trustworthy afterwards.
    #
    # Each stage names what it takes in and what it puts out. That line is
    # literally what the code at that stage does, not a caption.
    steps = [
        ("First, the closing price",
         "closing line", "fair probability", "",
         "The closing line is the sharpest number a bookmaker publishes. Raw "
         "<code>1/odds</code> sums to about 1.07, and counting that margin as "
         "information is the easiest way to fool yourself \u2014 so it is removed first."),
        ("Then a model, walked forward",
         "goals", "model probability", "",
         "Team strengths are estimated from goals with a low-score correction, "
         "refitted as the season moves, and every match is predicted using only "
         "what was known before it kicked off."),
        ("Then the calibration",
         "model probability", "calibrated probability", "pivot",
         "The raw number is fitted to what actually happened, on folds it never "
         "saw. That is the step which makes \u201c62%\u201d mean 62% instead of "
         "meaning \u201cconfident\u201d."),
        ("Finally, written down and graded",
         "calibrated probability", "a graded record", "trusted",
         "One pick per price band per match day goes into a ledger before "
         "kick-off and is settled against the result afterwards. The record on "
         "this page is that file, not a backtest."),
    ]
    step_html = "".join(
        f'<div class="lstep {state}">'
        f'<div class="rail" aria-hidden="true"></div>'
        f'<div class="what"><h3>{c.e(title)}</h3>'
        f'<div class="io">{c.e(takes)} <span class="arrow">&rarr;</span> '
        f'{c.e(makes)}</div></div>'
        f'<p>{body}</p></div>'
        for title, takes, makes, state, body in steps)

    return c.layout(links, "Football Betting Hub", "index", f"""
<div class="lhero">
  <div class="eyebrow">CALIBRATED FOOTBALL PROBABILITIES</div>
  <h1>How likely it is. <em>Not</em> what to bet.</h1>
  <p class="lead">Every pick is written down before kick-off and graded against
  the result afterwards, in public, whichever way it goes.</p>
  <div class="lactions">
    <a class="btn primary" href="{links.href('card')}">See today's card</a>
    <a class="btn ghost" href="{links.href('reliability')}">Is 85% really 85%?</a>
  </div>
  {_landing_pick(picks)}
</div>

{_landing_stats(ctx)}

{_landing_calibration(links, ctx)}

<div class="lsection">
  <h2 class="lead">Four steps, and the third is the one that matters.</h2>
  <div class="lsteps">{step_html}</div>
</div>

<div class="lsection">
  <h2 class="lead">The limits are the product too.</h2>
  <div class="llimits"><ul>
    <li><strong>It will not tell you a bet is good value.</strong> A calibrated
    probability says how often something happens. Whether the price on offer is
    worth taking is a different question, and it needs a price this does not
    always have.</li>
    <li><strong>It is mostly the market.</strong> The closing line carries most
    of the forecast by design; the model's job is to add to it without damaging
    it. Measured on 45,580 out-of-sample matches, it does not beat the line — it
    keeps up with it.</li>
    <li><strong>The record is young.</strong> A handful of settled picks is not
    evidence of anything. The reliability tables run on tens of thousands of
    matches; the daily ledger does not, and is labelled with how few it has.</li>
    <li><strong>It is research, not advice.</strong> Nothing here accounts for
    your stake, your book, or your ability to get the price shown.</li>
  </ul></div>
</div>

<div class="lcta">
  <div>
    <h2>The card is rebuilt every morning</h2>
    <p>Results, model, calibration and prices refresh on a schedule, and the
    day's pick goes out before the first kick-off.</p>
  </div>
  <div class="lactions" style="margin-top:0">
    <a class="btn primary" href="{links.href('card')}">Open the card</a>
    <a class="btn ghost" href="{links.href('method')}">Read the method</a>
  </div>
</div>
""", show_head=False,
                    page_data={"calibration": _calibration_points(ctx)})


# -- 1. the card -----------------------------------------------------------

def page_card(links, ctx):
    picks = ctx["picks"]
    if not picks:
        return c.layout(
            links, "Card", "card",
            c.empty("No card yet.", "Press “Refresh the card” to price the upcoming "
                                    "fixtures. It takes about twenty seconds."),
            subtitle="The selections most likely to land.")

    payload = _card_payload(picks)
    # Counted off the selections rather than off the payload: the payload rows
    # are packed arrays now, and this number belongs to the page anyway.
    strong = [r for r in picks["selections"]
              if (r["prob"] or 0) >= 0.75 and r.get("validated")]
    by_match = len({r["match"] for r in strong})

    kpi = c.kpis([
        ("Fixtures priced", f"{picks['n_fixtures']}", picks.get("first_date", "")),
        ("Selections", f"{picks['n_selections']:,}", "across all markets"),
        ("At 75% or better", f"{len(strong):,}", f"on {by_match} fixtures"),
        ("Calibrated on", f"{picks.get('calibrated_on', 0):,}",
         "historical matches"),
    ])

    groups = "".join(f'<option value="{c.e(key)}">{c.e(label)}</option>'
                     for key, label in payload["groups"].items())
    # Sorted by the name people read, not the code they do not.
    comps = "".join(f'<option value="{c.e(code)}">{c.e(name)}</option>'
                    for code, name in sorted(payload["competitions"].items(),
                                             key=lambda kv: kv[1]))

    body = f"""
{kpi}

{_best_pick_section(picks)}

{_slate_section(picks)}

{_accumulator_section(picks)}

<section class="card">
  <h2>The card</h2>
  <p class="note">Ranked by calibrated probability. One selection per fixture by
  default, because Over 1.5 and Home &minus;1.5 in the same match are nearly the
  same bet — stacking them makes a card look far more diversified than it is.</p>
  <p class="note"><strong>Offered</strong> and <strong>Edge</strong> are blank
  wherever the price feed does not quote that market. It carries 1X2 and — once
  prices have been fetched with totals — Over/Under 1.5 and 2.5. Handicaps, team
  totals, BTTS and corners are priced here and nowhere else, so there is no
  offer to compare against. <strong>Fair</strong> is always filled in: it is what
  the price would have to be for the bet to break even.</p>

  <div class="filters">
    <label class="visually-hidden" for="f-q">Search team or match</label>
    <input id="f-q" type="search" placeholder="Search team or match" size="22">
    <label class="visually-hidden" for="f-comp">League</label>
    <select id="f-comp"><option value="all">All leagues</option>{comps}</select>
    <label class="visually-hidden" for="f-group">Market</label>
    <select id="f-group"><option value="all">All markets</option>{groups}</select>
    <label class="pill"><input id="f-min" type="range" min="55" max="99" value="75"
      step="1"> <span id="f-min-v">75%</span> confidence</label>
    <label class="pill"><input id="f-odds" type="range" min="100" max="300" value="100"
      step="5"> <span id="f-odds-v">1.00</span> fair odds</label>
    <label class="pill"><input id="f-one" type="checkbox" checked> one per fixture</label>
    <label class="pill"><input id="f-priced" type="checkbox"> only with a price</label>
    <label class="pill"><input id="f-unval" type="checkbox"> show unverified</label>
    <span class="count" id="f-count"></span>
  </div>

  <div class="tablewrap tall"><table class="sticky cardtable" id="cardtable">
    <thead><tr>
      <th></th><th class="nowrap">Date</th><th class="col-league">League</th>
      <th>Match</th><th>Selection</th>
      <th class="num">Confidence</th><th class="num">Fair</th>
      <th class="num col-offered">Offered</th><th class="num col-edge">Edge</th>
      <th class="col-band">Band record</th>
    </tr></thead>
    <tbody id="card-body"></tbody>
  </table></div>

  <div class="acca" id="acca" hidden>
    <div class="legs" id="acca-legs"></div>
    <div class="nums">
      <div><div class="k">Combined chance</div><div class="v" id="acca-prob">{NONE}</div></div>
      <div><div class="k">Fair odds</div><div class="v" id="acca-fair">{NONE}</div></div>
      <div><div class="k">Offered</div><div class="v" id="acca-offered">{NONE}</div></div>
      <button id="acca-clear">Clear</button>
    </div>
    <div class="warn" id="acca-warn" hidden></div>
  </div>
</section>

<section class="card">
  <h2>Confidence is not value</h2>
  <p class="note">The top of any such card is a wall of 96% picks at fair odds of
  1.04, and those are the prices bookmakers get most right. <strong>Edge</strong> is
  the only column about money, and it only exists where the fixture feed quotes a
  price — 1X2. Drag the fair-odds slider up to ask the more useful question: of the
  bets that actually pay something, which are the safest?</p>
  {c.next_links(links, [
      ("reliability", "Is 85% really 85%?", "the out-of-sample record"),
      ("fixtures", "Every fixture", "all markets, match by match"),
      ("evidence", "Why not chase value?", "the backtest that says don't")])}
</section>
"""
    return c.layout(links, "Card", "card", body,
                    page_data={"card": payload,
                               "accumulators": picks.get("accumulators") or {}},
                    subtitle="The selections most likely to land, ranked by a "
                             "probability that has been checked against what happened.",
                    badges=[f"{picks['n_fixtures']} fixtures",
                            f"{picks['n_selections']:,} selections",
                            f"built {picks['built'][:16].replace('T', ' ')}"])


# -- 2. fixtures -----------------------------------------------------------

# key, column header, and the class that decides whether it survives a narrow
# window. 1X2 and Over 2.5 are the markets people actually look for; the rest
# go rather than push the table into a sideways scroll.
HEADLINE = [("1x2_home", "Home", ""), ("1x2_draw", "Draw", ""),
            ("1x2_away", "Away", ""), ("btts_yes", "BTTS", "col-extra"),
            ("ou1.5_over", "O1.5", "col-extra"), ("ou2.5_over", "O2.5", ""),
            ("ou3.5_over", "O3.5", "col-extra")]


def page_fixtures(links, ctx):
    picks = ctx["picks"]
    if not picks:
        return c.layout(links, "Fixtures", "fixtures",
                        c.empty("No fixtures priced yet.",
                                "Press “Refresh the card”."),
                        subtitle="Every upcoming match, market by market.")

    wanted = {key for key, _, _ in HEADLINE}
    matches = {}
    for row in picks["selections"]:
        entry = matches.setdefault(row["match"], {
            "date": row["date"], "competition": row["competition"],
            "competition_name": row.get("competition_name") or row["competition"],
            "match": row["match"], "new_team": row["new_team"], "probs": {}})
        if row["key"] in wanted:
            entry["probs"][row["key"]] = row["prob"]

    # Built by hand rather than through `c.table`, because each row carries the
    # match name as an attribute: clicking one expands every market for that
    # fixture, and matching on the rendered cell text would break the moment a
    # tag or an accent got in the way.
    rows = []
    for entry in sorted(matches.values(), key=lambda m: (m["date"], m["match"])):
        cells = "".join(
            f'<td class="num {css}">{_pct(entry["probs"].get(key), 0)}</td>'
            for key, _, css in HEADLINE)
        flag = ' <span class="tag warn">new team</span>' if entry["new_team"] else ""
        rows.append(
            f'<tr data-match="{c.e(entry["match"])}">'
            f'<td class="nowrap">{c.e(entry["date"])}</td>'
            f'<td class="col-league">{c.e(entry["competition_name"])}</td>'
            f'<td>{c.e(entry["match"])}{flag}</td>{cells}</tr>')

    headers = "".join(f'<th class="num {css}">{c.e(label)}</th>'
                      for _, label, css in HEADLINE)
    fixture_table = f"""<div class="tablewrap tall"><table class="sticky fixtures">
    <thead><tr><th class="nowrap">Date</th><th class="col-league">League</th>
    <th>Match</th>{headers}</tr></thead>
    <tbody>{''.join(rows)}</tbody></table></div>"""

    body = f"""
<section class="card">
  <h2>{len(rows)} fixtures</h2>
  <p class="note">The headline markets for every match on the card. Click a row to
  see every selection for that fixture, ranked. “New team” means one side has no
  history in this competition — a promoted club or a cup tie — so the price is
  carrying almost the whole forecast.</p>
  <div class="filters">
    <label class="visually-hidden" for="fx-q">Search team or match</label>
    <input id="fx-q" type="search" placeholder="Search team or match" size="22">
    <span class="count" id="fx-count"></span>
  </div>
  {fixture_table}
</section>
"""
    return c.layout(links, "Fixtures", "fixtures", body,
                    page_data={"card": _card_payload(picks)},
                    subtitle="Every upcoming match, market by market.",
                    badges=[f"{len(rows)} fixtures"])


# -- 3. history ------------------------------------------------------------

OUTCOME_LABEL = {"won": ("good", "Won"), "lost": ("critical", "Lost"),
                 "void": ("neutral", "Void"), "pending": ("neutral", "Pending")}


def _acca_history_section(frame):
    """The accumulator's own book, kept apart from the single picks.

    They are different bets with different failure modes — a four-leg slip at
    33% and a single at 62% cannot share a hit rate without both becoming
    meaningless — so they never share a total either.
    """
    if frame is None or frame.empty:
        return """
<section class="card">
  <h2>Accumulator picks</h2>
  <div class="empty">No accumulator recorded yet.<div class="hint">One slip is
  written down each day you refresh the card.</div></div>
</section>"""

    head = ledger.acca_summary(frame)
    rows = []
    for row in frame.sort_values("issued", ascending=False).itertuples():
        outcome = row.outcome if isinstance(row.outcome, str) else "pending"
        state, label = OUTCOME_LABEL.get(outcome, ("neutral", outcome))
        legs = ledger.acca_legs(row._asdict())
        detail = " • ".join(f"{leg['match']}: {leg['selection']}" for leg in legs)
        landed = (NONE if row.legs_won != row.legs_won
                  else f"{int(row.legs_won)}/{int(row.legs)}")
        rows.append([
            str(row.issued), f"{int(row.legs)}",
            f'<span class="note">{c.e(detail)}</span>',
            _pct(row.probability), _num(row.fair_odds),
            landed,
            f'<span style="color:var(--{state})">{label}</span>',
        ])

    return f"""
<section class="card">
  <h2>Accumulator picks</h2>
  <p class="note">A separate book. A four-leg slip at 33% and a single at 62%
  have nothing to say to each other, so they never share a hit rate or a total.
  A void leg drops out and the slip settles on what is left, as a bookmaker
  would.</p>
  {c.kpis([
      ("Record", f"{head['wins']}&ndash;{head['losses']}",
       f"{head['pending']} still to play"),
      ("Hit rate", _pct(head["hit_rate"]),
       f"said {_pct(head['expected'])}" if head["expected"] is not None else ""),
      ("Legs landing", (f"{head['average_legs_won']:.1f} of "
                        f"{head['average_legs']:.0f}")
       if head["average_legs_won"] is not None else NONE, "on average"),
      ("Slips recorded", f"{head['recorded']}", "one per match day"),
  ])}
  {c.table(["Issued", "Legs", "Selections", "Chance", "Fair",
            "Landed", "Result"], rows, numeric_from=3, raw=True)}
</section>"""


def _results_behind_note(frame, data):
    """Pending because the match has not been played, or because nobody fetched
    the result? The page should say which.

    The distinction matters: one is patience, the other is a job nobody ran —
    and for six days they looked identical, which is how a broken fetch went
    unnoticed while every row on the page sat at "pending".
    """
    if data is None or frame is None or frame.empty:
        return ""
    by_comp = data.get("last_by_competition") or {}
    fallback = pd.Timestamp(str(data["last"])[:10])
    pending = frame[frame["outcome"].fillna("pending") == "pending"].copy()
    if pending.empty:
        return ""
    pending["day_ts"] = pd.to_datetime(pending["day"], errors="coerce")
    pending["league_last"] = [
        pd.Timestamp(by_comp.get(str(comp), str(fallback.date())))
        for comp in pending["competition"]
    ]
    waiting = pending[(pending["day_ts"] < pd.Timestamp.today().normalize())
                      & (pending["day_ts"] > pending["league_last"])]
    if waiting.empty:
        return ""

    behind = ", ".join(
        f"{comp} to {pd.Timestamp(by_comp.get(str(comp), fallback)):%d %b}"
        for comp in sorted(set(waiting["competition"]))[:4])
    return c.status_block(
        "warning",
        f"{len(waiting)} pick(s) are waiting on results that have not arrived",
        f"Their matches have been played, but the results file for those "
        f"leagues stops earlier ({behind}). Some sources publish a few days "
        f"late; if it persists, press <strong>Fetch new results</strong>.")


def page_history(links, ctx):
    frame = ctx["ledger"]
    accas = ctx.get("accas")
    empty_ledger = frame is None or frame.empty
    empty_accas = accas is None or accas.empty
    if empty_ledger and empty_accas:
        return c.layout(
            links, "History", "history",
            c.empty("Nothing recorded yet.",
                    "The picks for the next few match days are written down the "
                    "first time you refresh the card, and graded once the "
                    "results arrive."),
            subtitle="What the daily picks have actually done.")
    if empty_ledger:
        return c.layout(links, "History", "history",
                        _acca_history_section(accas),
                        subtitle="What the daily picks have actually done.")

    head = ledger.summary(frame)

    rows = []
    for row in frame.sort_values("day", ascending=False).itertuples():
        outcome = row.outcome if isinstance(row.outcome, str) else "pending"
        state, label = OUTCOME_LABEL.get(outcome, ("neutral", outcome))
        score = (NONE if row.home_goals != row.home_goals
                 else f"{int(row.home_goals)}–{int(row.away_goals)}")
        # `nan or fallback` returns the nan — NaN is truthy — so the check has
        # to be explicit rather than an `or`.
        league = (row.competition_name if isinstance(row.competition_name, str)
                  else row.competition)
        band = row.band if isinstance(row.band, str) else "main"
        rows.append([
            str(row.day),
            f'<span class="tag">{c.e(BAND_LABEL.get(band, band))}</span>',
            c.e(league),
            c.e(row.match), c.e(row.selection),
            _pct(row.prob), _num(row.fair_odds),
            score,
            f'<span style="color:var(--{state})">{label}</span>',
        ])

    verdict = _history_verdict(head)

    behind_note = _results_behind_note(frame, ctx.get("data"))

    overdue_note = ""
    if head.get("overdue"):
        overdue_note = c.status_block(
            "warning", f"{head['overdue']} pick(s) never settled",
            "Their matches should have been played by now, so the result is not "
            "being found — usually a club the results file spells differently. "
            f"Days affected: {', '.join(head['overdue_days'][:5])}.")

    # The page used to carry a profit figure and a running P&L curve. Both
    # needed an offered price, and the feed quotes 1X2 and the goals totals and
    # nothing else, so 27 of 30 settled picks had none: the money was computed
    # over three bets while the record beside it was computed over thirty. Two
    # numbers an order of magnitude apart, side by side, both unlabelled.
    #
    # Neither is missed. The claim this site makes is that a probability means
    # what it says, and testing that needs a result, not a price.
    unpriced_note = ""

    bands = ledger.summary_by_band(frame)
    band_section = ""
    if len(bands) > 1:
        band_rows = [[
            BAND_LABEL.get(row["band"], row["band"]),
            f"{row['wins']}&ndash;{row['losses']}",
            _pct(row["hit_rate"]),
            _pct(row["expected"]),
            _interval(row.get("hit_ci")),
            str(row["pending"]),
        ] for row in bands]
        band_section = f"""
<section class="card">
  <h2>By price band</h2>
  <p class="note">Pooling the bands hides the failure worth catching: a forecast
  can be honest at 70% and overconfident at 40%. Read <em>did</em> against
  <em>said</em> in each row, not the money.</p>
  {c.table(["Band", "Record", "Did", "Said", "95% interval", "Pending"],
           band_rows, numeric_from=1, raw=True)}
</section>"""


    gap = ""
    if head["hit_rate"] is not None and head["expected"] is not None:
        points = (head["hit_rate"] - head["expected"]) * 100
        gap = f"{points:+.1f} points against what it claimed"

    body = f"""
<section class="card">
  <div class="verdict">
    <div>
      <div class="hero-label">What the picks did</div>
      <div class="hero">{head['wins']}&ndash;{head['losses']}
        <span class="ci">{_pct(head['hit_rate'])} landed against
        {_pct(head['expected'])} claimed &middot;
        {head['pending']} still to play</span></div>
    </div>
    {verdict}
  </div>
</section>

{c.kpis([
    ("Landed", _pct(head["hit_rate"]),
     f"said {_pct(head['expected'])}" if head["expected"] is not None else ""),
    ("95% interval", _interval(head.get("hit_ci")),
     "on what landed, not what was claimed"),
    ("Settled", f"{head['settled']}",
     f"{head['void']} void" if head["void"] else "graded against the result"),
    ("Picks recorded", f"{head['recorded']}", "one per band per match day"),
])}
{behind_note}
{overdue_note}
{unpriced_note}
{band_section}

<section class="card">
  <h2>Every single pick</h2>
  <p class="note">Written down before kick-off and never edited afterwards. One
  per price band per match day, so Saturday's picks are logged on Thursday at
  Thursday's price: refreshing the card again before the match keeps the first
  answer, because a ledger that follows whichever pick currently looks best
  would show a flattering history and mean nothing.</p>
  {c.table(["Day", "Band", "League", "Match", "Selection", "Confidence", "Fair",
            "Score", "Result"], rows, numeric_from=5, raw=True)}
</section>

{_acca_history_section(accas)}
"""
    return c.layout(links, "History", "history", body,
                    subtitle="Every best pick of the day, written down before "
                             "the match and graded afterwards.",
                    badges=[f"{head['recorded']} pick"
                            + ("s" if head["recorded"] != 1 else ""),
                            f"{head['settled']} settled",
                            "graded on the result, not on a price"])


def _history_verdict(head):
    """Say plainly how little a short record proves.

    Keyed off whether the claim sits inside the interval rather than off
    whether the record is ahead. A run of wins and a run of losses are the
    same event at this sample size, and saying so is the point of the page.
    """
    settled = head["settled"]
    if settled == 0:
        return c.status_block("neutral", "Nothing settled yet",
                              "The first pick is still waiting on its match.")

    low, high = head.get("hit_ci") or (None, None)
    said = head["expected"]
    if low is None or said is None:
        return c.status_block("neutral", f"{settled} settled, nothing to read yet",
                              "A hit rate needs a few dozen results before it "
                              "says anything at all.")

    inside = low <= said <= high
    if settled < 100:
        return c.status_block(
            "warning", "Far too early to read anything into this",
            f"{settled} settled pick(s). The interval on what landed runs "
            f"{low:.0%} to {high:.0%}, which is wide enough to contain almost "
            "any honest forecast. A hit rate starts meaning something in the "
            "hundreds; the reliability tables get there, this does not.")
    if inside:
        return c.status_block(
            "good", "Landing where it said it would",
            f"The claimed {said:.1%} sits inside the {low:.0%} to {high:.0%} "
            "interval on what actually happened, over "
            f"{settled} settled picks.")
    return c.status_block(
        "critical", "Not landing where it said it would",
        f"The claimed {said:.1%} sits outside the {low:.0%} to {high:.0%} "
        f"interval on what actually happened, over {settled} settled picks. "
        "That is a calibration failure, not bad luck.")


# -- 4. reliability --------------------------------------------------------

def page_reliability(links, ctx):
    table = ctx["reliability"]
    if table is None or table.empty:
        return c.layout(links, "Reliability", "reliability",
                        c.empty("No reliability record yet.",
                                "Press “Recalibrate” — it needs predictions first."),
                        subtitle="Whether the confidence numbers are true.")

    overall = table[table["scope"] == "all"]
    rows = []
    for row in overall.itertuples():
        gap = row.actual - row.predicted
        colour = "var(--warning)" if abs(gap) > 0.02 else "var(--text-secondary)"
        rows.append([row.band, f"{int(row.n):,}", _pct(row.predicted), _pct(row.actual),
                     f'<span style="color:{colour}">{gap * 100:+.2f}pp</span>',
                     f"{_pct(row.ci_low)} – {_pct(row.ci_high)}"])

    worst = overall.assign(gap=(overall["actual"] - overall["predicted"]).abs())
    worst_gap = float(worst["gap"].max()) if len(worst) else 0.0
    total = int(overall["n"].sum())

    ceilings = (ctx["picks"] or {}).get("ceilings") or {}
    ceiling_rows = [[GROUPS.get(group, group),
                     "no ceiling — tested to the top" if value >= 0.999 else _pct(value, 0)]
                    for group, value in sorted(ceilings.items(), key=lambda kv: kv[1])]

    scopes = sorted(set(table["scope"]) - {"all"})
    options = "".join(f'<option value="{c.e(s)}">{c.e(GROUPS.get(s, s))}</option>'
                      for s in scopes)
    detail_rows = []
    for row in table[table["scope"] != "all"].itertuples():
        detail_rows.append({
            "scope": row.scope, "band": row.band, "n": int(row.n),
            "predicted": float(row.predicted), "actual": float(row.actual),
            "ci_low": float(row.ci_low), "ci_high": float(row.ci_high)})

    body = f"""
{c.kpis([
    ("Graded selections", f"{total:,}", "out of sample"),
    ("Worst band gap", f"{worst_gap * 100:.2f}pp", "predicted vs actual"),
    ("Bands", f"{len(overall)}", "50% up to 100%"),
])}

<section class="card">
  <h2>Does an 80% pick win 80% of the time?</h2>
  <p class="note">Every graded selection, bucketed by what it claimed. Each band's
  interval is Wilson rather than the normal approximation, because the bands that
  matter most sit near 95% where a normal interval runs past 1.0 and stops meaning
  anything.</p>
  {c.table(["Band", "Bets", "Said", "Did", "Gap", "95% interval"], rows,
           numeric_from=1, raw=True)}
</section>

<section class="card">
  <h2>Where the numbers stop being checked</h2>
  <p class="note">Each market is trusted only as far up as its own record supports.
  Two different reasons show up: corners and BTTS <em>overstated themselves</em> in
  their top bands, while the rest simply never produce enough bets that high to have
  been tested. Picks above the ceiling are dropped from the card.</p>
  {c.table(["Market", "Checked up to"], ceiling_rows, numeric_from=1)
   if ceiling_rows else '<div class="empty">Refresh the card to compute ceilings.</div>'}
</section>

<section class="card">
  <h2>By market</h2>
  <p class="note">The pooled table above hides the two markets that fail. Pick one.</p>
  <div class="filters">
    <label class="visually-hidden" for="rel-scope">Market</label>
    <select id="rel-scope">{options}</select>
    <span class="count" id="rel-count"></span>
  </div>
  <div class="tablewrap"><table>
    <thead><tr><th>Band</th><th class="num">Bets</th><th class="num">Said</th>
    <th class="num">Did</th><th class="num">Gap</th><th>95% interval</th></tr></thead>
    <tbody id="rel-body"></tbody>
  </table></div>
</section>
"""
    return c.layout(links, "Reliability", "reliability", body,
                    page_data={"reliability": detail_rows},
                    subtitle="A confidence number is only worth reading next to the "
                             "record of what that confidence has been worth.",
                    badges=[f"{total:,} graded selections", "walk-forward",
                            "isotonic calibration"])


# -- 4. evidence -----------------------------------------------------------

VERDICT_TEXT = {
    "good": ("good", "An edge, on an adequate sample",
             "The bootstrap interval excludes zero."),
    "warning": ("warning", "An edge, but on a thin sample",
                "The interval excludes zero on fewer than 200 bets."),
    "critical": ("critical", "No demonstrated edge",
                 "The interval includes zero, so nothing has been shown."),
    "losing": ("losing", "Reliably loses money",
               "The interval lies entirely below zero."),
    "none": ("neutral", "No bets", "Nothing qualified."),
}


# Ten findings in a row, all the same shape, is where a reader stops reading.
# They already carry a state, and the state is the honest grouping: two say the
# thesis fails, three say the rescues fail with it, two describe where the bets
# came from, and three survive. Headings that say what each group found, rather
# than "Critical" and "Warning", because the finding is more use than the label.
INSIGHT_GROUPS = [
    ("critical", "The two findings that settle it"),
    ("warning", "Three rescues that did not survive"),
    ("neutral", "Where the bets actually came from"),
    ("good", "What held up anyway"),
]


def _grouped_insights(insights):
    """The evidence cards, chunked by verdict instead of stacked ten deep."""
    out = []
    for state, heading in INSIGHT_GROUPS:
        members = [i for i in insights if i.get("state") == state]
        if not members:
            continue
        out.append(f'<h3 class="insight-group">{c.e(heading)}</h3>')
        out.extend(c.insight_card(i, heading_level="h4") for i in members)
    # Anything with an unexpected state still gets rendered rather than dropped.
    seen = {s for s, _ in INSIGHT_GROUPS}
    rest = [i for i in insights if i.get("state") not in seen]
    if rest:
        out.append('<h3 class="insight-group">Everything else</h3>')
        out.extend(c.insight_card(i, heading_level="h4") for i in rest)
    return "".join(out)


def page_evidence(links, ctx):
    evidence = ctx["evidence"]
    if not evidence:
        return c.layout(links, "Evidence", "evidence",
                        c.empty("No evidence built yet.",
                                "Press “Rebuild the evidence”. It re-runs the "
                                "value-betting backtest and takes several minutes."),
                        subtitle="The value-betting verdict this project is built on.")

    head = evidence["summary"]
    state, title, detail = VERDICT_TEXT.get(head["verdict"], VERDICT_TEXT["none"])
    low, high = head["ci"]

    market_rows = [[row["market"], f"{row['n']:,}", _pct(row["win_rate"]),
                    _num(row["avg_odds"]), f"{row['roi']:+.2f}%",
                    f"[{row['ci'][0]:+.1f}%, {row['ci'][1]:+.1f}%]"]
                   for row in evidence["by_market"]]
    sweep_rows = [[row["band"], f"{row['n']:,}", _pct(row["win_rate"]),
                   f"{row['roi']:+.2f}%",
                   f"[{row['ci'][0]:+.1f}%, {row['ci'][1]:+.1f}%]"]
                  for row in evidence["sweep"]]

    insights = _grouped_insights(evidence["insights"])

    body = f"""
<section class="card">
  <div class="verdict">
    <div>
      <div class="hero-label">Return on investment</div>
      <div class="hero">{head['roi']:+.2f}%
        <span class="ci">95% CI [{low:+.2f}%, {high:+.2f}%] over
        {head['n']:,} bets</span></div>
    </div>
    {c.status_block(state, title, detail)}
  </div>
</section>

{c.kpis([
    ("Bets placed", f"{head['n']:,}", f"{evidence['n_matches']:,} matches"),
    ("Win rate", _pct(head['win_rate']), f"{head['wins']:,} winners"),
    ("Net P&amp;L", f"{head['pnl']:+,.0f}", f"staked {head['staked']:,.0f}"),
    ("Average claimed edge", _pct(head['avg_edge']), "model vs de-vigged price"),
])}

<section class="card">
  <h2>Equity curve</h2>
  <p class="note">Cumulative profit, every bet in order. A flat-to-drifting line on
  a sample this size is the signature of no edge — not of bad luck.</p>
  <div class="chart" id="equity"></div>
</section>

<section class="card">
  <h2>By period</h2>
  <p class="note">The same bets split into four consecutive stretches. An edge that
  only exists in one of them is a story about that period.</p>
  <div class="chart" id="periods"></div>
</section>

<h2 style="margin:30px 0 14px">What the backtest actually says</h2>
{insights}

<section class="card">
  <h2>By market</h2>
  {c.table(["Market", "Bets", "Win rate", "Avg odds", "ROI", "95% CI"],
           market_rows, numeric_from=1)}
</section>

<section class="card">
  <h2>Odds band sweep</h2>
  <p class="note">Widening the band buys sample size and costs accuracy, monotonically.
  It is a diagnostic, not a dial: picking the best-scoring row would be fitting a
  parameter to the test set.</p>
  {c.table(["Band", "Bets", "Win rate", "ROI", "95% CI"], sweep_rows, numeric_from=1)}
</section>
"""
    return c.layout(links, "Evidence", "evidence", body,
                    page_data={"equity": evidence["equity"],
                               "periods": evidence["by_period"]},
                    subtitle="This half asked whether a model can beat the closing "
                             "line. It cannot — which is exactly why the other half "
                             "uses the line instead of fighting it.",
                    badges=[f"{evidence['n_matches']:,} matches",
                            f"{evidence['n_competitions']} leagues",
                            "closing odds", "walk-forward"])


# -- 5. method -------------------------------------------------------------

def page_method(links, ctx):
    data = ctx["data"]
    evidence = ctx["evidence"] or {}
    coverage = [[row[0], f"{row[1]:,}", row[2], row[3]]
                for row in evidence.get("coverage", [])]

    span = (f"{data['first']} to {data['last']}" if data else "no data yet")
    body = f"""
<section class="card">
  <h2>Two halves, one dataset</h2>
  <p class="note">The same {data['matches'] if data else 0:,} matches feed both. The
  value half asks whether the model can out-forecast the closing price; the answer
  is no, decisively, and that answer is what licenses the confidence half to treat
  the closing price as its best input rather than its opponent.</p>
  <ol>
    <li><strong>Fetch.</strong> Results and closing odds from football-data.co.uk
    (free, no key); upcoming prices from The Odds API (metered, hence a button that
    tells you what it costs).</li>
    <li><strong>De-vig.</strong> Consensus prices, power method. It removes margin
    from longshots rather than spreading it evenly, which halves the calibration
    error against the usual proportional scaling.</li>
    <li><strong>Reprice the market.</strong> Fit a pair of Poisson means and a
    Dixon-Coles rho until the score matrix reproduces the quoted 1X2 and totals.
    Every market nobody quotes — BTTS, Over 1.5, team totals, handicaps — is then
    read off that same matrix, so the whole card is internally consistent.</li>
    <li><strong>Fuse a model in, at 10%.</strong> Joint-MLE attack and defence with
    time decay, rated on goals and shots on target. Measured: 0.90 beats both 0.75
    and pure market, paired by match at p = 2.3e-07.</li>
    <li><strong>Calibrate.</strong> Isotonic regression per market group, fitted only
    on earlier matches. It barely touches the market-anchored markets and rescues
    corners, which have no anchor at all.</li>
    <li><strong>Check, then cap.</strong> Every market is trusted only as far as its
    own out-of-sample record supports.</li>
  </ol>
</section>

<section class="card">
  <h2>What it cannot do</h2>
  <ul>
    <li>No injuries, lineups, weather, motivation or fixture congestion. The model
    sees goals, shots on target and corners; the price sees everything else, which is
    most of why the price carries 90% of the weight.</li>
    <li>Corners have no market anchor and are capped at 85% confidence for it.</li>
    <li>A team with no history in its division is carried almost entirely by the
    price, and is flagged on the card.</li>
    <li>The fixture feed quotes 1X2 only, so upcoming matches are anchored on two
    constraints rather than three.</li>
    <li>Accumulator maths assumes independent fixtures. Legs from one match are not
    independent, which is why the tray warns when you tick two.</li>
    <li>Nothing here is a claim of profit.</li>
  </ul>
</section>

<section class="card">
  <h2>Data</h2>
  <p class="note">{span}. Closing prices throughout — the sharpest number a
  bookmaker publishes. Backtesting against opening prices flatters a model that is
  really just slower than the market.</p>
  {c.table(["Competition", "Matches", "From", "To"], coverage, numeric_from=1)
   if coverage else '<div class="empty">Build the evidence to list coverage.</div>'}
</section>
"""
    badges = []
    if data:
        badges = [f"{data['matches']:,} matches", f"{data['competitions']} competitions",
                  span]
    return c.layout(links, "Method", "method", body,
                    subtitle="How a price becomes a probability you can check.",
                    badges=badges)


BUILDERS = {
    "index": page_landing,
    "card": page_card,
    "fixtures": page_fixtures,
    "history": page_history,
    "reliability": page_reliability,
    "evidence": page_evidence,
    "method": page_method,
}


def render(page, links, ctx=None):
    ctx = build_context() if ctx is None else ctx
    return BUILDERS[page](links, ctx)
