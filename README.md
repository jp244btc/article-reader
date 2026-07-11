# Article Reader

A small, self-hosted web app: paste an article URL, get back a clean,
popup-free, reader-mode version you can actually read (and print / save as
PDF). No accounts, no cloud — it runs entirely on your own machine.

Built to deal with news sites (New York Times, Washington Post, Daily Mail,
and similar) whose subscribe/consent overlays sit on top of article text that
is already loaded in the page — and sites that go further and block automated
browsers entirely.

## Install & run (Windows)

Requirements: [Python 3.12+](https://www.python.org/downloads/) (tick **"Add
python.exe to PATH"** in the installer) and, optionally, Google Chrome (only
needed for the "Use my logged-in Chrome" mode described below).

Get the code either way:

- `git clone https://github.com/jp244btc/article-reader.git`, **or**
- download and extract the source zip from the
  [latest release](https://github.com/jp244btc/article-reader/releases).

Then just double-click **`run.bat`**. On first run it sets everything up
automatically — creates a private Python environment, installs the pinned
dependencies, and downloads a headless Chromium (~130 MB) for the fallback
extraction tiers. That takes a few minutes once; afterwards it starts
instantly.

Your browser opens to `http://127.0.0.1:5000`. Paste a URL, click **Read**.
Press **Ctrl+C** in the terminal (or close it) to stop the server.

<details>
<summary>Manual setup, if you prefer</summary>

```powershell
cd article-reader
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe app.py
```
</details>

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

Some sites don't just paywall — they refuse automated browsers outright. The
**New York Times** serves them an empty bot-challenge page; the **Washington
Post** resets the connection at the network level. For these, the app drives a
*real* Chrome window (with its own dedicated profile, stored in
`chrome-profile/`, separate from your everyday browser) that passes the
challenge.

This engine is **engaged automatically** for known bot-walled domains (NYT,
WaPo) — the doomed fast tiers are skipped entirely, which also makes those
sites much faster. For any other site that misbehaves, tick the
**Use my logged-in Chrome** box to force it.

- Logged out, you'll get whatever the site gives free visitors — for the NYT
  that's the first few paragraphs; the Washington Post sends only about one.
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
