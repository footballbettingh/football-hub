"""Reusable HTML fragments. Pure functions, no I/O — easy to unit test.

Adapted from the value-betting project's site components, with one addition
that matters: `Links`. The same builders have to produce a page that works
behind the local server (`/fixtures`) and a page that works as a file on disk
or on GitHub Pages (`fixtures.html`). Everything routes through `links.href`
so there is no second copy of the markup to keep in step.
"""

import html
import json
from datetime import datetime
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"

# The identity sheet's wordmark, used wherever the site names itself.
SITE_NAME = "Football Betting Hub"

PAGES = [
    ("index", "Home", "What this is, and what it has been worth"),
    ("card", "Card", "The selections most likely to land"),
    ("fixtures", "Fixtures", "Every upcoming match, market by market"),
    ("history", "History", "What the daily pick has actually done"),
    ("reliability", "Reliability", "Whether the confidence numbers are true"),
    ("evidence", "Evidence", "The value-betting verdict this is built on"),
    ("method", "Method", "How it works, and what it cannot do"),
]

ICONS = {
    "good": '<svg class="icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" '
            'stroke-width="1.8"><circle cx="10" cy="10" r="8"/>'
            '<path d="M6.2 10.3l2.6 2.6 5-5.4"/></svg>',
    "warning": '<svg class="icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" '
               'stroke-width="1.8"><path d="M10 2.6L18.5 17H1.5z"/><path d="M10 8v4"/>'
               '<circle cx="10" cy="14.6" r=".9" fill="currentColor" stroke="none"/></svg>',
    "critical": '<svg class="icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" '
                'stroke-width="1.8"><circle cx="10" cy="10" r="8"/>'
                '<path d="M7 7l6 6M13 7l-6 6"/></svg>',
    "losing": '<svg class="icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" '
              'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
              '<path d="M2.5 5.5l5.5 5.5 3-3 6.5 6.5"/><path d="M17.5 10v4.5H13"/></svg>',
    "neutral": '<svg class="icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" '
               'stroke-width="1.8"><circle cx="10" cy="10" r="8"/><path d="M10 9v5"/>'
               '<circle cx="10" cy="6.2" r=".9" fill="currentColor" stroke="none"/></svg>',
}


# -- the brand -------------------------------------------------------------

# The identity sheet draws the mark as three cells — 1 filled, X and 2 outlined
# — under a stacked FOOTBALL / BETTING HUB lockup. Reproduced here rather than
# exported as an image so it inherits the page's own colours in both themes and
# stays sharp at any zoom.
LOGOMARK = (
    '<svg class="logomark" viewBox="0 0 78 32" aria-hidden="true" focusable="false">'
    '<rect x="0.9" y="0.9" width="22" height="30" rx="5" fill="var(--brand)"/>'
    '<text x="11.9" y="22.5" text-anchor="middle" font-size="17" font-weight="700"'
    ' fill="var(--surface-1)">1</text>'
    '<rect x="27.9" y="0.9" width="22" height="30" rx="5" fill="none"'
    ' stroke="currentColor" stroke-opacity="0.35" stroke-width="1.8"/>'
    '<text x="38.9" y="22.5" text-anchor="middle" font-size="17" font-weight="700"'
    ' fill="currentColor">X</text>'
    '<rect x="54.9" y="0.9" width="22" height="30" rx="5" fill="none"'
    ' stroke="currentColor" stroke-opacity="0.35" stroke-width="1.8"/>'
    '<text x="65.9" y="22.5" text-anchor="middle" font-size="17" font-weight="700"'
    ' fill="currentColor">2</text></svg>')

FAVICON_DATA_URI = "data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20viewBox%3D%220%200%20100%20100%22%3E%3Ccircle%20cx%3D%2250%22%20cy%3D%2250%22%20r%3D%2250%22%20fill%3D%22oklch%280.2%200.035%20165%29%22%2F%3E%3Crect%20x%3D%2214%22%20y%3D%2230%22%20width%3D%2226%22%20height%3D%2240%22%20rx%3D%226%22%20fill%3D%22oklch%280.68%200.14%20158%29%22%2F%3E%3Crect%20x%3D%2246%22%20y%3D%2230%22%20width%3D%2226%22%20height%3D%2240%22%20rx%3D%226%22%20fill%3D%22none%22%20stroke%3D%22oklch%280.64%200.025%20165%29%22%20stroke-width%3D%224%22%2F%3E%3C%2Fsvg%3E"


