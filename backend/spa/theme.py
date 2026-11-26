"""What the Django-rendered pages wear: the SPA's own stylesheet and the brand colour.

The apex landing and the legal pages render without the SPA, but they link the stylesheet it is
built with, so frontend/src/theme.css dresses them too and stays the one file for the look. Their
templates lay themselves out with Tailwind's utility classes on the theme's tokens
(`bg-primary`, `text-muted-foreground`, …): the build scans backend/spa/templates (`@source` in
frontend/src/index.css). The colour is the site owner's, inlined as `--brand` on <html>, where
the SPA puts it too.
"""

import json
from pathlib import Path

from django.conf import settings

from spa.portfolio import brand_for

# Without a build (a checkout, `./dev`) Vite's dev server answers a stylesheet link to the source
# with the compiled CSS. The pages reach it through Vite's proxy on :5173, like the SPA.
DEV_STYLESHEET = "/src/index.css"


def stylesheets() -> list[str]:
    """The CSS files the build's manifest lists for the SPA's entry, or the dev path when there's
    no manifest. Read on each render: it's a small file, and a deploy restarts the process."""
    try:
        manifest = json.loads(Path(settings.SPA_MANIFEST).read_text())
    except (OSError, ValueError):
        return [DEV_STYLESHEET]
    return [f"/{css}" for css in manifest.get("index.html", {}).get("css", [])]


def theme_context() -> dict:
    """The two template variables every Django-rendered page's <head> reads."""
    return {"brand": brand_for(None), "stylesheets": stylesheets()}
