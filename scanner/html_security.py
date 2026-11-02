"""Preserve JavaScript escapes and authorize only known inline script bytes."""
from __future__ import annotations

import base64
import hashlib
import re


def inline_script(source: str) -> str:
    # HTML closes script elements case-insensitively, even inside JS strings.
    return re.sub(r"</script", lambda _match: r"<\/script", source, flags=re.I)


def authorize_inline_scripts(html: str) -> str:
    hashes = []
    for match in re.finditer(r"<script\b[^>]*>(.*?)</script\s*>", html, re.I | re.S):
        digest = hashlib.sha256(match.group(1).encode("utf-8")).digest()
        hashes.append("'sha256-" + base64.b64encode(digest).decode("ascii") + "'")
    if hashes:
        html = html.replace("script-src 'self';", "script-src 'self' " + " ".join(hashes) + ";", 1)
    return html
