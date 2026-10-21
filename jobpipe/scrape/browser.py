"""Playwright access. Raw extraction only: visible text plus every anchor, no CSS selectors,
so layout changes on Jobright or employer sites do not break anything.

Jobright pages use the persistent profile in .browser-profile/ (you log in by hand once with
`jobpipe login`). Employer pages use a throwaway context.
"""

from __future__ import annotations

import logging
import random
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator
from urllib.parse import urlparse

from jobpipe.config import path, settings

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

ANCHORS_JS = """
() => Array.from(document.querySelectorAll('a[href]')).map(a => ({
  text: (a.innerText || a.getAttribute('aria-label') || a.title || '').trim().replace(/\\s+/g, ' '),
  href: a.href
})).filter(a => a.href && !a.href.startsWith('javascript:'))
"""


@dataclass
class Anchor:
    text: str
    href: str


@dataclass
class Snapshot:
    url: str          # final URL after redirects
    title: str
    text: str         # visible text of <body>
    anchors: list[Anchor] = field(default_factory=list)

    def hrefs(self) -> set[str]:
        return {a.href for a in self.anchors}


def is_jobright(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host == "jobright.ai" or host.endswith(".jobright.ai")


@contextmanager
def browser_context(persistent: bool, headless: bool = True) -> Iterator[object]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        if persistent:
            ctx = p.chromium.launch_persistent_context(
                str(path("browser_profile")), headless=headless, user_agent=USER_AGENT,
                viewport={"width": 1400, "height": 1000},
            )
            try:
                yield ctx
            finally:
                ctx.close()
        else:
            browser = p.chromium.launch(headless=headless)
            ctx = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1400, "height": 1000})
            try:
                yield ctx
            finally:
                ctx.close()
                browser.close()


def snapshot_page(page, url: str, timeout_s: int | None = None) -> Snapshot:
    timeout_ms = 1000 * (timeout_s or settings()["jobright"]["page_timeout_seconds"])
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 15000))
    except Exception:  # noqa: BLE001  (busy pages never go idle; content is there anyway)
        pass
    text = page.inner_text("body")
    anchors = [Anchor(**a) for a in page.evaluate(ANCHORS_JS)]
    return Snapshot(url=page.url, title=page.title(), text=text, anchors=anchors)


def snapshot(url: str) -> Snapshot:
    """Open one URL and return its raw text and anchors."""
    with browser_context(persistent=is_jobright(url)) as ctx:
        page = ctx.new_page()
        return snapshot_page(page, url)


class NotLoggedIn(Exception):
    pass


LOGIN_CUES = ("sign in", "log in", "login", "sign up", "continue with google")

def looks_logged_out(snap: Snapshot) -> bool:
    """Heuristic: a login wall has login cues in the first part of the text and few links."""
    head = snap.text[:3000].lower()
    job_links = [a for a in snap.anchors if "/jobs/" in a.href]
    on_login_url = any(p in snap.url.lower() for p in ("/login", "/signin", "/sign-in"))
    return on_login_url or (any(c in head for c in LOGIN_CUES) and len(job_links) < 3)


def login() -> None:
    """Open the persistent profile on Jobright, headed, and wait until the window is closed."""
    with browser_context(persistent=True, headless=False) as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(settings()["jobright"]["base_url"], wait_until="domcontentloaded")
        print("Log in to Jobright in the browser window, then close the window to finish.")
        try:
            ctx.wait_for_event("close", timeout=0)
        except Exception:  # noqa: BLE001  (closing the last window tears the context down)
            pass


# For every job link on screen, the text of the largest ancestor that still contains links to
# that one job only (its card). Structural, so no CSS class names are involved.
CARDS_JS = """
(pattern) => {
  const cards = {};
  for (const a of document.querySelectorAll('a[href]')) {
    if (!a.href.includes(pattern)) continue;
    let el = a;
    while (el.parentElement) {
      const hrefs = new Set(Array.from(el.parentElement.querySelectorAll('a[href]'))
        .map(x => x.href).filter(h => h.includes(pattern)));
      if (hrefs.size > 1 || el.parentElement === document.body) break;
      el = el.parentElement;
    }
    const text = (el.innerText || '').trim();
    if (!cards[a.href] || text.length > cards[a.href].length) cards[a.href] = text;
  }
  return cards;
}
"""

