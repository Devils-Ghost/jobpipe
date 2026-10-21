"""Turn a master resume into a docxtpl template, and extract the master's own content.

The master's paragraphs are reused in place: pPr and rPr are copied verbatim, only the
text-bearing runs are swapped for Jinja tags. Bullet text is a list of segments
{"t": text, "b": bold, "link"?: url} rendered through a run-level loop with exemplar runs
(normal, bold, link), so bolded keywords keep the master's exact run formatting.

Layout detection works on any resume that has section headings:
  - a heading is a short paragraph that is ALL CAPS, uses a Word "Heading" style, or is a
    bold, well-known section name ("Experience", "Skills", ...). layout.yaml next to the
    master can map headings explicitly: {sections: {"Technical Toolkit": skills, ...}}
  - skills section: every paragraph is a row; "Label: items" rows keep their label
  - projects section: a non-list paragraph is a project header (title, tab, then a repo link or a
    date at the right), list paragraphs are its bullets
  - everything else (header, contact, education, experience, ...) is static

Dynamic: skills items and the projects block. Experience stays exactly as on the master.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml.ns import qn
from lxml import etree

KIND_KEYWORDS = [
    ("skills", ("SKILL", "TECHNOLOG", "TOOLKIT", "COMPETENC")),
    ("experience", ("EXPERIENCE", "EMPLOYMENT", "WORK HISTORY")),
    ("projects", ("PROJECT",)),
]
KNOWN_HEADINGS = {
    "summary", "profile", "objective", "education", "skills", "technical skills", "experience",
    "work experience", "professional experience", "employment", "projects", "academic projects",
    "personal projects", "publications", "certifications", "awards", "honors", "leadership",
    "activities", "volunteering", "research", "research experience", "coursework", "interests",
}


# ---------------------------------------------------------------- parsing helpers


def _text(el) -> str:
    return "".join(t.text or "" for t in el.iter(qn("w:t")))


def _line_text(p) -> str:
    """Paragraph text with tabs shown as " | " (e.g. a header's right-aligned date)."""
    parts = []
    for el in p.iter(qn("w:t"), qn("w:tab")):
        parts.append(" | " if el.tag == qn("w:tab") else (el.text or ""))
    return " ".join("".join(parts).split()).strip(" |")


def _is_bold(run) -> bool:
    rpr = run.find(qn("w:rPr"))
    if rpr is None:
        return False
    b = rpr.find(qn("w:b"))
    return b is not None and b.get(qn("w:val")) not in ("0", "false")


def _pstyle(p) -> str:
    ppr = p.find(qn("w:pPr"))
    st = ppr.find(qn("w:pStyle")) if ppr is not None else None
    return st.get(qn("w:val"), "") if st is not None else ""


def _is_list(p) -> bool:
    """A bullet: direct list numbering, or a list paragraph style (e.g. "List Bullet")."""
    ppr = p.find(qn("w:pPr"))
    if ppr is not None and ppr.find(qn("w:numPr")) is not None:
        return True
    return "list" in _pstyle(p).lower()


def _text_runs(p) -> list[Any]:
    return [r for r in p.iter(qn("w:r")) if "".join(x.text or "" for x in r.iter(qn("w:t"))).strip()]


def heading_kind(p, explicit: dict[str, str] | None = None) -> str | None:
    """'skills' | 'experience' | 'projects' | 'static' for a section heading, else None."""
    text = " ".join(_text(p).split())
    if not text or _is_list(p):
        return None
    if explicit:
        for name, kind in explicit.items():
            if text.lower() == name.strip().lower():
                return kind
    runs = _text_runs(p)
    letters = re.sub(r"[^A-Za-z]", "", text)
    is_heading = len(text) <= 40 and (
        (letters and letters.isupper())
        or _pstyle(p).lower().startswith("heading")
        or (runs and all(_is_bold(r) for r in runs) and text.lower().rstrip(":") in KNOWN_HEADINGS)
    )
    if not is_heading:
        return None
    up = text.upper()
    for kind, words in KIND_KEYWORDS:
        if any(w in up for w in words):
            return kind
    return "static"


def segments(p, merge: bool = True, rels: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Text runs of a paragraph as {t, b} segments, plus "link": url for runs inside a
    w:hyperlink (resolved through `rels`, rId -> URL).
    merge=False keeps the original run boundaries: Word lays out glyphs per run, so the
    acceptance test needs the master's exact run split to be pixel-identical."""
    segs: list[dict[str, Any]] = []
    for r in p.iter(qn("w:r")):
        t = "".join(x.text or "" for x in r.iter(qn("w:t")))
        if not t:
            continue
        seg: dict[str, Any] = {"t": t, "b": _is_bold(r)}
        parent = r.getparent()
        if rels is not None and parent.tag == qn("w:hyperlink") and parent.get(qn("r:id")) in rels:
            seg["link"] = rels[parent.get(qn("r:id"))]
        if merge and segs and "link" not in seg and "link" not in segs[-1] and segs[-1]["b"] == seg["b"]:
            segs[-1]["t"] += t
        else:
            segs.append(seg)
    return segs


def _external_rels(doc) -> dict[str, str]:
    return {rid: rel.target_ref for rid, rel in doc.part.rels.items() if rel.is_external}


def _ind_right(p) -> int:
    ppr = p.find(qn("w:pPr"))
    ind = ppr.find(qn("w:ind")) if ppr is not None else None
    return int(ind.get(qn("w:right"), 0)) if ind is not None else 0


def _num_id(p) -> str | None:
    ppr = p.find(qn("w:pPr"))
    num = ppr.find(qn("w:numPr")) if ppr is not None else None
    nid = num.find(qn("w:numId")) if num is not None else None
    return nid.get(qn("w:val")) if nid is not None else None


def _spacing_before(p) -> int:
    ppr = p.find(qn("w:pPr"))
    sp = ppr.find(qn("w:spacing")) if ppr is not None else None
    return int(sp.get(qn("w:before"), 0)) if sp is not None else 0


@dataclass
class Block:
    header: Any
    bullets: list[Any] = field(default_factory=list)


@dataclass
class Layout:
    skills: list[Any] = field(default_factory=list)
    experience: list[Block] = field(default_factory=list)
    projects: list[Block] = field(default_factory=list)


class LayoutError(ValueError):
    pass


def parse(body, explicit: dict[str, str] | None = None) -> Layout:
    layout = Layout()
    section = None
    for el in body.iterchildren():
        if el.tag != qn("w:p"):
            continue
        kind = heading_kind(el, explicit)
        if kind:
            section = kind
            continue
        if not _text(el).strip():
            continue
        if section == "skills":
            layout.skills.append(el)
        elif section in ("experience", "projects"):
            blocks = layout.experience if section == "experience" else layout.projects
            if _is_list(el) and blocks:
                blocks[-1].bullets.append(el)
            elif not _is_list(el):
                blocks.append(Block(header=el))
    if not layout.skills:
        raise LayoutError("no skills section found: add a heading such as 'SKILLS', or map it in layout.yaml")
    bad = [_text(b.header)[:50] for b in layout.projects if not b.bullets]
    if bad:
        raise LayoutError(f"project headers without bullets (is each project's bullet list a Word list?): {bad}")
    return layout


def _label_run_count(p) -> int:
    """Leading runs that form the 'Label:' of a skills row (0 when the row has no label)."""
    seen = ""
    for i, r in enumerate(p.iter(qn("w:r"))):
        seen += "".join(x.text or "" for x in r.iter(qn("w:t")))
        if ":" in seen:
            return i + 1 if len(seen) <= 60 else 0
    return 0


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def skill_keys(labels: list[str]) -> list[str]:
    keys, seen = [], set()
    for i, label in enumerate(labels, 1):
        k = _slug(label.split(":")[0]) or f"row{i}"
        while k in seen:
            k += "_2"
        seen.add(k)
        keys.append(k)
    return keys


# ---------------------------------------------------------------- master content


def master_context(master: Path, explicit: dict[str, str] | None = None) -> dict[str, Any]:
    """The master resume's own dynamic content, in the template's context shape. Cached per
    file and modification time; callers get a deep copy they may modify."""
    if explicit is None:
        explicit = _explicit_for(master)
    key = json.dumps(explicit, sort_keys=True)
    return copy.deepcopy(_master_context(str(master), master.stat().st_mtime, key))


def _explicit_for(master: Path) -> dict[str, str]:
    """Section mapping from layout.yaml next to the master, if any."""
    import yaml

    f = master.parent / "layout.yaml"
    data = (yaml.safe_load(f.read_text(encoding="utf-8")) or {}) if f.exists() else {}
    return data.get("sections") or {}


@lru_cache(maxsize=16)
def _master_context(master_str: str, _mtime: float, explicit_json: str) -> dict[str, Any]:
    doc = Document(master_str)
    rels = _external_rels(doc)
    layout = parse(doc.element.body, json.loads(explicit_json))
    labels = []
    items = []
    for p in layout.skills:
        n = _label_run_count(p)
        runs = list(p.iter(qn("w:r")))
        labels.append("".join(_text(r) for r in runs[:n]))
        tail = etree.Element(qn("w:p"))
        for r in runs[n:]:
            tail.append(copy.deepcopy(r))
        items.append(segments(tail, merge=False, rels=rels))
    keys = skill_keys(labels)
    experience = [
        {"header": _line_text(b.header), "bullets": [segments(x, merge=False, rels=rels) for x in b.bullets]}
        for b in layout.experience
    ]
    projects = []
    for blk in layout.projects:
        title, term, term_link = _header_parts(blk.header, rels)
        projects.append(
            {
                "title": title,
                "term": term,
                "term_link": term_link,
                "term_seg": {"t": term, "link": term_link},  # lets render._wrap_links find the URL
                "before": _spacing_before(blk.header),
                "ind_right": _ind_right(blk.header),
                "num_id": _num_id(blk.bullets[0]),
                "bullets": [{"segs": segments(b, merge=False, rels=rels), "ind_right": _ind_right(b)} for b in blk.bullets],
            }
        )
    return {
        "skills": dict(zip(keys, items)),
        "skill_labels": dict(zip(keys, labels)),
        "skill_keys": keys,
        "experience": experience,
        "projects": projects,
    }


def _header_parts(p, rels: dict[str, str]) -> tuple[str, str, str]:
    """Project header split at its first tab: (title, term, link). The term is what sits at the
    right (a repo link, a date, ...); link is its URL when it is a hyperlink, else "".
    No tab: (whole text, "", "")."""
    before, after, seen_tab, link = [], [], False, ""
    for r in p.iter(qn("w:r")):
        if r.find(qn("w:tab")) is not None:
            seen_tab = True
        t = "".join(x.text or "" for x in r.iter(qn("w:t")))
        (after if seen_tab else before).append(t)
        parent = r.getparent()
        if seen_tab and t.strip() and parent.tag == qn("w:hyperlink"):
            link = link or rels.get(parent.get(qn("r:id")), "")
    return "".join(before), "".join(after).strip(), link


# ---------------------------------------------------------------- template building


def _w(tag: str, **attrs: str):
    el = etree.Element(qn(f"w:{tag}"))
    for k, v in attrs.items():
        el.set(qn(f"w:{k}"), v)
    return el


def _run(text: str, rpr=None):
    r = _w("r")
    if rpr is not None:
        r.append(copy.deepcopy(rpr))
    t = _w("t")
    t.text = text
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    r.append(t)
    return r


def _tag_paragraph(tag: str):
    """A paragraph holding only a {%p ... %} tag; docxtpl removes the whole paragraph."""
    p = _w("p")
    p.append(_run(tag))
    return p


def _rprs(p) -> tuple[Any, Any]:
    """(normal rPr, bold rPr) taken from the paragraph's own runs; synthesized when missing."""
    normal = bold = None
    for r in p.iter(qn("w:r")):
        rpr = r.find(qn("w:rPr"))
        if rpr is not None and rpr.find(qn("w:rStyle")) is not None:
            continue
        if not "".join(x.text or "" for x in r.iter(qn("w:t"))):
            continue
        if _is_bold(r):
            bold = bold if bold is not None else (rpr if rpr is not None else _w("rPr"))
        else:
            normal = normal if normal is not None else (rpr if rpr is not None else _w("rPr"))
    if normal is None and bold is None:
        normal = _w("rPr")
    if normal is None:
        normal = copy.deepcopy(bold)
        for x in normal.findall(qn("w:b")) + normal.findall(qn("w:bCs")):
            normal.remove(x)
    if bold is None:
        bold = copy.deepcopy(normal)
        anchor = bold.find(qn("w:rFonts"))
        idx = list(bold).index(anchor) + 1 if anchor is not None else 0
        bold.insert(idx, _w("bCs"))
        bold.insert(idx, _w("b"))
    return normal, bold


def _link_rpr(layout: Layout, fallback):
    """Run formatting for links inside bullets: the master's own hyperlink run if a bullet
    has one, else the normal run plus the Hyperlink character style."""
    for blk in layout.experience + layout.projects:
        for p in blk.bullets:
            for r in p.iter(qn("w:r")):
                rpr = r.find(qn("w:rPr"))
                if rpr is not None and rpr.find(qn("w:rStyle")) is not None:
                    return rpr
    rpr = copy.deepcopy(fallback)
    rpr.insert(0, _w("rStyle", val="Hyperlink"))
    return rpr


def _seg_runs(var: str, normal, bold, link) -> list[Any]:
    return [
        _run(f"{{%r for s in {var} %}}"),
        _run("{%r if s.link %}"),
        _run("{{ s.t }}", link),
        _run("{%r elif s.b %}"),
        _run("{{ s.t }}", bold),
        _run("{%r else %}"),
        _run("{{ s.t }}", normal),
        _run("{%r endif %}"),
        _run("{%r endfor %}"),
    ]


def _clear_content(p) -> None:
    for child in list(p):
        if child.tag != qn("w:pPr"):
            p.remove(child)


def _ensure_ppr(p):
    ppr = p.find(qn("w:pPr"))
    if ppr is None:
        ppr = _w("pPr")
        p.insert(0, ppr)
    return ppr


def _set_attr(p, child: str, attr: str, value: str) -> None:
    ppr = _ensure_ppr(p)
    el = ppr.find(qn(f"w:{child}"))
    if el is None:
        el = _w(child)
        # keep schema order: ind and spacing come before jc / rPr when present
        anchor = ppr.find(qn("w:jc"))
        if anchor is None:
            anchor = ppr.find(qn("w:rPr"))
        if anchor is not None:
            anchor.addprevious(el)
        else:
            ppr.append(el)
    el.set(qn(f"w:{attr}"), value)


def _bullet_template(src, var: str, ind_var: str | None, link):
    normal, bold = _rprs(src)
    p = copy.deepcopy(src)
    _clear_content(p)
    if ind_var:
        _set_attr(p, "ind", "right", f"{{{{ {ind_var} }}}}")
    for r in _seg_runs(var, normal, bold, link):
        p.append(r)
    return p


def _header_rprs(p) -> tuple[Any, Any, Any]:
    """(title rPr, tab run, term rPr) of a project header."""
    runs = [r for r in p.iter(qn("w:r"))]
    tab = next((r for r in runs if r.find(qn("w:tab")) is not None), None)
    texted = [r for r in runs if "".join(x.text or "" for x in r.iter(qn("w:t"))).strip()]
    before = [r for r in texted if tab is None or _index(runs, r) < _index(runs, tab)]
    after = [r for r in texted if tab is not None and _index(runs, r) > _index(runs, tab)]

    def rpr_of(r):
        x = r.find(qn("w:rPr")) if r is not None else None
        return x if x is not None else _w("rPr")

    return rpr_of(before[0] if before else None), tab, rpr_of(after[0] if after else (before[0] if before else None))


def _index(seq, item) -> int:
    for i, x in enumerate(seq):
        if x is item:
            return i
    return -1


def build_template(master: Path, out: Path, explicit: dict[str, str] | None = None) -> dict[str, Any]:
    """Write the template next to `out` plus a .json with its layout facts. Returns the meta."""
    if explicit is None:
        explicit = _explicit_for(master)
    doc = Document(str(master))
    layout = parse(doc.element.body, explicit)
    ctx = master_context(master, explicit)
    first_bullet = next((b.bullets[0] for b in layout.projects + layout.experience if b.bullets), layout.skills[0])
    link = copy.deepcopy(_link_rpr(layout, _rprs(first_bullet)[0]))

    # Skills: keep each row's label runs, loop the rest.
    for key, p in zip(ctx["skill_keys"], layout.skills):
        n = _label_run_count(p)
        normal, bold = _rprs(p)
        runs = list(p.iter(qn("w:r")))
        if n:
            last_label = runs[n - 1]
            # drop every sibling after the label (runs, proofErr, hyperlinks, bookmarks)
            anchor = last_label if last_label.getparent() is p else last_label.getparent()
            for sib in list(anchor.itersiblings()):
                p.remove(sib)
        else:
            _clear_content(p)
        for r in _seg_runs(f"skills['{key}']", normal, bold, link):
            p.append(r)

    # Projects: one header + bullets block, looped.
    if layout.projects:
        first_header = layout.projects[0].header
        hdr = copy.deepcopy(first_header)
        title_rpr, tab_run, term_rpr = _header_rprs(hdr)
        _clear_content(hdr)
        _set_attr(hdr, "spacing", "before", "{{ proj.before }}")
        _set_attr(hdr, "ind", "right", "{{ proj.ind_right }}")
        hdr.append(_run("{{ proj.title }}", title_rpr))
        if tab_run is not None:
            hdr.append(copy.deepcopy(tab_run))
            # a hyperlinked term (repo URL) keeps the master's link look; a plain one (date) its own
            plain_rpr = copy.deepcopy(term_rpr)
            style = plain_rpr.find(qn("w:rStyle"))
            link_rpr = term_rpr if style is not None else link
            if style is not None:
                plain_rpr.remove(style)
            for r in (_run("{%r if proj.term_link %}"), _run("{{ proj.term }}", link_rpr),
                      _run("{%r else %}"), _run("{{ proj.term }}", plain_rpr), _run("{%r endif %}")):
                hdr.append(r)
        bullet_tpl = _bullet_template(layout.projects[0].bullets[0], "b.segs", "b.ind_right", link)
        num = bullet_tpl.find(qn("w:pPr")).find(qn("w:numPr"))
        if num is not None and num.find(qn("w:numId")) is not None:
            # project lists may use different numbering definitions (glyph size changes line
            # height), so the list id is per project
            num.find(qn("w:numId")).set(qn("w:val"), "{{ proj.num_id }}")
        for el in [
            _tag_paragraph("{%p for proj in projects %}"),
            hdr,
            _tag_paragraph("{%p for b in proj.bullets %}"),
            bullet_tpl,
            _tag_paragraph("{%p endfor %}"),
            _tag_paragraph("{%p endfor %}"),
        ]:
            first_header.addprevious(el)
        for blk in layout.projects:
            for p in [blk.header, *blk.bullets]:
                p.getparent().remove(p)

    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))

    meta = {
        "skill_keys": ctx["skill_keys"],
        "skill_labels": ctx["skill_labels"],
        "project_styles": [
            {"before": p["before"], "ind_right": p["ind_right"], "num_id": p["num_id"],
             "bullet_ind_right": p["bullets"][0]["ind_right"]}
            for p in ctx["projects"]
        ],
        "project_bullet_counts": [len(b.bullets) for b in layout.projects],
        "experience_bullet_counts": [len(b.bullets) for b in layout.experience],
    }
    out.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta
