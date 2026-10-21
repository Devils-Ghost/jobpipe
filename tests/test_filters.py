import pytest

from jobpipe import filters
from jobpipe.pipeline import job_key


@pytest.mark.parametrize(
    "text",
    [
        "We are unable to sponsor visas for this role.",
        "This position does not offer visa sponsorship.",
        "Candidates must be authorized to work in the United States without the need for current or future visa sponsorship.",
        "Must be a U.S. citizen.",
        "US citizenship is required.",
        "Active Secret clearance required.",
        "Ability to obtain and maintain a security clearance.",
        "TS/SCI clearance",
        "No sponsorship available.",
        "We will not sponsor H-1B.",
        "No H1B",
        "H1B sponsorship not available",
        "This company doesn't sponsor",
    ],
)
def test_sponsorship_blocks(text):
    assert filters.sponsorship_reason(text)


@pytest.mark.parametrize(
    "text",
    [
        "We sponsor visas for qualified candidates.",
        "Visa sponsorship is available.",
        "Work on cloud security and identity.",
        "H1B Sponsor Likely",
        "Has sponsored H1B in the past",
    ],
)
def test_sponsorship_allows(text):
    assert filters.sponsorship_reason(text) is None


def test_sponsorship_from_llm_note():
    assert "llm note" in filters.sponsorship_reason("nothing here", "Employer cannot sponsor H1B")


def test_consultancy():
    names = ["Infosys", "BeaconFire Inc.", "TCS"]
    assert filters.consultancy_reason("Infosys Limited", "direct", "", names) == "denylist: Infosys"
    assert filters.consultancy_reason("Beaconfire", "direct", "", names)
    assert filters.consultancy_reason("Acme", "staffing_agency", "", names) == "employer type: staffing_agency"
    assert filters.consultancy_reason("Acme", "direct", "Hiring on behalf of our client, a bank.", names)
    assert filters.consultancy_reason("Acme", "direct", "C2C or W2 contract", names)
    assert filters.consultancy_reason("Deloitte", "big_consulting", "Build great software.", names) is None
    # 'bench' must not match inside other words like 'benchmark'
    assert filters.consultancy_reason("Acme", "direct", "Run benchmarks.", names) is None
    # short denylist names must not fuzzy-match unrelated companies
    assert filters.consultancy_reason("TCSystems Health", "direct", "", names) is None


def test_reposted():
    assert filters.reposted_reason("Reposted 2 days ago")
    assert filters.reposted_reason("", flag=True)
    assert filters.reposted_reason("Posted 2 days ago") is None


def test_processed(tmp_path):
    (tmp_path / "2026-10-12" / "Acme_Engineer_abc123").mkdir(parents=True)
    assert filters.processed_reason("abc123", tmp_path)
    assert filters.processed_reason("zzz", tmp_path) is None


def test_applied_fuzzy():
    rows = [("Jobright", "Acme Inc.", "Software Engineer II"), ("Linkedin", "Globex", "Backend Engineer")]
    assert filters.applied_reason("Acme", "Software Engineer II", rows, 90)
    assert filters.applied_reason("Acme", "Data Scientist", rows, 90) is None


def test_job_key():
    assert job_key("https://jobright.ai/jobs/info/68f0a1b2c3d4e5f6a7b8c9d0") == "68f0a1b2c3d4e5f6a7b8c9d0"
    k = job_key("https://boards.greenhouse.io/acme/jobs/123")
    assert k.startswith("u") and k == job_key("https://boards.greenhouse.io/acme/jobs/123")


def test_reapply_after_window():
    import datetime as dt

    today = dt.date(2026, 10, 15)
    rows = [("Jobright", "IMC Trading", "Software Engineer, Early Career", dt.date(2026, 5, 24))]
    assert filters.applied_reason("IMC Trading", "Software Engineer, Early Career", rows, 90, today) is None  # 144 days ago (window: 30 days)
    recent = [("Jobright", "IMC Trading", "Software Engineer, Early Career", dt.date(2026, 9, 30))]
    assert "on 2026-09-30" in filters.applied_reason("IMC Trading", "Software Engineer, Early Career", recent, 90, today)
    undated = [("Jobright", "IMC Trading", "Software Engineer, Early Career", None)]
    assert filters.applied_reason("IMC Trading", "Software Engineer, Early Career", undated, 90, today)


def test_exclude():
    assert filters.excluded_reason("Amazon Web Services (AWS)", ["Amazon"]) == "excluded (--exclude Amazon)"
    assert filters.excluded_reason("amazon", ["Amazon"])
    assert filters.excluded_reason("Amazonia Labs", ["Amazon"]) is None  # whole words only
    assert filters.excluded_reason("Meta", []) is None


def test_sheet_date_parsing():
    import datetime as dt

    from jobpipe.tracker import parse_date

    assert parse_date("05/24/2026") == dt.date(2026, 5, 24)
    assert parse_date("2026-05-24") == dt.date(2026, 5, 24)
    assert parse_date("") is None and parse_date("soon") is None


def test_blacklisted_reason_whole_words_with_reason():
    bl = [("Garmin", "Auto rejection"), ("R3", ""), ("Southwest Airlines", "Auto rejection")]
    assert filters.blacklisted_reason("Garmin Ltd.", bl) == "blacklisted (sheet): Garmin: Auto rejection"
    assert filters.blacklisted_reason("R3", bl) == "blacklisted (sheet): R3"
    assert filters.blacklisted_reason("R3D Systems", bl) is None  # whole words only
    assert filters.blacklisted_reason("Southwest", bl) is None
    assert filters.blacklisted_reason("Acme", []) is None


def test_read_blacklist_from_sheet_tab(monkeypatch):
    from jobpipe import tracker

    class Tab:
        def get_all_values(self):
            return [["Company", "", "Blacklisted", "Why"], ["Amazon", "", "Waystar", "No H1B"],
                    ["Google", "", "", ""], ["", "", "Garmin", ""]]

    class Book:
        def worksheet(self, name):
            assert name == "Sheet4"
            return Tab()

    monkeypatch.setattr(tracker, "settings", lambda: {"tracker": {"type": "google_sheet", "google_sheet": {
        "blacklist": {"tab": "Sheet4", "column": "C", "reason_column": "D"}}}})
    monkeypatch.setattr(tracker, "_sheet", lambda: Book())
    assert tracker.read_blacklist() == [("Waystar", "No H1B"), ("Garmin", "")]
    monkeypatch.setattr(tracker, "settings", lambda: {"tracker": {"type": "csv"}})
    assert tracker.read_blacklist() == []  # only for the sheet tracker


def test_graduation_window():
    import datetime as dt

    g = lambda *a, **k: filters.graduation_reason(*a, graduated="2026-05", today=dt.date(2026, 10, 19), **k)
    assert "2026-12 or later" in g("2026-12", "2027-08")
    assert g("2027-01", "")
    assert "by 2025-12" in g("", "2025-12")
    assert g("2025-12", "2026-08") is None
    assert g("", "") is None
    assert "current enrollment" in g(requires_enrollment=True)
    assert filters.graduation_reason(requires_enrollment=True, graduated="2026-05", today=dt.date(2026, 3, 1)) is None
    assert filters.graduation_reason("2027-05", graduated="") is None      # setting off
    assert g("Summer 2027") is None                                         # unparseable -> no skip
