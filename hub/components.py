"""Reusable HTML fragments. Pure functions, no I/O — easy to unit test.

Adapted from the value-betting project's site components, with one addition
that matters: `Links`. The same builders have to produce a page that works
behind the local server (`/fixtures`) and a page that works as a file on disk
or on GitHub Pages (`fixtures.html`). Everything routes through `links.href`
so there is no second copy of the markup to keep in step.
"""

import html
import json
import os
from datetime import datetime
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"

# The identity sheet's wordmark, used wherever the site names itself.
SITE_NAME = "Football Betting Hub"

# Where the pages actually live once published. Canonical links and share cards
# have to be absolute, and they have to point at the published address rather
# than at whichever host is rendering them — a card scraped off localhost still
# belongs to the Pages site. The default is the repository's Pages URL; set
# SITE_URL to move the site behind a custom domain without touching this file.
SITE_URL = os.environ.get(
    "SITE_URL", "https://footballbettingh.github.io/football-hub").rstrip("/")

SITE_TAGLINE = ("Calibrated football match probabilities, written down before "
                "kick-off and graded afterwards.")

# 512 square, so the share card is the small-summary kind. Claiming
# `summary_large_image` with a square logo gets it letterboxed or cropped.
OG_IMAGE = "og-image.png"

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
    return (f'<a class="brand" href="{href}" aria-label="Football Betting Hub, home">'
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
    """Where a page link points: `/fixtures` under the server, `fixtures.html`
    on disk and on Pages.

    The site is read-only. Nothing on a page rebuilds anything; every job that
    writes an artifact is a command in the README, run from a terminal where
    its output and its exit code are visible.
    """

    def __init__(self, mode="server"):
        if mode not in ("server", "static"):
            raise ValueError(f"unknown link mode {mode!r}")
        self.mode = mode

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
        # Fonts are downloaded, never hand-edited, and the <link rel=preload>
        # in the head has to ask for the byte-identical URL that fonts.css
        # asks for — a stamp on one and not the other fetches the file twice.
        if name.startswith("fonts/"):
            return f"/assets/{name}"
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

    # Both the tab and the share card. `subtitle` is the one-line description
    # each builder already writes for the page header, so there is no second
    # copy of the same sentence to drift out of step.
    full_title = e(title) if title == SITE_NAME else f"{e(title)} · {SITE_NAME}"
    description = e(subtitle) if subtitle else SITE_TAGLINE
    canonical = f"{SITE_URL}/" if current == "index" else f"{SITE_URL}/{current}.html"

    data_script = ""
    if page_data is not None:
        # </script> inside a JSON string would end the block early; escaping the
        # slash is the standard fix and stays valid JSON.
        blob = json.dumps(page_data).replace("</", "<\\/")
        data_script = f"<script>window.__PAGE__ = {blob};</script>"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{full_title}</title>
<meta name="description" content="{description}">
<meta name="theme-color" content="#0d211c">
<link rel="canonical" href="{canonical}">
<link rel="icon" href="{FAVICON_DATA_URI}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:title" content="{full_title}">
<meta property="og:description" content="{description}">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{SITE_URL}/assets/{OG_IMAGE}">
<meta property="og:image:width" content="512">
<meta property="og:image:height" content="512">
<meta property="og:image:alt" content="The {SITE_NAME} mark: a filled 1 beside an outlined X and 2.">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{full_title}">
<meta name="twitter:description" content="{description}">
<meta name="twitter:image" content="{SITE_URL}/assets/{OG_IMAGE}">
<link rel="preload" href="{links.asset('fonts/space-grotesk-variable-latin.woff2')}" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="{links.asset('fonts/ibm-plex-mono-400-latin.woff2')}" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="{links.asset('fonts.css')}">
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
