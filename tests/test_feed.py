"""jobpipe feed: pipeline.md format, URL dedupe, append-only writes, and the feed loop with the
browser and LLM replaced by fakes. Uses the fictional sample profile and temp folders only."""

import contextlib
import re

import pytest

from jobpipe import feed
from jobpipe.scrape.browser import Snapshot
from jobpipe.config import settings
from jobpipe.scrape.extract import Extraction, JobCard
from jobpipe.scrape.jd import JobPage, JobPageMeta

SKELETON = feed.SKELETON


# ---------------------------------------------------------------- URLs


def test_normalize_url_matches_career_ops_rules():
    n = feed.normalize_url
    assert n("http://Jobs.Example.com./a/b/?utm_source=x&b=2&a=1#frag") == "https://jobs.example.com/a/b?a=1&b=2"
    assert n("https://boards.greenhouse.io/acme/jobs/1?gh_jid=77&gh_src=abc") == "https://boards.greenhouse.io/acme/jobs/1?gh_jid=77"
    assert n("https://jobs.ashbyhq.com/plaid/8de9?jr_id=6a96fba6") == "https://jobs.ashbyhq.com/plaid/8de9"
    assert n("https://x.com/") == "https://x.com/"          # root slash stays
    assert n("N/A") == "" and n("") == "" and n("local:jds/a.md") == "" and n("ftp://x.com/a") == ""


def test_clean_url_removes_tracking_only_and_keeps_order():
    assert feed.clean_url("https://x.com/p?z=1&utm_medium=m&gh_jid=5&jr_id=9&a=2") == "https://x.com/p?z=1&gh_jid=5&a=2"
    assert feed.clean_url("https://x.com/p?utm_source=a") == "https://x.com/p"
    assert feed.clean_url("  https://x.com/p  ") == "https://x.com/p"
    assert feed.clean_url("not a url") == "not"


# ---------------------------------------------------------------- line format


def test_format_line_shapes():
    assert feed.format_line("https://a.com/1", "Acme", "Backend Engineer") == "- [ ] https://a.com/1 | Acme | Backend Engineer"
    assert feed.format_line("https://a.com/1", "Acme", "Backend Engineer", "Remote (US)", "jobright") == \
        "- [ ] https://a.com/1 | Acme | Backend Engineer | Remote (US) | note: jobright"
    # no location: the labeled note still rides on the 3-column row
    assert feed.format_line("https://a.com/1", "Acme", "SWE", "", "jobright") == "- [ ] https://a.com/1 | Acme | SWE | note: jobright"
    # sanitized like scan.mjs sanitizeMarkdownField: pipes and newlines can't break the columns
    line = feed.format_line("https://a.com/1", "A | B", "Dev [II]\n(Platform)", "NYC | Remote")
    assert line == r"- [ ] https://a.com/1 | A / B | Dev \[II\] (Platform) | NYC / Remote"
    assert "\n" not in line


def test_format_line_is_parsed_like_a_scanner_line():
    line = feed.format_line("https://boards.greenhouse.io/acme/jobs/9?gh_jid=9", "Acme", "SWE", "Remote", "jobright")
    assert re.match(r"^- \[ \]\s+", line)                                   # PIPELINE_CHECKBOX_RE
    assert re.search(r"https?://[^\s|]+", line).group(0) == "https://boards.greenhouse.io/acme/jobs/9?gh_jid=9"
    assert line.split(" | ")[-1] == "note: jobright"


# ---------------------------------------------------------------- inserting into pipeline.md


def test_insert_into_empty_skeleton():
    out = feed.insert_pending(SKELETON, ["- [ ] https://a.com/1 | A | T"])
    assert out == SKELETON.replace("## Pending\n", "## Pending\n- [ ] https://a.com/1 | A | T\n")
    assert out.index("## Pending") < out.index("- [ ] https://a.com") < out.index("## Processed")


def test_insert_is_append_only_inside_pending():
    old = (
        "# Pipeline\n\n## Pending\n- [ ] https://a.com/1 | A | T\n- [!] https://b.com/2 — Error: login required\n\n"
        "## Processed\n- [x] #1 | https://c.com/3 | C | R | 4.2/5 | PDF ✅\n"
    )
    new = feed.insert_pending(old, ["- [ ] https://d.com/4 | D | T"])
    old_lines, new_lines = old.splitlines(), new.splitlines()
    assert [ln for ln in new_lines if ln != "- [ ] https://d.com/4 | D | T"] == old_lines   # nothing else touched or reordered
    assert new_lines.index("- [ ] https://d.com/4 | D | T") == new_lines.index("- [!] https://b.com/2 — Error: login required") + 1
    assert new.endswith(old.split("## Processed")[1])                                            # Processed section byte-identical


