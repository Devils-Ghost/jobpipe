"""One-page fit ladder."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterator
from docx import Document
from jobpipe.config import settings
from jobpipe.render.adjust import Adjust, apply_adjust, body_font_pt, has_paragraph_spacing, max_line_spacing, max_margin_in
from jobpipe.render.fill import render_docx
from jobpipe.render.pdf import page_count, to_pdf


def drop_lowest_bullet(context: dict[str, Any]) -> str | None:
    """Remove the lowest-ranked project bullet: later projects rank lower than earlier ones,
    and within a project the last bullet is the lowest. Never empties a project (experience is
    fixed and never trimmed). Returns a description or None."""
    for proj in reversed(context.get("projects", [])):
        if len(proj["bullets"]) > 1:
            proj["bullets"].pop()
            return f"bullet from project {proj['title'][:40]!r}"
    return None


def drop_lowest_project(context: dict[str, Any]) -> str | None:
    projects = context.get("projects", [])
    if len(projects) > 1:
        return f"project {projects.pop()['title'][:40]!r}"
    return None


@dataclass
class FitResult:
    pages: int
    steps: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    converter: str = ""
    fits: bool = False


@dataclass
class DocStats:
    """What the rendered document currently looks like, so no-op steps are skipped."""
    spacing: bool
    line_spacing: float
    margin_in: float
    font_pt: float


def ladder(stats: DocStats, floors: dict[str, Any], step_pt: float) -> Iterator[tuple[str, Adjust]]:
    """Yield cumulative formatting adjustments in order, skipping steps that change nothing."""
    adj = Adjust()
    if stats.spacing:
        adj = replace(adj, zero_spacing=True)
        yield "paragraph spacing -> 0", adj
    if stats.line_spacing > floors["line_spacing"]:
        adj = replace(adj, line_spacing=floors["line_spacing"])
        yield f"line spacing -> {floors['line_spacing']}", adj
    if stats.margin_in > floors["margin_in"]:
        adj = replace(adj, margin_in=floors["margin_in"])
        yield f"margins -> {floors['margin_in']} in", adj
    font = stats.font_pt
    while font - step_pt >= floors["body_font_pt"] - 1e-9:
        font = round(font - step_pt, 2)
        adj = replace(adj, font_pt=font)
        yield f"body font -> {font} pt", adj


def fit_one_page(
    context: dict[str, Any],
    out_docx: Path,
    out_pdf: Path,
    *,
    template: Path | None = None,
    measure: Callable[[dict[str, Any], Adjust], tuple[int, DocStats]] | None = None,
    max_drops: int = 6,
) -> FitResult:
    """Render, then walk the ladder until the PDF is one page. `measure` is injectable
    for tests; by default it renders to out_docx/out_pdf and counts pages."""
    cfg = settings()["render"]
    floors = cfg["floors"]
    ctx = copy.deepcopy(context)
    result = FitResult(pages=0)

    def real_measure(c: dict[str, Any], adj: Adjust) -> tuple[int, DocStats]:
        render_docx(c, out_docx, template)
        stats = _stats(out_docx)
        if adj != Adjust():
            apply_adjust(out_docx, adj)
        result.converter = to_pdf(out_docx, out_pdf)
        return page_count(out_pdf), stats

    measure = measure or real_measure
    pages, stats = measure(ctx, Adjust())
    result.pages = pages
    if pages <= 1:
        result.fits = True
        return result

    adj = Adjust()
    for desc, step in ladder(stats, floors, cfg.get("font_step_pt", 0.5)):
        adj = step
        result.steps.append(desc)
        result.pages, _ = measure(ctx, adj)
        if result.pages <= 1:
            result.fits = True
            return result

    for i in range(max_drops):
        dropped = drop_lowest_bullet(ctx) if i % 2 == 0 else drop_lowest_project(ctx)
        if dropped is None:
            dropped = drop_lowest_project(ctx) if i % 2 == 0 else drop_lowest_bullet(ctx)
        if dropped is None:
            break
        result.dropped.append(dropped)
        result.pages, _ = measure(ctx, adj)
        if result.pages <= 1:
            result.fits = True
            return result
    return result


def _stats(docx_path: Path) -> DocStats:
    doc = Document(str(docx_path))
    return DocStats(
        spacing=has_paragraph_spacing(doc),
        line_spacing=max_line_spacing(doc),
        margin_in=max_margin_in(doc),
        font_pt=body_font_pt(doc),
    )
