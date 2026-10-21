"""Template context: skills rows, project blocks, resume text."""

from __future__ import annotations

from typing import Any
from jobpipe import facts, resume
from jobpipe.segments import linkify
from jobpipe.render.template import master_context
from jobpipe.tailor.layout import skill_keys, template_meta
from jobpipe.tailor.schema import TailorOutput
from jobpipe.tailor.slots import offerable_projects, project_header
from jobpipe.tailor.text import parse_bold, plain
from jobpipe.tailor.validate import all_bullet_texts, fixed_texts


def skill_row_budget() -> int:
    """Longest skills row on the master (label + items), in characters. Rows longer than
    this wrap to a second line, which the master never does."""
    ctx = master_context(resume.active().master)
    return max(len(ctx["skill_labels"][k]) + len("".join(s["t"] for s in ctx["skills"][k])) for k in ctx["skill_keys"])


def fit_skill_rows(out: TailorOutput) -> list[str]:
    """Trim trailing (lowest-priority) items from any skills row that would wrap.
    Returns the removed items as 'row: item'."""
    labels, budget, removed = template_meta()["skill_labels"], skill_row_budget(), []
    seen: set[str] = set()
    for key in skill_keys():
        items = out.skills.setdefault(key, [])
        # an item listed in an earlier row is dropped from later rows
        for item in [i for i in items if facts.norm_skill(i) in seen]:
            items.remove(item)
            removed.append(f"{key}: {item} (duplicate)")
        seen |= {facts.norm_skill(i) for i in items}
        sep = "" if labels[key].endswith(" ") else " "
        while len(items) > 1 and len(labels[key]) + len(sep) + len(", ".join(items)) > budget:
            removed.append(f"{key}: {items.pop()}")
    return removed


def build_context(out: TailorOutput) -> dict[str, Any]:
    meta = template_meta()
    labels = meta["skill_labels"]
    skills = {}
    for key in skill_keys():
        sep = "" if (not labels[key] or labels[key].endswith(" ")) else " "
        skills[key] = [{"t": sep + ", ".join(out.skills.get(key, [])), "b": False}]
    styles = meta["project_styles"]
    new_by_slug = {np.slug: np for np in out.new_projects}
    built = {p["id"]: p for p in offerable_projects()}
    projects = []
    for i, pid in enumerate(out.projects_used):
        st = styles[min(i, len(styles) - 1)]
        new = new_by_slug.get(pid)
        texts = new.resume_bullets if new else built[pid]["resume_bullets"]
        title, url = project_header(pid, new)
        term = facts.repo_label(url)
        projects.append(
            {
                "title": title, "term": term, "term_link": url, "term_seg": {"t": term, "link": url},
                "before": st["before"], "ind_right": st["ind_right"], "num_id": st["num_id"],
                "bullets": [{"segs": linkify(parse_bold(t)), "ind_right": st["bullet_ind_right"]} for t in texts],
            }
        )
    return {"skills": skills, "projects": projects}


def resume_text(out: TailorOutput) -> str:
    skills = " ".join(", ".join(v) for v in out.skills.values())
    return plain(" ".join(fixed_texts(out) + all_bullet_texts(out)) + " " + skills)
