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

# The one place the site points off itself: the Telegram channel the daily pick
# is posted to.
TELEGRAM_URL = "https://t.me/football_betting_hub"

# Filled rather than stroked like the ICONS below, because this one is a brand
# mark and not a status glyph: it is recognised by its silhouette, and at the
# 14px the footer gives it a 1.8-wide outline would close up into a smudge.
TELEGRAM_ICON = (
    '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">'
    '<path d="M9.417 15.181l-.397 5.584c.568 0 .814-.244 1.109-.537l2.663-2.545'
    ' 5.518 4.041c1.012.564 1.725.267 1.998-.931l3.622-16.972.001-.001c.321-1.496'
    '-.541-2.081-1.527-1.714l-21.29 8.151c-1.453.564-1.431 1.374-.247 1.741l5.443'
    ' 1.693 12.643-7.911c.595-.394 1.136-.176.691.218z"/></svg>')

# Where the project can be supported. A plain link: it was Ko-fi's overlay
# widget, a script from their CDN on every page — the one piece of the site run
# from somewhere else, able to read anything the page could and to change it,
# and a floating button over the bottom of every table on a phone.
KOFI_URL = "https://ko-fi.com/footballbettinghub"

# Stroked like the status icons rather than filled like the Telegram mark: it
# is a cup, not a brand, and at 14px an outline reads as one.
KOFI_ICON = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M4 8h13v6a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5z"/>'
    '<path d="M17 10h1.5a2.5 2.5 0 0 1 0 5H17"/><path d="M8 3v2M12 3v2"/></svg>')

# What a page may load, and from where: its own scripts, styles and fonts and
# nothing else. Possible since the page data stopped being a script and the
# Ko-fi widget became a link — there is no inline script left to allow and no
# third party to trust. Styles keep 'unsafe-inline' for the style attributes
# the tables colour their cells with. `file:` is for the export opened from
# disk, where some browsers do not count a file as 'self'; a published page
# can load nothing from a file: URL whatever this says.
CONTENT_SECURITY_POLICY = (
    "default-src 'self' file:; script-src 'self' file:; "
    "style-src 'self' file: 'unsafe-inline'; img-src 'self' file: data:; "
    "font-src 'self' file:; connect-src 'none'; object-src 'none'; "
    "base-uri 'none'; form-action 'none'")

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


# The phone menu's button: three bars closed, a cross open. Two drawings
# swapped by `aria-expanded` rather than one animated into the other, so the
# state the button announces and the state it shows cannot disagree.
MENU_ICON = (
    '<svg class="i-open" viewBox="0 0 24 24" aria-hidden="true" fill="none" '
    'stroke="currentColor" stroke-width="1.8" stroke-linecap="round">'
    '<path d="M4 7h16M4 12h16M4 17h16"/></svg>'
    '<svg class="i-close" viewBox="0 0 24 24" aria-hidden="true" fill="none" '
    'stroke="currentColor" stroke-width="1.8" stroke-linecap="round">'
    '<path d="M6 6l12 12M18 6L6 18"/></svg>')

# The menu only opens with a script. Without one it is laid out under the bar
# instead, so a phone that blocks scripts still has every page one tap away.
MENU_NOSCRIPT = (".menu-toggle{display:none!important}"
                 "@media (max-width:900px){.topbar .inner{flex-wrap:wrap}"
                 ".menu{display:flex!important;position:static!important;"
                 "flex-basis:100%;box-shadow:none!important;border:0!important}}")


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
    # The theme button names the theme it switches to. On a phone it is a row
    # in the menu, where a bare "Dark" reads as the theme you are already in.
    theme = ('<button class="theme" id="theme" type="button">'
             '<span class="theme-long">Switch to </span>'
             '<span id="theme-label">Dark</span>'
             '<span class="theme-long"> theme</span></button>')

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
        # Data, not code: a JSON block the browser never runs, read with
        # JSON.parse. It used to be a script assigning the data, which a
        # stray sequence in a team name could have turned into a script of
        # its own. `<`, `>` and `&` are written as \u escapes — still the same
        # JSON, and with no `<` in the block nothing in a string can end it
        # or open a comment inside it.
        blob = (json.dumps(page_data).replace("<", "\\u003c")
                .replace(">", "\\u003e").replace("&", "\\u0026"))
        data_script = f'<script type="application/json" id="page-data">{blob}</script>'

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{CONTENT_SECURITY_POLICY}">
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
<noscript><style>{MENU_NOSCRIPT}</style></noscript>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<div class="topbar"><div class="inner">
  {brandmark(links.href('index'))}
  <button class="menu-toggle" id="menu-toggle" type="button" aria-expanded="false"
    aria-controls="site-menu" aria-label="Menu">{MENU_ICON}</button>
  <div class="menu" id="site-menu">
    <nav class="main" aria-label="Pages">{nav}</nav>
    {theme}
  </div>
</div></div>