def test_insert_keeps_crlf_and_spanish_headers():
    old = "# P\r\n\r\n## Pendientes\r\n- [ ] https://a.com/1\r\n\r\n## Procesadas\r\n"
    new = feed.insert_pending(old, ["- [ ] https://b.com/2"])
    assert new == "# P\r\n\r\n## Pendientes\r\n- [ ] https://a.com/1\r\n- [ ] https://b.com/2\r\n\r\n## Procesadas\r\n"


def test_insert_creates_missing_pending_section_before_processed():
    new = feed.insert_pending("# P\n\n## Processed\n- [x] done\n", ["- [ ] https://a.com/1"])
    assert new.index("## Pending") < new.index("- [ ] https://a.com/1") < new.index("## Processed")
    assert new.endswith("## Processed\n- [x] done\n")


# ---------------------------------------------------------------- writing: dedupe, atomic, lock


def test_append_pending_writes_once_and_never_rewrites_existing_text(tmp_path):
    p = tmp_path / "data" / "pipeline.md"
    p.parent.mkdir()
    p.write_bytes("## Pending\n- [ ] https://a.com/1?utm_source=x | A | T\n\n## Processed\n".encode())
    before = p.read_bytes()

    assert feed.append_pending(p, "https://b.com/2", "- [ ] https://b.com/2 | B | T") is True
    after = p.read_bytes()
    assert after.startswith(b"## Pending\n- [ ] https://a.com/1?utm_source=x | A | T\n- [ ] https://b.com/2 | B | T")
    assert after.replace(b"- [ ] https://b.com/2 | B | T\n", b"") == before

    # same posting, different spelling (tracking param, http, trailing slash): refused, file untouched
    snap = p.read_bytes()
    assert feed.append_pending(p, "http://A.com/1/", "- [ ] http://A.com/1/ | A | T") is False
    assert feed.append_pending(p, "https://b.com/2?gh_src=z", "- [ ] x | B | T") is False
    assert p.read_bytes() == snap
    assert not list(p.parent.glob("*.tmp-*")) and not (p.parent / "pipeline.md.lock").exists()


def test_append_pending_creates_the_file_from_the_skeleton(tmp_path):
    p = tmp_path / "data" / "pipeline.md"
    assert feed.append_pending(p, "https://a.com/1", "- [ ] https://a.com/1 | A | T")
    assert p.read_text(encoding="utf-8") == SKELETON.replace("## Pending\n", "## Pending\n- [ ] https://a.com/1 | A | T\n")


def test_lock_is_a_directory_with_owner_and_blocks_a_second_writer(tmp_path):
    p = tmp_path / "pipeline.md"
    p.write_text(SKELETON, encoding="utf-8")
    with feed.PipelineLock(p) as lock:
        assert (lock.dir / "owner.json").exists() and lock.dir.name == "pipeline.md.lock"
        with pytest.raises(feed.PipelineLockTimeout):
            with feed.PipelineLock(p, timeout=0.3):
                pass
        assert lock.dir.exists()       # the failed second writer did not remove the first one's lock
    assert not lock.dir.exists()


# ---------------------------------------------------------------- reading what is already known


def make_career_ops(tmp_path):
    root = tmp_path / "career-ops"
    (root / "data").mkdir(parents=True)
    (root / "reports").mkdir()
    return feed.CareerOps(root=root, data=root)


def test_known_urls_come_from_pipeline_tracker_and_reports(tmp_path):
    co = make_career_ops(tmp_path)
    co.pipeline.write_text("## Pending\n- [ ] https://a.com/1?utm_campaign=x | A | T\n## Processed\n"
                           "- [x] #3 | https://b.com/2 | B | R | 4/5 | PDF ✅\n- [x] ~~https://c.com/3 | C | R~~ — expired\n", encoding="utf-8")
    (co.root / "data" / "applications.md").write_text("| 1 | 2026-10-01 | D | R | [ashby](https://d.com/4/) | Applied |\n", encoding="utf-8")
    (co.reports / "001-e.md").write_text("# Report\n\n**URL:** https://e.com/5?gh_jid=9\n", encoding="utf-8")
    known = feed.known_urls(co)
    assert {known[feed.normalize_url(u)] for u in ("https://a.com/1", "https://b.com/2", "https://c.com/3", "https://d.com/4", "https://e.com/5?gh_jid=9")} == \
        {"pipeline.md", "applications.md", "reports/001-e.md"}
    assert feed.normalize_url("https://e.com/5") not in known      # gh_jid is functional: a different posting


