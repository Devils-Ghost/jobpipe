"""Tailoring rules, against the fictional sample profile (Jordan Lee, data science)."""

import pytest
from docx import Document
from docx.oxml.ns import qn

from jobpipe import render, resume
from jobpipe.config import settings
from jobpipe.tailor import (
    NewProject,
    TailorOutput,
    apply_pool_reuse,
    build_context,
    build_prompt,
    experience_bullets,
    fit_skill_rows,
    numbers,
    offerable_projects,
    parse_bold,
    project_header,
    repeated_terms,
    skill_row_budget,
    style_lint,
    unbuilt_cap_warning,
    validate,
)

POOL = {"projects": []}

# three bullets of 160-235 characters, each with a number, a "by/using" clause and a known technology
NEW_BULLETS = [
    "Sustained **2,000** writes per second across **3** replicas by building a **Kafka** consumer group that applies idempotent upserts into **PostgreSQL**, keeping p99 latency under **40 ms**.",
    "Recovered from a killed worker in under **5** seconds through heartbeat-based leader election, verified by killing a random node during a **20** minute load test with **Kafka** replay enabled.",
    "Held **92%** line coverage with **pytest** suites that inject network partitions and duplicate messages, and gated every change on that threshold in a **GitHub** workflow before any merge.",
]
NEW_METRICS = [{"claim": "2,000 writes/s on 3 replicas, p99 under 40 ms"}, {"claim": "recovers in under 5 s in a 20 minute test"},
               {"claim": "92% line coverage"}]


def new_project(slug="dist-log", bullets=None) -> NewProject:
    return NewProject(slug=slug, name="Distributed Log Service", stack=["Kafka", "PostgreSQL", "Python", "pytest"],
                      resume_bullets=bullets or list(NEW_BULLETS), metrics=NEW_METRICS)


def valid_output() -> TailorOutput:
    return TailorOutput(
        skills={
            "languages": ["Python", "SQL", "R"],
            "machine_learning": ["XGBoost", "PyTorch", "scikit-learn"],
            "data_tools": ["pandas", "Spark", "Airflow"],
        },
        projects_used=["proj-churn", "proj-sentiment"],
        keywords={"must_have_matched": ["Python", "SQL"], "must_have_missing": ["Scala"]},
    )


def with_new_project(out: TailorOutput, **kw) -> TailorOutput:
    out.new_projects = [new_project(**kw)]
    out.projects_used = ["proj-churn", out.new_projects[0].slug]
    return out


def _errors(out, pool=POOL):
    return " | ".join(validate(out, pool).errors)


@pytest.fixture(autouse=True)
def _ds(ds):
    """Every test here runs with the ds resume (it has a projects section) unless it asks for another."""
    yield


def test_valid_output_passes():
    v = validate(valid_output(), POOL)
    assert v.ok, v.errors


# ---------------------------------------------------------------- fixed experience


def test_experience_is_static_in_the_template():
    assert "roles" not in build_context(valid_output())  # experience is copied from the master, never rendered from data


def test_prompt_shows_fixed_experience_and_profile_values():
    p = build_prompt("JD", POOL)
    assert "Cut weekly reporting time by **70%**" in p  # master experience bullet, verbatim
    assert "Jordan Lee" in p and "data science" in p
    assert "`machine_learning` (Machine Learning)" in p
    assert '"languages": [], "machine_learning": [], "data_tools": []' in p
    assert "acme-salary" not in p  # experience facts are not offered for rewriting
    assert "Explained churn drivers to stakeholders through **SHAP**" in p  # built projects show their final bullets
    assert "<<" not in p


def test_experience_bullets_keyed_by_header():
    exp = experience_bullets()
    first = list(exp)[0]
    assert first.startswith("Data Analyst, Acme Retail") and len(exp[first]) == 3


# ---------------------------------------------------------------- project slots


def test_built_projects_need_curated_bullets_of_the_right_count():
    assert {p["id"] for p in offerable_projects()} == {"proj-churn", "proj-forecast", "proj-sentiment"}


def test_unknown_or_duplicate_projects_fail():
    out = valid_output()
    out.projects_used = ["proj-churn", "proj-nope"]
    assert "'proj-nope', which is neither a built project id nor a new_projects slug" in _errors(out)
    out.projects_used = ["proj-churn", "proj-churn"]
    assert "exactly 2 distinct ids" in _errors(out)


