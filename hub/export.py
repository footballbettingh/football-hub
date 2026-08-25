"""The same five pages, written to disk as files.

This exists so the local server is not the only way to read the thing: the
export opens over `file://`, drops onto a USB stick, and is the exact artefact
GitHub Pages would serve. Because both modes call the same builders with a
different `Links`, a page cannot render correctly in one and be broken in the
other — the only difference is that the static build shows a freshness
snapshot where the server shows buttons.
"""

import shutil
from datetime import date
from pathlib import Path

from . import components, pages
from valuebets import config as vb_config

STATIC_DIR = Path(__file__).resolve().parent / "static"
# The share card lives here rather than in the design folder it was drawn in:
# that folder is deliberately outside the repository, so a CI export could not
# see it and every og:image tag would have pointed at a 404.
ASSETS = ("style.css", "fonts.css", "charts.js", "hub.js", "og-image.png")


def export(out_dir="site"):
    out = Path(out_dir)
    if not out.is_absolute():
        out = vb_config.PROJECT_ROOT / out
    (out / "assets").mkdir(parents=True, exist_ok=True)

    for name in ASSETS:
        shutil.copy(STATIC_DIR / name, out / "assets" / name)
    shutil.copytree(STATIC_DIR / "fonts", out / "assets" / "fonts",
                    dirs_exist_ok=True)
    links = components.Links("static")
    context = pages.build_context()
    for page in pages.BUILDERS:
        (out / f"{page}.html").write_text(pages.render(page, links, context),
                                          encoding="utf-8")

    _write_sitemap(out)
    return out


def _write_sitemap(out):
    """A sitemap and a robots.txt, so the seven pages are findable.

    Every page is rebuilt on the same schedule from the same data, so they all
    carry today's date rather than a per-page one that would be a guess.
    """
    today = date.today().isoformat()
    urls = "".join(
        f"\n  <url><loc>{components.SITE_URL}/"
        f"{'' if page == 'index' else page + '.html'}</loc>"
        f"<lastmod>{today}</lastmod></url>"
        for page, _, _ in components.PAGES)
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{urls}\n</urlset>\n", encoding="utf-8")
    (out / "robots.txt").write_text(
        "User-agent: *\nAllow: /\n"
        f"Sitemap: {components.SITE_URL}/sitemap.xml\n", encoding="utf-8")