def brandmark(href):
    """The full lockup: mark plus the two-line wordmark."""
    return (f'<a class="brand" href="{href}" aria-label="Football Betting Hub — home">'
            f'{LOGOMARK}'
            '<span class="wordmark"><span class="w1">FOOTBALL</span>'
            '<span class="w2">BETTING HUB</span></span></a>')


def e(x):
    return html.escape(str(x))


def _asset_stamp(name):
    try:
        return int((STATIC_DIR / name).stat().st_mtime)
    except OSError:
        return 0


class Links:
    """Where a page link points, and whether the action buttons exist at all.

    A static export has no server to POST to, so the control strip is omitted
    rather than rendered dead — a button that silently does nothing is worse
    than no button.
    """

    def __init__(self, mode="server", control_html=""):
        if mode not in ("server", "static"):
            raise ValueError(f"unknown link mode {mode!r}")
        self.mode = mode
        # The control strip belongs to the delivery mode, not to any one page,
        # so it rides along here instead of being threaded through all five
        # builders and forgotten in one of them.
        self.control_html = control_html

    @property
    def interactive(self):
        return self.mode == "server"

    def href(self, page):
        if self.mode == "static":
            return f"{page}.html"
        return "/" if page == "index" else f"/{page}"

    def asset(self, name):
        if self.mode == "static":
            # No query string: a static export may be opened over file://,
            # where some browsers refuse a URL with one.
            return f"assets/{name}"
        # Served assets carry the file's own timestamp, so editing the CSS and
        # reloading actually shows the new CSS. Without it the browser holds
        # the cached copy for an hour and the page looks unchanged — which
        # reads as "my edit did nothing" rather than "the browser cached it".
        stamp = _asset_stamp(name)
        return f"/assets/{name}?v={stamp}" if stamp else f"/assets/{name}"


def layout(links, title, current, body_html, page_data=None, subtitle="",
           badges=(), show_head=True):
    """Full page shell: topbar, nav, header, body, footer.

    `show_head=False` drops the standard page heading for a page that brings
    its own — the landing hero is an <h1>, and a second one above it would be
    both a duplicate heading and a smaller title sitting on top of a bigger one.
    """
    def nav_link(page, label):
        mark = ' aria-current="page"' if page == current else ""
        return '<a href="%s"%s>%s</a>' % (links.href(page), mark, e(label))

    nav = "".join(nav_link(page, label) for page, label, _ in PAGES)

    badge_html = ""
    if badges:
        badge_html = ('<div class="badges">'
                      + "".join(f'<span class="badge">{e(b)}</span>' for b in badges)
                      + "</div>")

    pagehead = ""
    if show_head:
        pagehead = (f'<div class="pagehead"><h1>{e(title)}</h1>'
                    + (f"<p>{subtitle}</p>" if subtitle else "")
                    + badge_html + "</div>")

    data_script = ""
    if page_data is not None:
        # </script> inside a JSON string would end the block early; escaping the
        # slash is the standard fix and stays valid JSON.
        blob = json.dumps(page_data).replace("</", "<\\/")
        data_script = f"<script>window.__PAGE__ = {blob};</script>"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title) if title == SITE_NAME else f"{e(title)} — {SITE_NAME}"}</title>
<meta name="description" content="{e(subtitle) if subtitle else 'Calibrated football match probabilities, written down before kick-off and graded afterwards.'}">
<meta name="theme-color" content="#0d211c">
<link rel="icon" href="{FAVICON_DATA_URI}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{links.asset('style.css')}">
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<div class="topbar"><div class="inner">
  {brandmark(links.href('index'))}
  <nav class="main">{nav}</nav>
  <button class="theme" id="theme">Dark</button>
</div></div>

<div class="wrap">
  {links.control_html}
  <main id="main">
  {pagehead}
  {body_html}
  </main>
  <footer class="site">
    <p>Built {datetime.now():%d %b %Y, %H:%M} from local data. Probabilities are
    anchored to <em>de-vigged</em> closing prices: raw <code>1/odds</code> sums to
    about 1.07, and counting that margin as information is the easiest way to
    fool yourself.</p>
    <p>A research tool, not betting advice. A calibrated probability says how
    often something happens — not whether the price on offer is worth taking.</p>
  </footer>
