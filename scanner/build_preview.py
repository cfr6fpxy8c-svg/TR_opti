"""Build frontend/preview.html — the dashboard as ONE self-contained file.

Inlines styles.css and app.js into index.html so the dashboard can be viewed
from any sandboxed/file context without external resource loading. The
canonical multi-file version (index.html + styles.css + app.js) remains the
deployed artifact for Cloudflare Pages; this is regenerated on every scan.

    python -m scanner.build_preview
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .config import repo_root


def build(frontend_dir: Optional[Path] = None) -> None:
    """Rebuild preview.html.

    ``frontend_dir`` is injectable so tests (and any caller working on a
    scratch tree) never write into the checked-out repository.
    """
    fe = Path(frontend_dir) if frontend_dir else repo_root() / "frontend"
    html = (fe / "index.html").read_text(encoding="utf-8")
    css = (fe / "styles.css").read_text(encoding="utf-8")
    js = (fe / "app.js").read_text(encoding="utf-8")

    html = re.sub(
        r'<link rel="stylesheet" href="styles.css">',
        "<style>\n" + css.replace("</", "<\\/") + "\n</style>",
        html, count=1)
    html = re.sub(
        r'<script src="app\.js"></script>',
        "<script>\n" + js.replace("</script>", "<\\/script>") + "\n</script>",
        html, count=1)
    (fe / "preview.html").write_text(html, encoding="utf-8")
    print(f"preview.html built ({len(html) // 1024} KB)")


if __name__ == "__main__":
    build()
