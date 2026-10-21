"""Page-image comparison for the template acceptance test."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageChops


@dataclass
class PageDiff:
    page: int
    differing_pixels: int
    total_pixels: int
    bbox: tuple[int, int, int, int] | None

    @property
    def ratio(self) -> float:
        return self.differing_pixels / self.total_pixels if self.total_pixels else 0.0


def rasterize(pdf: Path, scale: float = 2.0) -> list[Image.Image]:
    doc = pdfium.PdfDocument(str(pdf))
    try:
        return [doc[i].render(scale=scale).to_pil().convert("L") for i in range(len(doc))]
    finally:
        doc.close()


def compare(a: Path, b: Path, out_dir: Path | None = None, threshold: int = 48) -> tuple[list[PageDiff], int, int]:
    """Compare two PDFs page by page. Pixels count as different when their grey levels
    differ by more than `threshold` (ignores anti-aliasing noise). Writes diff images
    to out_dir if given. Returns (diffs, pages_a, pages_b)."""
    pa, pb = rasterize(a), rasterize(b)
    diffs = []
    for i, (ia, ib) in enumerate(zip(pa, pb)):
        if ia.size != ib.size:
            ib = ib.resize(ia.size)
        delta = ImageChops.difference(ia, ib).point(lambda v: 255 if v > threshold else 0)
        n = sum(1 for v in delta.getdata() if v)
        diffs.append(PageDiff(i + 1, n, ia.size[0] * ia.size[1], delta.getbbox()))
        if out_dir and n:
            out_dir.mkdir(parents=True, exist_ok=True)
            faint = ia.point(lambda v: v // 3 + 170)  # master page, washed out
            overlay = Image.merge("RGB", (faint, faint, faint))
            overlay.paste((220, 0, 0), mask=delta)  # differing pixels in red
            overlay.save(out_dir / f"diff_page{i + 1}.png")
    return diffs, len(pa), len(pb)
