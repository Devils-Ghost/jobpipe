"""Bold markup and number helpers."""

from __future__ import annotations

import re
from typing import Any


BOLD = re.compile(r"\*\*(.+?)\*\*")


NUM = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:[.,]\d+)*)")


def plain(text: str) -> str:
    return BOLD.sub(r"\1", text).replace("*", "")


def numbers(text: str) -> set[str]:
    out = set()
    for m in NUM.finditer(plain(text)):
        n = m.group(1).replace(",", "")
        n = n.rstrip(".")
        try:
            out.add(str(float(n)).removesuffix(".0"))
        except ValueError:
            continue
    return out


def parse_bold(text: str) -> list[dict[str, Any]]:
    """'Cut latency by **53%** with **Redis**' -> segments. Adds a final period."""
    text = " ".join(text.split())
    if text and text[-1] not in ".!?":
        text += "."
    segs, pos = [], 0
    for m in BOLD.finditer(text):
        if m.start() > pos:
            segs.append({"t": text[pos : m.start()].replace("*", ""), "b": False})
        segs.append({"t": m.group(1), "b": True})
        pos = m.end()
    if pos < len(text):
        segs.append({"t": text[pos:].replace("*", ""), "b": False})
    return [s for s in segs if s["t"]]