def test_built_project_bullets_are_printed_verbatim():
    ctx = build_context(valid_output())
    got = ["".join(s["t"] for s in b["segs"]) for b in ctx["projects"][0]["bullets"]]
    assert got == [
        "Raised churn recall by 18% on 40,000 customers by training gradient-boosted models in XGBoost with class weighting.",
        "Explained churn drivers to stakeholders through SHAP values in a Streamlit report.",
        "Prepared customer features with pandas in a repeatable Python pipeline that feeds the model.",
    ]
    assert any(s["b"] for s in ctx["projects"][0]["bullets"][0]["segs"])  # **bold** kept


def test_a_weak_built_project_can_be_replaced_by_a_new_one():
    out = with_new_project(valid_output())
    assert validate(out, POOL).ok, validate(out, POOL).errors
    assert [p["title"] for p in build_context(out)["projects"]] == ["Customer Churn Predictor", "Distributed Log Service"]


def test_every_slot_can_be_new():
    second = NewProject(
        slug="edge-cache", name="Edge Cache", stack=["Redis", "Go"], metrics=[{"claim": "120,000 reads/s, 99.9% hit rate, 8 shards"}],
        resume_bullets=["Served **120,000** reads per second across **8** shards by routing keys with consistent hashing in **Go**, with a **99.9%** hit rate on a skewed Zipf workload generated by a custom load tool.",
                        "Cut tail latency through request coalescing in **Redis**, holding **99.9%** hit rate while **8** shards rebalanced under a rolling restart that replaced every node in turn.",
                        "Proved correctness by replaying **120,000** recorded reads against a model of the cache in **Go**, matching **99.9%** of values after each rebalance and after every simulated node failure."])
    out = valid_output()
    out.new_projects = [new_project("dist-log"), second]
    out.projects_used = ["dist-log", "edge-cache"]
    assert validate(out, POOL).ok, validate(out, POOL).errors
    out.new_projects += [new_project("dist-log-two"), new_project("dist-log-three")]
    assert "at most 3 new projects" in _errors(out)


# ---------------------------------------------------------------- skills


def test_skills_whitelist_and_aliases():
    out = valid_output()
    out.skills["languages"].append("Scala")
    assert "Scala" in _errors(out)
    out = valid_output()
    out.skills["machine_learning"] += ["statistical modeling", "A/B testing"]
    assert validate(out, POOL).ok


def test_skills_rows_must_match_resume():
    out = valid_output()
    del out.skills["data_tools"]
    out.skills["cloud"] = ["Docker"]
    v = validate(out, POOL)
    assert any("missing rows ['data_tools']" in e for e in v.errors)
    assert any("'cloud' is not on the resume" in w for w in v.warnings)


def test_skills_from_used_new_project_stack():
    out = valid_output()
    out.skills["data_tools"].append("dbt Cloud")
    assert "dbt Cloud" in _errors(out)
    with_new_project(out)
    out.new_projects[0].stack.append("dbt Cloud")
    assert "dbt Cloud" not in _errors(out)


def test_skill_rows_trimmed_and_deduplicated():
    out = valid_output()
    out.skills["machine_learning"] = ["scikit-learn", "XGBoost", "PyTorch", "LightGBM", "statistical modeling",
                                      "A/B testing", "Prophet", "SHAP", "Hugging Face Transformers", "Streamlit"]
    removed = fit_skill_rows(out)
    assert any(r.startswith("machine_learning:") for r in removed)
    assert len("Machine Learning: " + ", ".join(out.skills["machine_learning"])) <= skill_row_budget()
    assert out.skills["machine_learning"][0] == "scikit-learn"  # highest-priority items kept
    out.skills["data_tools"] = ["python", "Spark"]
    assert "data_tools: python (duplicate)" in fit_skill_rows(out)


# ---------------------------------------------------------------- style


def test_style_lint():
    assert style_lint("Built a **real** pipeline — fast") == ["contains '—'", "banned word 'real'"]
    assert style_lint("Built real-time and real-life systems for US clients over I/O") == []
    assert "banned word 'i'" in style_lint("I built it")
    assert "banned phrase 'responsible for'" in style_lint("Responsible for the API")


def test_ai_jargon_qualifiers():
    assert style_lint("Leveraged **Spark** to build a robust, seamless pipeline") == [
        "AI-jargon qualifier 'leveraged'", "AI-jargon qualifier 'robust'", "AI-jargon qualifier 'seamless'"]
    assert style_lint("Built an automated test suite for dynamic analysis on scalable services") == []


