"""The same five pages, written to disk as files.

This exists so the local server is not the only way to read the thing: the
export opens over `file://`, drops onto a USB stick, and is the exact artefact
GitHub Pages would serve. Because both modes call the same builders with a
different `Links`, a page cannot render correctly in one and be broken in the
other — the only difference is that the static build shows a freshness
snapshot where the server shows buttons.
"""

import shutil
from pathlib import Path

from . import artifacts, components, pages
from valuebets import config as vb_config

STATIC_DIR = Path(__file__).resolve().parent / "static"
ASSETS = ("style.css", "charts.js", "hub.js")


def export(out_dir="site"):
    out = Path(out_dir)
    if not out.is_absolute():
        out = vb_config.PROJECT_ROOT / out
    (out / "assets").mkdir(parents=True, exist_ok=True)

    for name in ASSETS:
        shutil.copy(STATIC_DIR / name, out / "assets" / name)

    links = components.Links("static")
    links.control_html = components.control_strip(links, artifacts.status(), [])

    context = pages.build_context()
    for page in pages.BUILDERS:
        (out / f"{page}.html").write_text(pages.render(page, links, context),
                                          encoding="utf-8")
    return out