<div class="wrap">
  <main id="main">
  {pagehead}
  {body_html}
  </main>
  <footer class="site">
    <p>Built {datetime.now():%d %b %Y, %H:%M} from local data. Probabilities are
    anchored to <em>de-vigged</em> bookmaker prices — the current line on the card,
    the closing one in everything it was checked against: raw <code>1/odds</code>
    sums to about 1.07, and counting that margin as information is the easiest way
    to fool yourself.</p>
    <p>A research tool, not betting advice. A calibrated probability says how
    often something happens — not whether the price on offer is worth taking.</p>
    <p class="ext"><a href="{TELEGRAM_URL}" rel="noopener">{TELEGRAM_ICON}<span>Telegram
    channel</span></a> — the daily pick, posted before kick-off.</p>
    <p class="ext"><a href="{KOFI_URL}" rel="noopener">{KOFI_ICON}<span>Support it on
    Ko-fi</span></a> — if the record is worth keeping.</p>
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


def table(columns, rows, numeric_from=None, classes="", raw=False,
          per_page=None, pages_label="Pages", roles=None, blank=None):
    """Static table. `numeric_from` right-aligns columns at that index onward.

    `raw=True` trusts the cells to be HTML already — used where a cell carries
    a coloured span. Everything else is escaped.

    `per_page` splits a table that only ever grows into pages of that many
    rows, in the order given, with a pager under it. Every row is still on the
    page; see `pager`.

    `roles` gives each column a part in the stacked layout a phone gets instead
    of the grid — see `ROLES`. A role of `None` leaves the column out of it. A
    cell equal to `blank`, the page's "no number here" mark, is dropped from
    that layout too: a labelled dash is noise where a missing column was not.
    """
    def cls(i, value=None):
        names = ["num"] if numeric_from is not None and i >= numeric_from else []
        if roles:
            role = roles[i]
            names += [ROLES[part] for part in role.split()] if role else ["s-hide"]
            if blank is not None and value == blank:
                names.append("s-hide")
        label = (f' data-label="{e(columns[i])}"'
                 if value is not None and roles and roles[i] and "label" in roles[i]
                 else "")
        return (f' class="{" ".join(names)}"' if names else "") + label

    def cell(value):
        return value if raw else e(value)

    if roles:
        classes = f"{classes} stack".strip()

    paged = per_page is not None and len(rows) > per_page

    def page_of(index):
        if not paged:
            return ""
        page = index // per_page
        return f' data-page="{page}"' + (" hidden" if page else "")

    head = "".join(f"<th{cls(i)}>{e(c)}</th>" for i, c in enumerate(columns))
    body = "".join(
        f"<tr{page_of(n)}>"
        + "".join(f"<td{cls(i, c)}>{cell(c)}</td>" for i, c in enumerate(row)) + "</tr>"
        for n, row in enumerate(rows))
    html = (f'<div class="tablewrap"><table class="{classes}"><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")
    if not paged:
        return html
    count = -(-len(rows) // per_page)
    numbers = [(str(n), str(n + 1)) for n in range(count)]
    return (f'<div class="paged">{html}'
            f'{pager(numbers, pages_label, collapse=True)}</div>')


# A table too wide for a phone is not scrolled sideways there: each row becomes
# a small block. The title leads with its headline figure opposite, the
# subtitle sits under it with a second figure opposite that, and everything
# else runs along a line of small print below. `label` prints the column's
# name in front of a figure, which a row out of its grid otherwise loses.
# `block` is a full-width part after the small print.
ROLES = {"title": "s-title", "end": "s-end", "sub": "s-sub", "end2": "s-end2",
         "meta": "s-meta", "block": "s-block", "label": "s-label"}


def pager(pages, label="Pages", collapse=False):
    """One button per page of a table whose rows carry `data-page`.

    `pages` is (key, label) pairs, the label already HTML. Every row is on the
    page with all but the first page hidden, and this is drawn in that state,
    so the file reads correctly before hub.js runs; the script only moves
    between pages. Moving is not a trip to the server, because the static
    export has none.

    `collapse` lets a long run of numbered pages fold down to the ends and
    the neighbours of the current one. Named pages, like the days of the
    fixture list, are few, and each one is worth seeing.
    """
    buttons = []
    for index, (key, text) in enumerate(pages):
        current = ' aria-current="page"' if index == 0 else ""
        buttons.append(f'<button type="button" data-page="{e(key)}"{current}>'
                       f'{text}</button>')
    last = " disabled" if len(pages) < 2 else ""
    folds = " data-collapse" if collapse else ""
    return (f'<nav class="pager" aria-label="{e(label)}"{folds}>'
            '<button type="button" data-step="-1" aria-label="Previous page" '
            'disabled>&lsaquo;</button>'
            f'{"".join(buttons)}'
            f'<button type="button" data-step="1" aria-label="Next page"{last}>'
            '&rsaquo;</button></nav>')


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