def test_repeated_terms():
    texts = ["secure API", "secure DB", "secure cache", "secure queue and the and the and the and"]
    assert repeated_terms(texts, 3) == {"secure": 4}


# ---------------------------------------------------------------- new projects


def test_new_project_rules():
    out = valid_output()
    bad = ["Cut latency **40%** using **Spark**.",
           "Built it with **Spark**, no metric here at all, but long enough to pass the length rule so that only the "
           "metric rule fires on this bullet and nothing else is wrong with the sentence.",
           NEW_BULLETS[2]]
    out.new_projects = [NewProject(slug="Bad Slug", name="X", resume_bullets=bad, metrics=[{"claim": "30% faster"}])]
    e = _errors(out)
    assert "kebab-case" in e and "must also be listed in projects_used" in e and "numbers ['40']" in e
    assert "bullet 2: has no metric" in e and "bullet 1: 28 characters" in e


def test_new_project_needs_three_bullets():
    out = with_new_project(valid_output(), bullets=NEW_BULLETS[:2])
    assert "needs exactly 3 resume_bullets, got 2" in _errors(out)


def test_new_project_blocked_at_unbuilt_cap():
    cap = settings()["projects"]["max_unbuilt_projects"]
    full_pool = {"projects": [{"slug": f"p{i}", "status": "proposed"} for i in range(cap)]}
    out = with_new_project(valid_output(), slug="fresh")
    assert f"not allowed: {cap} unbuilt" in _errors(out, full_pool)
    out.new_projects[0].slug = "p0"
    out.projects_used[-1] = "p0"
    assert "unbuilt" not in _errors(out, full_pool)
    w = unbuilt_cap_warning(full_pool)
    assert w.startswith(f"{cap} unbuilt projects already proposed") and unbuilt_cap_warning(POOL) is None


def test_new_projects_off_without_projects_section(analytics):
    out = TailorOutput(skills={"row1": ["SQL", "Tableau"]}, projects_used=[],
                       new_projects=[NewProject(slug="x", name="X", resume_bullets=[])])
    assert "new projects are turned off" in _errors(out)
    out.new_projects = []
    assert validate(out, POOL).ok, validate(out, POOL).errors


def test_pool_reuse_keeps_stored_bullets():
    stored = ["Stored one.", "Stored two.", "Stored three."]
    pool = {"projects": [{"slug": "p0", "name": "Stored", "status": "proposed", "stack": ["Spark"], "repo": "https://github.com/x/p0",
                          "resume_bullets": stored, "metrics": [{"claim": "x", "how_to_measure": "y"}]}]}
    out = valid_output()
    out.new_projects = [NewProject(slug="p0", name="Other")]
    apply_pool_reuse(out, pool)
    assert out.new_projects[0].resume_bullets == stored and out.new_projects[0].name == "Stored"
    # bullets rewritten for the job are kept
    out.new_projects = [NewProject(slug="p0", resume_bullets=["New one.", "New two.", "New three."])]
    apply_pool_reuse(out, pool)
    assert out.new_projects[0].resume_bullets[0] == "New one."
    out.new_projects = [NewProject(slug="p0")]
    apply_pool_reuse(out, pool)
    assert out.new_projects[0].repo == "https://github.com/x/p0"
    # a stored project whose bullet count does not fit this resume is not reused
    pool["projects"][0]["resume_bullets"] = stored[:2]
    out.new_projects = [NewProject(slug="p0", name="Other", resume_bullets=["New one.", "New two.", "New three."])]
    apply_pool_reuse(out, pool)
    assert out.new_projects[0].name == "Other"


# ---------------------------------------------------------------- helpers and rendering


def test_numbers_and_bold_parsing():
    assert numbers("Cut **53%** for 150+ users, 2,000 req on x86 with CIC-IDS2017") == {"53", "150", "2000"}
    assert parse_bold("Cut latency by **53%** with **Redis**") == [
        {"t": "Cut latency by ", "b": False}, {"t": "53%", "b": True}, {"t": " with ", "b": False},
        {"t": "Redis", "b": True}, {"t": ".", "b": False}]


