"""Fill the docxtpl template with a context."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from docx.oxml.ns import qn
from docxtpl import DocxTemplate
from jobpipe import resume
from jobpipe.render.pdf import page_count, to_pdf


def render_docx(context: dict[str, Any], out_docx: Path, template: Path | None = None) -> None:
    tpl = DocxTemplate(str(template or resume.active().template))
    tpl.render(context, autoescape=True)
    _strip_empty_runs(tpl.docx.element.body)
    _wrap_links(tpl.docx, collect_links(context))
    strip_rsids(tpl.docx.element.body)
    out_docx.parent.mkdir(parents=True, exist_ok=True)
    tpl.save(str(out_docx))


def collect_links(obj: Any, found: dict[str, str] | None = None) -> dict[str, str]:
    """text -> url for every {"t", "link"} segment anywhere in the context."""
    found = {} if found is None else found
    if isinstance(obj, dict):
        if obj.get("link") and "t" in obj:
            found[obj["t"]] = obj["link"]
        for v in obj.values():
            collect_links(v, found)
    elif isinstance(obj, list):
        for v in obj:
            collect_links(v, found)
    return found


def _wrap_links(document, links: dict[str, str]) -> None:
    """The template renders link segments as Hyperlink-styled runs; wrap each one in a
    real w:hyperlink pointing at its URL (docxtpl cannot create relationships inline)."""
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from lxml import etree

    rel_ids: dict[str, str] = {}
    for r in list(document.element.body.iter(qn("w:r"))):
        if r.getparent().tag == qn("w:hyperlink"):
            continue  # static links copied from the master (contact line)
        rpr = r.find(qn("w:rPr"))
        style = rpr.find(qn("w:rStyle")) if rpr is not None else None
        if style is None or style.get(qn("w:val")) != "Hyperlink":
            continue
        url = links.get("".join(t.text or "" for t in r.iter(qn("w:t"))))
        if not url:
            continue
        if url not in rel_ids:
            rel_ids[url] = document.part.relate_to(url, RT.HYPERLINK, is_external=True)
        link = etree.Element(qn("w:hyperlink"))
        link.set(qn("r:id"), rel_ids[url])
        link.set(qn("w:history"), "1")
        r.addprevious(link)
        link.append(r)


def strip_rsids(body) -> None:
    """Drop rsid (revision-session) attributes. Word shapes runs with different rsids
    separately, so mixed rsids make otherwise identical text lay out differently.
    Stripping them everywhere gives every render the same, consistent layout."""
    for el in body.iter():
        for k in [k for k in el.attrib if "}rsid" in k]:
            del el.attrib[k]


def _strip_empty_runs(body) -> None:
    """docxtpl leaves an unformatted empty run on each side of every {%r %} tag. They
    fall back to the default font and shift Word's layout, so remove them."""
    for r in list(body.iter(qn("w:r"))):
        content = [c for c in r if c.tag != qn("w:rPr")]
        if all(c.tag == qn("w:t") and not c.text for c in content):
            r.getparent().remove(r)


def smoke_render(out_dir: Path | None = None) -> int:
    """Render the template with the master's own content; return the PDF page count."""
    from jobpipe.render.template import master_context

    out_dir = out_dir or Path(tempfile.mkdtemp(prefix="jobpipe-smoke-"))
    docx = out_dir / "master_rendered.docx"
    pdf = out_dir / "master_rendered.pdf"
    render_docx(master_context(resume.active().master), docx)
    to_pdf(docx, pdf)
    return page_count(pdf)
