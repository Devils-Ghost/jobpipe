"""Rendering: template build, template fill, PDF conversion, one-page fit."""

from jobpipe.render.adjust import TWIPS_PER_INCH, Adjust, body_font_pt, max_margin_in, max_line_spacing, has_paragraph_spacing, apply_adjust
from jobpipe.render.fill import render_docx, collect_links, strip_rsids, smoke_render
from jobpipe.render.pdf import ConversionError, to_pdf, page_count
from jobpipe.render.fit import drop_lowest_bullet, drop_lowest_project, FitResult, DocStats, ladder, fit_one_page

__all__ = ["TWIPS_PER_INCH", "Adjust", "body_font_pt", "max_margin_in", "max_line_spacing", "has_paragraph_spacing", "apply_adjust", "render_docx", "collect_links", "strip_rsids", "smoke_render", "ConversionError", "to_pdf", "page_count", "drop_lowest_bullet", "drop_lowest_project", "FitResult", "DocStats", "ladder", "fit_one_page"]
