"""Bullet text segments: [{"t": text, "b": bold, "link"?: url}] as the template expects."""

from __future__ import annotations

import re
from typing import Any

from jobpipe.config import settings

Seg = dict[str, Any]


def linkify(segs: list[Seg], links: dict[str, str] | None = None) -> list[Seg]:
    """Split segments so every occurrence of a configured phrase (settings.links, e.g.
    "pwn.college") becomes its own link segment. Case-sensitive, whole-phrase match."""
    links = settings().get("links", {}) if links is None else links
    if not links:
        return segs
    pattern = re.compile("|".join(re.escape(k) for k in sorted(links, key=len, reverse=True)))
    out: list[Seg] = []
    for seg in segs:
        if seg.get("link"):
            out.append(seg)
            continue
        pos = 0
        for m in pattern.finditer(seg["t"]):
            if m.start() > pos:
                out.append({**seg, "t": seg["t"][pos : m.start()]})
            out.append({"t": m.group(0), "b": seg["b"], "link": links[m.group(0)]})
            pos = m.end()
        if pos < len(seg["t"]):
            out.append({**seg, "t": seg["t"][pos:]})
    return out
