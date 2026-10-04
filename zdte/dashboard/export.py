"""Export the dashboard as standalone HTML with the current state baked in.

The exported pages need no server: open them from a phone, attach them to
a message, or host them anywhere static. They render the snapshot they
were exported with and do not poll.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from zdte.dashboard.server import CITY_PATH, HTML_PATH

PAGES = {"desk": HTML_PATH, "city": CITY_PATH}


def _inject(html: str, state: dict, links: dict[str, str]) -> str:
    payload = json.dumps(state).replace("</", "<\\/")
    tag = (f"<script>window.__ZDTE_STATE__ = {payload};"
           f"window.__ZDTE_LINKS__ = {json.dumps(links)};</script>\n")
    # before the first script so the page sees the snapshot when it boots
    idx = html.find("<script>")
    return html[:idx] + tag + html[idx:] if idx >= 0 else html + tag


def _as_fragment(html: str) -> str:
    """Strip the document skeleton (for hosts that wrap pages themselves)."""
    html = re.sub(r"(?is)^.*?<head[^>]*>", "", html)
    html = re.sub(r"(?is)</head>\s*<body[^>]*>", "", html)
    html = re.sub(r"(?is)</body>\s*</html>\s*$", "", html)
    html = re.sub(r'(?i)<meta charset="utf-8">\s*', "", html)
    html = re.sub(r'(?i)<meta name="viewport"[^>]*>\s*', "", html)
    return html.strip() + "\n"


def export_snapshot(state: dict, out_dir: str | Path, fragment_pages: tuple[str, ...] = ()) -> dict[str, Path]:
    """Write ``desk.html`` and ``city.html`` into ``out_dir``; return their paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    links = {name: f"{name}.html" for name in PAGES}
    written: dict[str, Path] = {}
    for name, src in PAGES.items():
        html = _inject(src.read_text(), state, links)
        if name in fragment_pages:
            html = _as_fragment(html)
        path = out_dir / f"{name}.html"
        path.write_text(html)
        written[name] = path
    return written
