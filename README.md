# Article Reader

A small, self-hosted web app: paste an article URL, get back a clean,
popup-free, reader-mode version you can actually read (and print / save as
PDF). No accounts, no cloud — it runs entirely on your own machine.

Built to deal with news sites (New York Times, Daily Mail, and similar) whose
subscribe/consent overlays sit on top of article text that is already loaded
in the page.

## Install (Windows)

Requirements: [Python 3.12+](https://www.python.org/downloads/) and,
optionally, Google Chrome (only needed for the "Use my logged-in Chrome"
mode described below).

```powershell
git clone https://github.com/jp244btc/article-reader.git
cd article-reader
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
```

The last step downloads a private headless Chromium (~130 MB) used by the
fallback extraction tiers. Skip it if you only want basic HTTP mode.

## Run

Double-click **`run.bat`**, or:

```powershell
.venv\Scripts\python.exe app.py
```

Your browser opens to `http://127.0.0.1:5000`. Paste a URL, click **Read**.
Press **Ctrl+C** in the terminal to stop the server.

## How it captures the article

The default **fast engine** tries four methods in order, stopping as soon as
enough text is recovered:

1. **Direct fetch** — plain HTTP request. Fast; works when the article text is
   in the server-sent HTML (Daily Mail and many others).
2. **Headless browser, JavaScript disabled** — the paywall/consent overlay is
   never injected because the script that creates it never runs.
3. **Headless browser + overlay removal** — loads with JS on, then deletes
   overlay/modal elements, re-enables scrolling, un-blurs the text.
4. **Wayback Machine** — pulls the newest archived snapshot, if one exists.

Every result is run through Mozilla-style readability extraction to isolate
the article body. An "Extraction trail" expander on each page shows which
method won.

### "Use my logged-in Chrome" mode

Some sites (notably the **New York Times**) don't just paywall — they serve
automated browsers an empty bot-challenge page, so none of the fast tiers ever
see the article. Ticking **Use my logged-in Chrome** drives a *real* Chrome
window (with its own dedicated profile, stored in `chrome-profile/`, separate
from your everyday browser) that passes the challenge.

- Logged out, you'll get whatever the site gives free visitors — for the NYT
  that's a teaser of the first few paragraphs.
- Sign in **once** in that Chrome window (a subscription, a free account, or
  your library's news-site access) and the login persists; after that the
  reader shows whatever your own account is entitled to see.

## Honest limitation

If a site sends only a teaser to your (logged-out) browser, the rest of the
text never reaches your machine, and **no client-side tool can recover it** —
that's a server-side decision, not an overlay. This tool removes popups from
content you already received; it does not conjure content you weren't sent.
For full access to subscriber-only text, use a subscription, a free library
login, or an archive service.

## Project layout

```
app.py              Flask app + the extraction pipeline (both engines)
templates/index.html  The single-page UI (URL box + reader view)
run.bat             Double-click launcher
requirements.txt    Pinned Python dependencies
chrome-profile/     Created at runtime by the logged-in Chrome mode (gitignored)
```

## License

[MIT](LICENSE)