# Scrolls the largest scrollable element (Jobright's list lives in an inner container) and the
# window by most of a screen.
STEP_JS = """
() => {
  let best = document.scrollingElement;
  for (const el of document.querySelectorAll('*')) {
    const s = getComputedStyle(el);
    if ((s.overflowY === 'auto' || s.overflowY === 'scroll') &&
        el.scrollHeight - el.clientHeight > best.scrollHeight - best.clientHeight) best = el;
  }
  best.scrollTop += Math.max(300, best.clientHeight * 0.8);
  window.scrollBy(0, window.innerHeight * 0.8);
}
"""


class CardCollector:
    """Collects job cards from the Recommended tab while scrolling, handing them out in batches.

    The list is virtualized (only ~12 cards exist in the page at once), so cards are gathered at
    every scroll step and keyed by URL. Keep this page for the list only; open job pages in a
    different page so the scroll position survives between batches."""

    def __init__(self, page):
        cfg = settings()["jobright"]
        self.page = page
        self.pattern = cfg.get("job_link_pattern", "/jobs/info/")
        self.max_scrolls = cfg["max_scrolls"]
        snap = snapshot_page(page, cfg["recommended_url"])
        if looks_logged_out(snap):
            raise NotLoggedIn("Jobright shows a login page. Run `uv run jobpipe login` and log in first.")
        self.cards: dict[str, str] = {}
        self.anchors: dict[str, Anchor] = {a.href: a for a in snap.anchors}
        self.handed = 0
        self.exhausted = False

    def _collect(self) -> None:
        for href, text in self.page.evaluate(CARDS_JS, self.pattern).items():
            if len(text) > len(self.cards.get(href, "")):
                self.cards[href] = text
        for a in self.page.evaluate(ANCHORS_JS):
            self.anchors.setdefault(a["href"], Anchor(**a))

    def next_batch(self, n: int) -> Snapshot | None:
        """The next `n` cards not handed out yet (fewer at the end of the list), or None."""
        want = self.handed + n
        stalls = 0
        for _ in range(self.max_scrolls):
            before = len(self.cards)
            self._collect()
            if len(self.cards) >= want:
                break
            stalls = stalls + 1 if len(self.cards) == before else 0
            if stalls >= 5:
                self.exhausted = True
                break
            self.page.evaluate(STEP_JS)
            self.page.wait_for_timeout(900)
        batch = list(self.cards.items())[self.handed:want]
        self.handed += len(batch)
        log.info("collected %d job cards so far, handing out %d", len(self.cards), len(batch))
        if not batch:
            return None
        blocks = [f"=== JOB CARD ({href}) ===\n{text}" for href, text in batch]
        return Snapshot(url=self.page.url, title=self.page.title(), text="\n\n".join(blocks),
                        anchors=list(self.anchors.values()))


def load_recommended(page, limit: int) -> Snapshot:
    """The first `limit` job cards (used by `inspect`)."""
    snap = CardCollector(page).next_batch(limit)
    if snap is None:
        raise ValueError("no job cards found on the Recommended page")
    return snap


def sanitize(text: str) -> str:
    """Remove personal details before saving a page as a test fixture."""
    from jobpipe.facts import load

    c = load()["contact"]
    for value, repl in ((c["name"], "Test User"), (c["email"], "user@example.com"), (c["phone"], "000-000-0000")):
        text = text.replace(value, repl)
    for part in c["name"].split():
        text = text.replace(part, "Test")
    return text


def polite_pause() -> None:
    lo, hi = settings()["jobright"]["page_delay_seconds"]
    time.sleep(random.uniform(lo, hi))