def test_project_headers():
    assert project_header("proj-churn", None) == ("Customer Churn Predictor", "https://github.com/jordan-lee/churn")
    # no `repo` in the fact bank: <github account>/<id without proj->
    assert project_header("proj-sentiment", None) == ("Review Sentiment Classifier", "https://github.com/jordan-lee/sentiment")
    assert project_header("x", NewProject(slug="edge-cache", name="Edge Cache")) == ("Edge Cache", "https://github.com/jordan-lee/edge-cache")
    pooled = NewProject(slug="edge-cache", name="Edge Cache", repo="https://github.com/jordan-lee/edge")
    assert project_header("edge-cache", pooled)[1] == "https://github.com/jordan-lee/edge"


def test_context_renders_with_candidate_content(ds, tmp_path):
    ctx = build_context(valid_output())
    out = tmp_path / "t.docx"
    render.render_docx(ctx, out)
    doc = Document(str(out))
    text = "".join(t.text or "" for t in doc.element.body.iter(qn("w:t")))
    assert "Cut weekly reporting time by 70%" in text  # fixed experience
    assert "Reached 91% accuracy on 50,000 product reviews" in text and "**" not in text
    links = [doc.part.rels[h.get(qn("r:id"))].target_ref for h in doc.element.body.iter(qn("w:hyperlink"))]
    assert "https://streamlit.io/" in links  # profile `links:` setting
    assert "https://github.com/jordan-lee/churn" in links and "jordan-lee/churn" in text  # repo link in the header


def test_coverage_accepts_aliases():
    from jobpipe.tailor.coverage import coverage

    out = valid_output()
    out.keywords.must_have_matched = ["python", "SQL", "XGBoost"]
    out.keywords.must_have_missing = []
    assert coverage(out) == (100.0, [])


def test_coverage_not_meaningful_for_vague_jobs(tmp_path):
    from jobpipe.tailor.coverage import coverage, write_coverage

    out = valid_output()
    out.keywords.must_have_matched = ["Python"]
    out.keywords.must_have_missing = ["Scala"]
    out.keywords.soft_skills = ["Cross-functional communication"]
    pct, absent = coverage(out)
    assert pct is None and absent == []
    write_coverage(tmp_path, out, pct, absent)
    text = (tmp_path / "coverage.txt").read_text()
    assert "not meaningful (the job names only 2 specific skills" in text
    assert "* Cross-functional communication" in text
    assert "Projects on this resume (best fit first): proj-churn, proj-sentiment" in text


def test_prompt_defines_concrete_must_haves():
    p = build_prompt("JD", POOL)
    assert "Not company products, team duties" in p and '"soft_skills": []' in p


def test_prompt_asks_for_advanced_three_bullet_projects():
    p = build_prompt("JD", POOL)
    assert "resume_bullets exactly 3, one sentence" in p
    assert "160-235 characters" in p and "No Target Company Names" in p
    assert "Zero Unresolved Gaps" in p and "<<" not in p


# ---------------------------------------------------------------- multiple resumes


def test_resume_choice():
    assert resume.default().id == "ds"
    assert resume.choose("analytics").id == "analytics"
    assert resume.choose("nonsense").id == "ds"
    assert resume.choose("analytics", forced="ds").id == "ds"
    with pytest.raises(resume.NoResume):
        resume.choose("", forced="missing")


def test_active_resume_switches_template_data(analytics):
    from jobpipe.tailor import project_count, skill_keys

    assert skill_keys() == ["row1"] and project_count() == 0
    with resume.use(resume.get("ds")):
        assert skill_keys() == ["languages", "machine_learning", "data_tools"] and project_count() == 2
    assert skill_keys() == ["row1"]


def test_years_requirement_is_not_searched_in_text():
    from jobpipe.tailor.coverage import coverage

    out = valid_output()
    out.keywords.must_have_matched = ["2+ years of experience in large codebases", "Python", "SQL"]
    out.keywords.must_have_missing = []
    assert coverage(out) == (100.0, [])


def test_skill_spelling_and_dropping():
    from jobpipe import facts
    from jobpipe.tailor import drop_unlisted_skills
    allowed = facts.allowed_norm()
    assert facts.skill_allowed("SQL", allowed) and not facts.skill_allowed("Zzzlang", allowed)
    out = valid_output()
    key = next(iter(out.skills))
    out.skills[key] = out.skills[key] + ["Zzzlang"]
    assert drop_unlisted_skills(out) == ["Zzzlang"] and "Zzzlang" not in out.skills[key]