</div>
{data_script}
<script src="{links.asset('charts.js')}"></script>
<script src="{links.asset('hub.js')}"></script>
</body></html>"""


def status_block(state, title, detail):
    return (f'<div class="status" style="color:var(--{state})">{ICONS[state]}'
            f'<div><div class="t">{e(title)}</div>'
            f'<div class="d">{detail}</div></div></div>')


def kpis(cells):
    """cells: iterable of (label, value, meta). Values may contain entities."""
    return ('<div class="kpis">' + "".join(
        f'<div class="kpi"><div class="k">{k}</div><div class="v">{v}</div>'
        f'<div class="m">{m}</div></div>' for k, v, m in cells) + "</div>")


def table(columns, rows, numeric_from=None, classes="", raw=False):
    """Static table. `numeric_from` right-aligns columns at that index onward.

    `raw=True` trusts the cells to be HTML already — used where a cell carries
    a coloured span. Everything else is escaped.
    """
    def cls(i):
        return ' class="num"' if numeric_from is not None and i >= numeric_from else ""

    def cell(value):
        return value if raw else e(value)

    head = "".join(f"<th{cls(i)}>{e(c)}</th>" for i, c in enumerate(columns))
    body = "".join(
        "<tr>" + "".join(f"<td{cls(i)}>{cell(c)}</td>" for i, c in enumerate(row)) + "</tr>"
        for row in rows)
    return (f'<div class="tablewrap"><table class="{classes}"><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


def insight_card(insight, heading_level="h3"):
    evidence = insight.get("evidence") or {}
    evidence_html = ""
    if evidence.get("type") == "table":
        evidence_html = table(evidence["columns"], evidence["rows"], numeric_from=1)
    return f"""
<section class="card insight" style="--state: var(--{insight['state']})" id="{e(insight['id'])}">
  <div class="top">
    {ICONS[insight['state']]}
    <div>
      <{heading_level}>{e(insight['title'])}</{heading_level}>
      <div class="headline">{e(insight['headline'])}</div>
    </div>
    <span class="stat">{e(insight['stat'])}</span>
  </div>
  <p class="detail">{insight['detail']}</p>
  {evidence_html}
</section>"""


def next_links(links, items):
    """items: iterable of (page, label, hint)."""
    return ('<div class="next">' + "".join(
        f'<a href="{links.href(page)}">{e(label)}<span>{e(hint)}</span></a>'
        for page, label, hint in items) + "</div>")


def empty(message, hint=""):
    hint_html = f'<div class="hint">{e(hint)}</div>' if hint else ""
    return f'<div class="card"><div class="empty">{e(message)}{hint_html}</div></div>'


# -- the control strip -----------------------------------------------------

def control_strip(links, status_rows, jobs, busy=None):
    """Freshness of every artifact, plus the buttons that rebuild them.

    Static exports get a plain freshness line instead: the data is a snapshot
    of whenever the export ran, and pretending otherwise would be a lie the
    page cannot back up.
    """
    chips = []
    for row in status_rows:
        if not row["exists"]:
            state, detail = "missing", "never built"
        elif row["stale_after"]:
            state, detail = "stale", f"behind {row['stale_after'][0].lower()}"
        else:
            state, detail = "fresh", row["age"]
        chips.append(
            f'<div class="chip {state}"><span class="k">{e(row["label"])}</span>'
            f'<span class="v">{e(detail)}</span></div>')
    chip_html = '<div class="chips">' + "".join(chips) + "</div>"

    if not links.interactive:
        return (f'<div class="control static">{chip_html}'
                '<p class="note">Snapshot. Run the local server to rebuild anything.</p>'
                "</div>")

    buttons = []
    for job in jobs:
        note = job.cost or job.estimate
        classes = "danger" if job.cost else ""
        buttons.append(
            f'<button class="run {classes}" data-job="{e(job.key)}" '
            f'title="{e(job.description)}">{e(job.label)}'
            f'<span class="est">{e(note)}</span></button>')

    running = f'<div class="running" id="job-running">{e(busy)}</div>' if busy else ""
    return f"""<div class="control" id="control">
  {chip_html}
  <div class="actions">{''.join(buttons)}</div>
  {running}
  <pre class="joblog" id="joblog" hidden></pre>
</div>"""
