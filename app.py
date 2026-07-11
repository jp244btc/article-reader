r"""
Article Reader — a local web app that fetches a URL and shows a clean,
popup-free, reader-mode version of the article.

Two engines:

  FAST (default) — tried in order until enough text is recovered:
    1. Plain HTTP fetch      - fastest; works when the article text is in the
                               server-sent HTML (e.g. Daily Mail, often NYT).
    2. Headless browser,     - loads with JavaScript OFF, so a paywall/consent
       JavaScript disabled     overlay is never injected.
    3. Headless browser,     - loads with JS on, then strips overlay elements
       overlay removal         and restores scrolling before capturing.
    4. Wayback Machine       - last resort: pull the newest archived snapshot.

  MY CHROME (opt-in checkbox) — for sites that block bots AND paywall content
  (the New York Times does both). Drives a REAL Chrome you can log into once,
  so you see exactly what your own authenticated browser can access.

In every case the HTML is run through readability extraction to isolate the
article body.

Run with:  .venv\Scripts\python.exe app.py
Then open: http://127.0.0.1:5000  (opens automatically)
"""

import os
import socket
import subprocess
import threading
import time
import webbrowser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request
from readability import Document

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT = True
except Exception:
    PLAYWRIGHT = False

app = Flask(__name__)

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

MIN_CHARS = 1200          # below this, try the next (heavier) tier
DEBUG_PORT = 9222         # remote-debugging port for the "My Chrome" engine

# Domains that block automated browsers at the network level (bot walls).
# Plain HTTP and headless fetches are dead on arrival there — the real-Chrome
# engine is engaged automatically and the doomed tiers are skipped.
HARD_WALL_DOMAINS = {"nytimes.com", "washingtonpost.com"}


def _needs_real_browser(url):
    host = urlparse(url).netloc.split(":")[0].lower()
    return any(host == d or host.endswith("." + d) for d in HARD_WALL_DOMAINS)


# A result on a known-paywalled domain with fewer than this many characters is
# probably a teaser (even if it cleared MIN_CHARS), so the archive.today link is
# offered. Full articles run well into the thousands of characters.
TEASER_SUSPECT_CHARS = 4000


def _archive_url(url):
    """Link to the newest archive.today snapshot of a page (a frozen copy that
    is often complete when a live logged-out fetch only yields a teaser)."""
    return "https://archive.ph/newest/" + url
CHROME_PROFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome-profile")

_CHROME_CANDIDATES = [
    os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
]
CHROME_EXE = next((c for c in _CHROME_CANDIDATES if os.path.exists(c)), None)

# JavaScript run inside the browser to tear down paywall/consent overlays and
# re-enable scrolling before the page is captured.
STRIP_JS = r"""() => {
  const selectors = [
    '#gateway-content', '#gateway-ab-testing-wrapper',
    '[data-testid="inline-message"]', '[data-testid="gateway-content"]',
    '[id*="paywall" i]', '[class*="paywall" i]',
    '[id*="gateway" i]', '[class*="gateway" i]',
    '[id*="piano" i]', '[class*="piano" i]',
    '.mol-consent', '#mol-consent', '[data-project*="privacy" i]',
    '#onetrust-consent-sdk', '.qc-cmp2-container', '.fc-consent-root',
    'div[role="dialog"]', '.modal-backdrop', '.tp-modal', '.tp-backdrop'
  ];
  // Never remove an element that WRAPS real article content — some sites
  // (e.g. Washington Post) nest the story inside paywall-named containers.
  const wrapsContent = el =>
    el.querySelector('article, main') || el.querySelectorAll('p').length > 8;

  selectors.forEach(s => document.querySelectorAll(s).forEach(el => {
    if (!wrapsContent(el)) el.remove();
  }));

  document.querySelectorAll('body *').forEach(el => {
    const cs = getComputedStyle(el);
    if (cs.position === 'fixed' || cs.position === 'sticky') {
      const z = parseInt(cs.zIndex) || 0;
      const r = el.getBoundingClientRect();
      if ((z >= 100 || r.height > window.innerHeight * 0.6) && !wrapsContent(el))
        el.remove();
    }
  });

  [document.documentElement, document.body].forEach(el => {
    el.style.setProperty('overflow', 'auto', 'important');
    el.style.setProperty('position', 'static', 'important');
    el.style.setProperty('height', 'auto', 'important');
  });

  document.querySelectorAll('*').forEach(el => {
    const cs = getComputedStyle(el);
    if (cs.filter && cs.filter.includes('blur'))
      el.style.setProperty('filter', 'none', 'important');
    if (parseFloat(cs.opacity) < 0.3)
      el.style.setProperty('opacity', '1', 'important');
    if (cs.maxHeight && cs.maxHeight !== 'none' && el.querySelector('p'))
      el.style.setProperty('max-height', 'none', 'important');
  });
}"""


