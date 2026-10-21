"""Formatting adjustments used to fit one page."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from docx import Document
from docx.oxml.ns import qn


TWIPS_PER_INCH = 1440


@dataclass(frozen=True)
class Adjust:
    zero_spacing: bool = False
    line_spacing: float | None = None
    margin_in: float | None = None
    font_pt: float | None = None

    def describe(self) -> str:
        parts = []
        if self.zero_spacing:
            parts.append("spacing=0")
        if self.line_spacing is not None:
            parts.append(f"line={self.line_spacing}")
        if self.margin_in is not None:
            parts.append(f"margins<={self.margin_in}in")
        if self.font_pt is not None:
            parts.append(f"body_font={self.font_pt}pt")
        return ", ".join(parts) or "none"


def body_font_pt(doc) -> float:
    """Most common run size in the document body, in points."""
    sizes = Counter(
        int(sz.get(qn("w:val")))
        for sz in doc.element.body.iter(qn("w:sz"))
        if sz.getparent().getparent().tag == qn("w:r")
    )
    return sizes.most_common(1)[0][0] / 2 if sizes else 11.0


def max_margin_in(doc) -> float:
    vals = [
        int(m.get(qn(f"w:{side}"), 0))
        for m in doc.element.body.iter(qn("w:pgMar"))
        for side in ("top", "right", "bottom", "left")
    ]
    return max(vals) / TWIPS_PER_INCH if vals else 0.0


def max_line_spacing(doc) -> float:
    vals = [
        int(s.get(qn("w:line"))) / 240
        for s in doc.element.body.iter(qn("w:spacing"))
        if s.get(qn("w:line")) and s.get(qn("w:lineRule"), "auto") == "auto"
    ]
    return max(vals) if vals else 1.0


def has_paragraph_spacing(doc) -> bool:
    return any(
        int(s.get(qn(f"w:{a}"), 0) or 0) > 0
        for s in doc.element.body.iter(qn("w:spacing"))
        for a in ("before", "after")
    )


def apply_adjust(docx_path: Path, adj: Adjust) -> None:
    doc = Document(str(docx_path))
    body = doc.element.body
    if adj.zero_spacing:
        for s in body.iter(qn("w:spacing")):
            for a in ("before", "after"):
                if s.get(qn(f"w:{a}")) is not None:
                    s.set(qn(f"w:{a}"), "0")
            for a in ("beforeAutospacing", "afterAutospacing"):
                if s.get(qn(f"w:{a}")) is not None:
                    s.set(qn(f"w:{a}"), "0")
    if adj.line_spacing is not None:
        target = int(round(240 * adj.line_spacing))
        for s in body.iter(qn("w:spacing")):
            line = s.get(qn("w:line"))
            if line and s.get(qn("w:lineRule"), "auto") == "auto" and int(line) > target:
                s.set(qn("w:line"), str(target))
    if adj.margin_in is not None:
        cap = int(round(adj.margin_in * TWIPS_PER_INCH))
        for m in body.iter(qn("w:pgMar")):
            for side in ("top", "right", "bottom", "left"):
                v = m.get(qn(f"w:{side}"))
                if v is not None and int(v) > cap:
                    m.set(qn(f"w:{side}"), str(cap))
    if adj.font_pt is not None:
        base = int(round(body_font_pt(doc) * 2))
        new = str(int(round(adj.font_pt * 2)))
        for tag in ("w:sz", "w:szCs"):
            for sz in body.iter(qn(tag)):
                if int(sz.get(qn("w:val"))) == base:
                    sz.set(qn("w:val"), new)
    doc.save(str(docx_path))