def test_known_urls_with_no_career_ops_files_is_empty(tmp_path):
    assert feed.known_urls(make_career_ops(tmp_path)) == {}


def test_career_ops_blacklist(tmp_path):
    co = make_career_ops(tmp_path)
    co.blacklist.write_text("# Company Blacklist\n\n| Company | Since | Scope | Reason |\n|---|---|---|---|\n"
                            "| Acme Corp | 2026-01-01 | company | bad |\n| ibm.com | 2026-01-01 | domain | no |\n", encoding="utf-8")
    rows = feed.read_career_ops_blacklist(co)
    assert feed.blacklist_reason("Acme Corp.", "https://x.com/1", rows) == "blacklisted (career-ops): Acme Corp: bad"
    assert feed.blacklist_reason("Other", "https://jobs.ibm.com/1", rows).startswith("blacklisted (career-ops): ibm.com")
    assert feed.blacklist_reason("Other", "https://notibm.com/1", rows) is None
    assert feed.blacklist_reason("Other", "https://x.com/1", []) is None


def test_career_ops_path_must_be_configured(monkeypatch, tmp_path):
    real = settings()
    monkeypatch.setattr(feed, "settings", lambda: {**real, "career_ops": {"path": ""}})
    with pytest.raises(feed.CareerOpsNotConfigured):
        feed.career_ops()
    monkeypatch.setattr(feed, "settings", lambda: {**real, "career_ops": {"path": str(tmp_path / "missing")}})
    with pytest.raises(feed.CareerOpsNotConfigured):
        feed.career_ops()


def test_check_writable_does_not_modify_pipeline(tmp_path):
    co = make_career_ops(tmp_path)
    co.pipeline.write_text(SKELETON, encoding="utf-8")
    assert feed.check_writable(co)[0] is True
    assert co.pipeline.read_text(encoding="utf-8") == SKELETON
    co.pipeline.unlink()
    assert feed.check_writable(co)[0] is True and not co.pipeline.exists()


# ---------------------------------------------------------------- the feed loop with fakes


def jr(i):
    return f"https://jobright.ai/jobs/info/{i:024x}"


def card(i, company, title="Software Engineer", **kw):
    return JobCard(company=company, title=title, jobright_url=jr(i), **kw)


class FakeFeedEnv:
    """Replaces the browser, the card reader and the job-page reader. `pages` maps a Jobright URL to
    the (meta kwargs, employer URL) the 'LLM' would return."""

    def __init__(self, monkeypatch, tmp_path, cards, pages):
        self.co = make_career_ops(tmp_path)
        self.co.pipeline.write_text(SKELETON, encoding="utf-8")
        self.analyzed = []
        real = settings()
        monkeypatch.setattr(feed, "settings", lambda: {**real, "career_ops": {"path": str(self.co.root), "source_note": "jobright"}})
        real_path = feed.path
        monkeypatch.setattr(feed, "path", lambda key: tmp_path / "applications" if key == "applications" else real_path(key))
        monkeypatch.setattr(feed, "_applied_rows", lambda s: [])
        monkeypatch.setattr(feed, "_blacklist", lambda s: [])
        monkeypatch.setattr(feed, "polite_pause", lambda: None)
        monkeypatch.setattr(feed.filters, "path", lambda key: tmp_path / "applications" if key == "applications" else real_path(key))

        class Ctx:
            def new_page(self_):
                return object()

        @contextlib.contextmanager
        def browser_context(persistent=True):
            yield Ctx()

        class Collector:
            def __init__(self_, page):
                self_.handed, self_.done = 0, False

            def next_batch(self_, n):
                if self_.done:
                    return None
                self_.done, self_.handed = True, len(cards)
                return Snapshot(url="https://jobright.ai/jobs/recommend", title="", text="", anchors=[])

        monkeypatch.setattr(feed, "browser_context", browser_context)
        monkeypatch.setattr(feed, "CardCollector", Collector)
        monkeypatch.setattr(feed, "extract_jobs", lambda snap: Extraction(jobs=list(cards)))
        monkeypatch.setattr(feed, "snapshot_page", lambda page, url: url)

        def analyze(url):
            self.analyzed.append(url)
            meta, _ = pages[url]
            return JobPage(url=url, text="Build services.", meta=JobPageMeta(**meta))

        def use_original(job, page):
            employer = pages[job.url][1]
            if employer:
                job.original_url, job.text_source = employer, "original"
            return job

        monkeypatch.setattr(feed, "analyze", analyze)
        monkeypatch.setattr(feed, "use_original_posting", use_original)


