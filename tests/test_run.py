import json

import pytest

from jobpipe import run
from jobpipe.scrape.browser import Anchor, Snapshot, looks_logged_out
from jobpipe.config import ROOT
from jobpipe.scrape.extract import JobCard, build_prompt, validate_urls

URL = "https://jobright.ai/jobs/info/68f0a1b2c3d4e5f6a7b8c9d0"


def card(**kw):
    base = dict(company="Acme", title="Software Engineer", jobright_url=URL)
    return JobCard(**{**base, **kw})


def test_validate_urls_exact_match_only_and_dedupe():
    hrefs = {URL, "https://jobright.ai/jobs/info/other"}
    cards = [card(), card(), card(jobright_url=URL + "?x=1"), card(jobright_url="https://jobright.ai/jobs/info/made-up")]
    ex = validate_urls(cards, hrefs)
    assert [c.jobright_url for c in ex.jobs] == [URL]
    assert len(ex.dropped) == 2 and all("not found" in r for _, r in ex.dropped)


def test_listing_filters(monkeypatch, tmp_path):
    real_path = run.filters.path
    monkeypatch.setattr(run.filters, "path", lambda key: tmp_path if key == "applications" else real_path(key))
    applied = [("Jobright", "Globex", "Backend Engineer")]
    assert run.listing_reason(card(), applied) is None
    assert "reposted" in run.listing_reason(card(reposted=True), applied)
    assert "already applied" in run.listing_reason(card(company="Globex Inc.", title="Backend Engineer"), applied)
    assert "sponsorship" in run.listing_reason(card(sponsorship_note="No H1B"), applied)
    assert run.listing_reason(card(sponsorship_note="H1B Sponsor Likely"), applied) is None
    assert "employer type" in run.listing_reason(card(employer_type="staffing_agency"), applied)
    assert "denylist" in run.listing_reason(card(company="Revature"), applied)
    assert "blacklisted (sheet): Waystar: No H1B" in run.listing_reason(card(company="Waystar"), applied, blacklist=[("Waystar", "No H1B")])
    (tmp_path / "2026-10-13" / f"Acme_Software-Engineer_{URL.rsplit('/', 1)[1]}").mkdir(parents=True)
    assert "already processed" in run.listing_reason(card(), applied)


def test_logged_out_detection():
    wall = Snapshot(url="https://jobright.ai/", title="Jobright", text="Sign in to continue. Continue with Google",
                    anchors=[Anchor("Home", "https://jobright.ai/")])
    jobs = Snapshot(url="https://jobright.ai/jobs/recommend", title="Jobs", text="Sign in settings ... Software Engineer",
                    anchors=[Anchor(f"Job {i}", f"https://jobright.ai/jobs/info/{i}") for i in range(10)])
    login_url = Snapshot(url="https://jobright.ai/login?next=/jobs", title="", text="", anchors=[])
    assert looks_logged_out(wall) and looks_logged_out(login_url) and not looks_logged_out(jobs)


FIXTURE = ROOT / "tests" / "fixtures" / "recommended.json"


@pytest.mark.skipif(not FIXTURE.exists(), reason="run `jobpipe inspect` once to save the fixture")
def test_fixture_prompt_and_url_validation():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    snap = Snapshot(url=data["url"], title=data["title"], text=data["text"], anchors=[Anchor(**a) for a in data["anchors"]])
    prompt = build_prompt(snap)
    assert "<<" not in prompt and len(prompt) < 120_000
    job_links = [a.href for a in snap.anchors if "/jobs/info/" in a.href]
    assert job_links, "fixture has no job links"
    # every job link in the fixture survives validation; a mangled one does not
    ex = validate_urls([card(jobright_url=h) for h in job_links] + [card(jobright_url=job_links[0] + "x")], snap.hrefs())
    assert len(ex.jobs) == len(set(job_links)) and len(ex.dropped) == 1


def test_experience_filter_on_card():
    from jobpipe.config import settings

    limit = settings()["filters"]["max_required_years"]
    assert run.listing_reason(card(required_years=limit + 5), []).startswith(f"experience: requires {limit + 5}+ years")
    assert run.listing_reason(card(required_years=limit), []) is None
    assert run.listing_reason(card(required_years=None), []) is None


def test_sanitize_removes_contact_details():
    from jobpipe.scrape.browser import sanitize
    from jobpipe.facts import load

    c = load()["contact"]
    text = f"Welcome {c['name']}! {c['email']} {c['phone']} {c['name'].split()[0]}'s jobs"
    out = sanitize(text)
    assert c["email"] not in out and c["phone"] not in out and c["name"].split()[0] not in out


def test_page_filters_relevance_and_experience():
    from jobpipe.scrape.jd import JobPage, JobPageMeta

    def page(**meta):
        return JobPage(url=URL, text="Build backend services.", meta=JobPageMeta(company="Acme", title="Frontend Engineer", **meta))

    assert run.page_reason(card(), page()) is None
    assert "not a relevant role: front desk" in run.page_reason(card(), page(role_relevant=False, relevance_reason="front desk reception work"))
    assert "requires 8+ years" in run.page_reason(card(), page(required_years=8))
    assert run.page_reason(card(), page(required_years=3)) is None
    # sample profile graduated 2022-05
    assert "graduation: must graduate 2026-12 or later" in run.page_reason(card(), page(grad_earliest="2026-12", grad_latest="2027-08"))
    assert run.page_reason(card(), page(grad_earliest="2021-12", grad_latest="2022-08")) is None
