import pytest

from jobpipe.scrape.jd import JobPage, JobPageMeta, looks_like_jd, posting_url, strip_jobright_noise, use_original_posting


@pytest.mark.parametrize(
    "apply, posting",
    [
        ("https://jobs.ashbyhq.com/chainalysis-careers/05ebe657-fb6e/application?jr_id=6acf",
         "https://jobs.ashbyhq.com/chainalysis-careers/05ebe657-fb6e?jr_id=6acf"),
        ("https://jobs.ashbyhq.com/acme/abc/application", "https://jobs.ashbyhq.com/acme/abc"),
        ("https://jobs.lever.co/acme/1234-5678/apply?lever-source=jobright", "https://jobs.lever.co/acme/1234-5678?lever-source=jobright"),
        ("https://job-boards.greenhouse.io/embed/job_app?for=flexe&token=4713328006&jr_id=6aa8",
         "https://job-boards.greenhouse.io/flexe/jobs/4713328006"),
        ("https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/Seattle/SWE_R123/apply/applyManually",
         "https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/Seattle/SWE_R123"),
        # already a posting page: unchanged
        ("https://fairsquare.breezy.hr/p/cd90b79a0aa2-software-engineer-ii?jr_id=6ab2",
         "https://fairsquare.breezy.hr/p/cd90b79a0aa2-software-engineer-ii?jr_id=6ab2"),
        ("https://job-boards.greenhouse.io/flexe/jobs/4713328006", "https://job-boards.greenhouse.io/flexe/jobs/4713328006"),
    ],
)
def test_posting_url(apply, posting):
    assert posting_url(apply) == posting


JD = ("About the role. You will build backend services. Responsibilities: design APIs. "
      "Qualifications: 3+ years experience with Java. Skills: SQL. ") * 12


def test_looks_like_jd():
    assert looks_like_jd(JD)
    form = "Name Email Resume Upload File Phone LinkedIn Profile URL Submit application " * 30
    assert not looks_like_jd(form)
    assert not looks_like_jd("Responsibilities and qualifications")  # too short


def test_strip_jobright_noise_removes_people_panel():
    page = ("Chainalysis\nSoftware Engineer II\nH1B Sponsor Likely\nInsider Connection @Chainalysis\n"
            "Jane Example & 4 connections\nPreviously@Acme\nFrom Your School\nJohn Sample\nFind Any Email\n"
            "Responsibilities\nBuild backend services\nRequired\nJava\n")
    out = strip_jobright_noise(page)
    assert "Jane Example" not in out and "John Sample" not in out and "Insider Connection" not in out
    assert out.startswith("Chainalysis\nSoftware Engineer II") and "Responsibilities\nBuild backend services" in out
    assert strip_jobright_noise("no panel here\nResponsibilities\n") == "no panel here\nResponsibilities\n"


class FakePage:
    def __init__(self, text=None, error=None):
        self.text, self.error, self.visited = text, error, []


def _job(apply_url="https://jobs.ashbyhq.com/acme/abc/application"):
    return JobPage(url="https://jobright.ai/jobs/info/x", text="Jobright text\nInsider Connection\nJane Example\nResponsibilities\nX\n",
                   meta=JobPageMeta(company="Acme", title="SWE"), apply_url=apply_url)


def test_use_original_posting(monkeypatch):
    import jobpipe.scrape.jd as jd
    from jobpipe.scrape.browser import Snapshot

    visited = []

    def fake_snapshot(page, url):
        visited.append(url)
        if page.error:
            raise page.error
        return Snapshot(url=url, title="", text=page.text, anchors=[])

    monkeypatch.setattr(jd, "snapshot_page", fake_snapshot)

    job = use_original_posting(_job(), FakePage(text=JD))
    assert job.text_source == "original" and job.text == JD and visited[-1] == "https://jobs.ashbyhq.com/acme/abc"

    job = use_original_posting(_job(), FakePage(text="Name Email Submit"))
    assert job.text_source == "jobright" and "did not look like" in job.source_note and "Jane Example" not in job.text

    job = use_original_posting(_job(), FakePage(error=TimeoutError("slow")))
    assert job.text_source == "jobright" and "could not be loaded" in job.source_note

    job = use_original_posting(_job(apply_url=""), FakePage(text=JD))
    assert job.text_source == "jobright" and "no original posting link" in job.source_note


def test_smartrecruiters_text_and_api_path(monkeypatch):
    import jobpipe.scrape.jd as jd

    data = {"name": "Software Engineer", "location": {"city": "San Jose", "region": "CA"},
            "jobAd": {"sections": {
                "jobDescription": {"title": "Job Description", "text": "<p>You will build &amp; test <b>firmware</b>.</p><ul><li>Write C</li><li>Debug</li></ul>"},
                "qualifications": {"title": "Qualifications", "text": "<p>Required: 0+ years</p>"}}}}
    text = jd.smartrecruiters_text(data)
    assert text.splitlines()[:2] == ["Software Engineer", "San Jose, CA"]
    assert "You will build & test firmware." in text and "- Write C" in text and "Qualifications" in text

    monkeypatch.setattr(jd, "ats_api_text", lambda url: (JD, "https://api.example/x"))
    monkeypatch.setattr(jd, "snapshot_page", lambda page, url: (_ for _ in ()).throw(AssertionError("page not needed")))
    job = use_original_posting(_job("https://jobs.smartrecruiters.com/WesternDigital/744000138717897-software-engineer"), FakePage())
    assert job.text_source == "original" and job.text == JD


def test_ats_api_only_for_known_boards():
    from jobpipe.scrape.jd import ats_api_text

    assert ats_api_text("https://jobs.ashbyhq.com/acme/abc") is None