def test_feed_day_end_to_end(monkeypatch, tmp_path):
    cards = [
        card(1, "Acme"),                                                 # passes
        card(2, "Globex", reposted=True),                                # skipped on the card, no page read
        card(3, "Initech"),                                              # employer URL already in the tracker -> duplicate
        card(4, "Umbrella"),                                             # page says it is not a relevant role
        card(5, "Hooli"),                                                # no employer link found -> Jobright URL with a flag
        card(6, "Revature"),                                             # consultancy denylist (config/consultancy_denylist.txt)
    ]
    pages = {
        jr(1): (dict(company="Acme", title="Software Engineer", location="Remote (US)"), "https://boards.greenhouse.io/acme/jobs/1?gh_jid=1&gh_src=zz&jr_id=abc"),
        jr(3): (dict(company="Initech", title="Software Engineer"), "https://initech.example/jobs/3?utm_source=jobright"),
        jr(4): (dict(company="Umbrella", title="Software Engineer", role_relevant=False, relevance_reason="sales role"), "https://umbrella.example/jobs/4"),
        jr(5): (dict(company="Hooli", title="Software Engineer"), ""),
    }
    env = FakeFeedEnv(monkeypatch, tmp_path, cards, pages)
    (env.co.root / "data" / "applications.md").write_text("| 1 | 2026-10-01 | Initech | SWE | https://initech.example/jobs/3 | Applied |\n", encoding="utf-8")

    s = feed.feed_day(10, echo=lambda m: None)

    text = env.co.pipeline.read_text(encoding="utf-8")
    pending = text.split("## Pending")[1].split("## Processed")[0].strip().splitlines()
    assert pending == [
        "- [ ] https://boards.greenhouse.io/acme/jobs/1?gh_jid=1 | Acme | Software Engineer | Remote (US) | note: jobright",
        f"- [ ] {jr(5)} | Hooli | Software Engineer | note: jobright (employer link not found)",
    ]
    assert [r["company"] for r in s.fed] == ["Acme", "Hooli"]
    assert [r["company"] for r in s.duplicates] == ["Initech"] and "applications.md" in s.duplicates[0]["reason"]
    reasons = {r["company"]: r["reason"] for r in s.skipped}
    assert "reposted" in reasons["Globex"] and "not a relevant role" in reasons["Umbrella"] and "denylist" in reasons["Revature"]
    assert jr(2) not in env.analyzed and jr(6) not in env.analyzed         # card-level skips cost no job-page LLM call
    assert (tmp_path / "applications").glob("*/feed.md") and list((tmp_path / "applications").glob("*/feed.md"))

    # second run over the same list: everything decided before is skipped without reading a page
    env.analyzed.clear()
    before = env.co.pipeline.read_bytes()
    s2 = feed.feed_day(10, echo=lambda m: None)
    assert env.analyzed == [] and s2.fed == [] and len(s2.duplicates) >= 4
    assert env.co.pipeline.read_bytes() == before


def test_feed_day_dry_run_writes_nothing(monkeypatch, tmp_path):
    pages = {jr(1): (dict(company="Acme", title="Software Engineer"), "https://acme.example/jobs/1")}
    env = FakeFeedEnv(monkeypatch, tmp_path, [card(1, "Acme")], pages)
    s = feed.feed_day(5, dry_run=True, echo=lambda m: None)
    assert [r["company"] for r in s.fed] == ["Acme"]
    assert env.co.pipeline.read_text(encoding="utf-8") == SKELETON
    assert not (tmp_path / "applications").exists()


def test_feed_day_stops_at_limit_and_honors_exclude(monkeypatch, tmp_path):
    cards = [card(i, f"Co{i}") for i in range(1, 6)]
    pages = {jr(i): (dict(company=f"Co{i}", title="Software Engineer"), f"https://co{i}.example/jobs/{i}") for i in range(1, 6)}
    env = FakeFeedEnv(monkeypatch, tmp_path, cards, pages)
    s = feed.feed_day(2, echo=lambda m: None, exclude=["Co1"])
    assert [r["company"] for r in s.fed] == ["Co2", "Co3"]
    assert any("excluded" in r["reason"] for r in s.skipped)
    assert env.analyzed == [jr(2), jr(3)]