# --------------------------------------------------------------------------- #
#  Extraction helpers
# --------------------------------------------------------------------------- #
def _text_len(html):
    return len(BeautifulSoup(html, "lxml").get_text(strip=True))


def _readability(html):
    doc = Document(html)
    return doc.short_title(), doc.summary(html_partial=True)


def _container_fallback(html):
    """Extract the largest <article>/<main> region directly.

    Readability sometimes under-extracts on heavily-templated pages (e.g.
    Washington Post); the semantic container is a more generous cut.
    Returns (content_html, text_len) or (None, 0).
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "header",
                     "footer", "aside", "button", "form"]):
        tag.decompose()
    best_el, best_len = None, 0
    for el in soup.select("article, main, [role='main']"):
        n = len(el.get_text(strip=True))
        if n > best_len:
            best_el, best_len = el, n
    if best_el is None:
        return None, 0
    return str(best_el), best_len


def _clean_and_absolutize(content_html, base_url):
    """Strip scripts, fix lazy-loaded images, make links/images absolute."""
    soup = BeautifulSoup(content_html, "lxml")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    for img in soup.find_all("img"):
        src = (
            img.get("src")
            or img.get("data-src")
            or img.get("data-src-large")
            or img.get("data-lazy-src")
            or ""
        )
        if not src and img.get("data-srcset"):
            src = img["data-srcset"].split(",")[0].strip().split(" ")[0]
        if not src and img.get("srcset"):
            src = img["srcset"].split(",")[0].strip().split(" ")[0]
        if src:
            img["src"] = urljoin(base_url, src)
        for attr in ("data-src", "data-srcset", "srcset", "loading", "data-lazy-src"):
            if img.has_attr(attr):
                del img[attr]

    for a in soup.find_all("a", href=True):
        a["href"] = urljoin(base_url, a["href"])
        a["target"] = "_blank"
        a["rel"] = "noopener noreferrer"

    return str(soup)


# --------------------------------------------------------------------------- #
#  Fetchers
# --------------------------------------------------------------------------- #
def _fetch_http(url):
    headers = {
        "User-Agent": DESKTOP_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    r = requests.get(url, headers=headers, timeout=25)
    r.raise_for_status()
    return r.text


def _fetch_headless(url, strip):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(
                user_agent=DESKTOP_UA,
                java_script_enabled=strip,   # JS off for the "no-js" tier
                viewport={"width": 1280, "height": 2200},
            )
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            if strip:
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                page.evaluate(STRIP_JS)
            return page.content()
        finally:
            browser.close()


def _fetch_wayback(url):
    av = requests.get(
        "https://archive.org/wayback/available", params={"url": url}, timeout=15
    ).json()
    snap = av.get("archived_snapshots", {}).get("closest", {})
    if not snap.get("url"):
        return None
    raw = snap["url"].replace("/http", "id_/http", 1)  # raw page, no archive toolbar
    return requests.get(raw, headers={"User-Agent": DESKTOP_UA}, timeout=40).text


# ---- "My Chrome" engine (real browser over CDP) --------------------------- #
def _port_open(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _ensure_debug_chrome():
    """Make sure a real Chrome with the remote-debugging port is running.

    Uses a dedicated profile folder so it can run alongside normal Chrome and
    remember any logins (NYT, a library account, etc.) between runs.
    """
    if _port_open(DEBUG_PORT):
        return
    if not CHROME_EXE:
        raise RuntimeError("Google Chrome was not found on this PC.")
    subprocess.Popen(
        [
            CHROME_EXE,
            f"--remote-debugging-port={DEBUG_PORT}",
            f"--user-data-dir={CHROME_PROFILE}",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ],
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
    )
    for _ in range(40):
        if _port_open(DEBUG_PORT):
            time.sleep(1.0)  # let the first page settle
            return
        time.sleep(0.5)
    raise RuntimeError("Chrome did not open its debugging port in time.")


def _wait_real_body(page, min_len, timeout_s):
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            n = page.evaluate("document.body ? document.body.innerText.length : 0")
        except Exception:
            n = 0
        if n and n >= min_len:
            return True
        page.wait_for_timeout(1000)
    return False


def _load_until(page, url, min_len, tries=3, per=12):
    """Navigate to url, retrying with reloads until the body has real content.

    Bot-challenge pages (e.g. NYT's Fastly wall) come back nearly empty, set a
    cookie, and only reveal content on a subsequent load — so we reload and wait.
    """
    for _ in range(tries):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception:
            pass
        if _wait_real_body(page, min_len, per):
            return True
        try:
            page.reload(wait_until="domcontentloaded", timeout=45000)
        except Exception:
            pass
        if _wait_real_body(page, min_len, per):
            return True
    return False


def _fetch_cdp(url):
    _ensure_debug_chrome()
    pr = urlparse(url)
    origin = f"{pr.scheme}://{pr.netloc}/"
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{DEBUG_PORT}")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()

        # Keep one "anchor" tab alive at all times so Chrome never closes down to
        # zero tabs (which would kill the debugging session). This is also the
        # tab you can use to log in — its cookies are shared with every fetch.
        if not ctx.pages:
            ctx.new_page().goto("about:blank")

        # Warm-up tab: load the site homepage to solve the bot challenge and set
        # cookies, THEN close it. Reliability drops if it stays open while the
        # article loads in another tab.
        warm = ctx.new_page()
        _load_until(warm, origin, 800, tries=3, per=10)
        try:
            warm.close()
        except Exception:
            pass

        # Fresh tab for the article itself (cookies from the warm-up are shared).
        page = ctx.new_page()
        try:
            _load_until(page, url, MIN_CHARS, tries=3, per=12)
            try:
                page.evaluate(STRIP_JS)
            except Exception:
                pass
            return page.content()
        finally:
            try:
                page.close()   # drop the throwaway article tab
            except Exception:
                pass
            # Leave the anchor tab and browser open; exiting the sync_playwright
            # context just disconnects CDP without killing Chrome.


# --------------------------------------------------------------------------- #
#  Orchestration
# --------------------------------------------------------------------------- #
def extract(url, use_browser=False):
    """Return (best, attempts).

    best     = dict(method, title, content, chars) or None
    attempts = list of (label, result) for the debug trail
    """
    best = None
    attempts = []

    def consider(method, html):
        nonlocal best
        title, content = _readability(html)
        chars = _text_len(content)
        # If readability under-extracted, fall back to the semantic container.
        if chars < MIN_CHARS:
            alt, alt_len = _container_fallback(html)
            if alt and alt_len > chars * 1.3:
                content, chars = alt, alt_len
                method += " (container)"
        attempts.append((method, f"{chars} chars"))
        if best is None or chars > best["chars"]:
            best = {"method": method, "title": title, "content": content, "chars": chars}
        return chars

    # Sites with network-level bot walls need the real-Chrome engine; engage it
    # automatically and don't waste time on tiers that are dead on arrival.
    hard_wall = _needs_real_browser(url)
    use_browser = use_browser or hard_wall

    # --- "My Chrome" engine first, if requested ---
    if use_browser:
        if not PLAYWRIGHT:
            attempts.append(("Your Chrome", "error: Playwright not installed"))
        elif not CHROME_EXE:
            attempts.append(("Your Chrome", "error: Chrome not found"))
        else:
            try:
                consider("Your logged-in Chrome", _fetch_cdp(url))
            except Exception as e:
                attempts.append(("Your Chrome", f"error: {e}"))
            if best and best["chars"] >= MIN_CHARS:
                return best, attempts

    if hard_wall:
        # Plain HTTP and headless fetches are connection-blocked on these
        # domains (timeouts / HTTP2 resets) — skip straight to Wayback.
        attempts.append(("Fast tiers", "skipped: this domain bot-walls automated fetches"))
        need_more = best is None or best["chars"] < MIN_CHARS
    else:
        # --- Fast engine, tier 1: plain HTTP ---
        try:
            consider("Direct fetch (no JavaScript)", _fetch_http(url))
        except Exception as e:
            attempts.append(("Direct fetch", f"error: {e}"))
        need_more = best is None or best["chars"] < MIN_CHARS

        # --- tier 2: headless browser, JavaScript disabled ---
        if need_more and PLAYWRIGHT:
            try:
                consider("Browser, JavaScript disabled", _fetch_headless(url, strip=False))
            except Exception as e:
                attempts.append(("Browser (JS off)", f"error: {e}"))
            need_more = best is None or best["chars"] < MIN_CHARS

        # --- tier 3: headless browser, overlay removal ---
        if need_more and PLAYWRIGHT:
            try:
                consider("Browser + overlay removal", _fetch_headless(url, strip=True))
            except Exception as e:
                attempts.append(("Browser (overlay strip)", f"error: {e}"))
            need_more = best is None or best["chars"] < MIN_CHARS

    # --- tier 4: Wayback Machine archive ---
    if need_more:
        try:
            h = _fetch_wayback(url)
            if h:
                consider("Wayback Machine archive", h)
            else:
                attempts.append(("Wayback Machine", "no snapshot"))
        except Exception as e:
            attempts.append(("Wayback Machine", f"error: {e}"))

    return best, attempts


@app.route("/", methods=["GET", "POST"])
def index():
    url = (request.form.get("url") or request.args.get("url") or "").strip()
    use_browser = bool(request.form.get("use_browser") or request.args.get("browser"))

    base = dict(playwright=PLAYWRIGHT, chrome=bool(CHROME_EXE), use_browser=use_browser)

    if not url:
        return render_template("index.html", **base)

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    paywalled = _needs_real_browser(url)
    base["archive_url"] = _archive_url(url)
    base["paywalled"] = paywalled

    # Reflect auto-engagement in the UI (shows the sign-in guidance notice).
    if paywalled and CHROME_EXE and PLAYWRIGHT:
        base["use_browser"] = True

    try:
        best, attempts = extract(url, use_browser=use_browser)
    except Exception as e:
        return render_template("index.html", url=url, error=str(e), **base)

    if not best or best["chars"] == 0:
        hint = ""
        if not use_browser and CHROME_EXE:
            hint = (" This site may block automated access — try ticking "
                    "“Use my logged-in Chrome” and searching again.")
        return render_template(
            "index.html",
            url=url,
            error="Couldn't find any article text on that page." + hint,
            attempts=attempts,
            **base,
        )

    # Offer the archive.today copy when the result looks incomplete: either it
    # fell short of MIN_CHARS, or it's a known paywall site and short enough to
    # be a teaser.
    incomplete = best["chars"] < MIN_CHARS or (
        paywalled and best["chars"] < TEASER_SUSPECT_CHARS
    )
    content = _clean_and_absolutize(best["content"], url)
    return render_template(
        "index.html",
        url=url,
        title=best["title"],
        content=content,
        method=best["method"],
        chars=best["chars"],
        truncated=best["chars"] < MIN_CHARS,
        incomplete=incomplete,
        host=urlparse(url).netloc,
        attempts=attempts,
        **base,
    )


if __name__ == "__main__":
    port = 5000
    threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    print(f"\n  Article Reader running at  http://127.0.0.1:{port}")
    print("  Press Ctrl+C to stop.\n")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
