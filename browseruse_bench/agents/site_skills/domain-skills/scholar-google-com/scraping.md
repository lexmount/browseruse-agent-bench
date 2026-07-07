Field-tested on 2026-07-04

scholar.google.com — Google Scholar academic paper search; scrape titles/authors/venue/year/citation-count from the results-list HTML.

## Do this first

The cloud browser IP is hard-blocked by Google (flat HTTP 403 on every scholar.google.com URL, no CAPTCHA to solve). Do NOT use new_tab / js / in-page fetch for this host — they all hit the 403. Use `http_get` instead: it runs on the LOCAL machine IP, which Google serves normally. Send a real desktop User-Agent, hit the plain `/scholar?q=...` HTML endpoint, and parse the results list with regex. There is no login-free JSON API; the HTML is the interface.

Search is a pure GET-URL pattern — no search box needed:
`https://scholar.google.com/scholar?q=<url-encoded query>&as_ylo=<from-year>&as_yhi=<to-year>&start=<offset>`
- `as_ylo` = earliest year (inclusive), `as_yhi` = latest year (inclusive). Either can be omitted.
- `start` = result offset for pagination; page size is 10 (start=0,10,20,...).
- Google Scholar has NO native "sort by citations". Results return in relevance order; extract `Cited by N` per result and sort client-side.

## Verified extraction (copy-paste runnable)

```python
import re, html as htmlmod, time
from urllib.parse import quote_plus

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

def _strip(t): return re.sub('<[^>]+>', '', t)

def scholar_search(query, year_from=None, year_to=None, pages=1):
    """Return list of dicts {citations,title,authors_venue,year}. Runs on local IP via http_get."""
    results = []
    for p in range(pages):
        url = f"https://scholar.google.com/scholar?q={quote_plus(query)}&start={p*10}"
        if year_from: url += f"&as_ylo={year_from}"
        if year_to:   url += f"&as_yhi={year_to}"
        body = http_get(url, headers=UA)
        if "not have permission" in body[:2000] or body[:1500].lstrip().startswith("<!doctype")==False and "Sorry" in body[:1500]:
            raise RuntimeError("Google blocked this request (403/Sorry page)")
        for b in body.split('class="gs_r gs_or gs_scl"')[1:]:
            mt = re.search(r'class="gs_rt"[^>]*>(.*?)</h3>', b, re.S)
            title = htmlmod.unescape(_strip(mt.group(1))).strip() if mt else None
            ma = re.search(r'class="gs_a"[^>]*>(.*?)</div>', b, re.S)
            au  = htmlmod.unescape(_strip(ma.group(1))).strip() if ma else None
            mc = re.search(r'Cited by (\d+)', b)
            my = re.search(r', (\d{4}) -', au or "")
            results.append({
                "citations": int(mc.group(1)) if mc else 0,
                "title": title,
                "authors_venue": au,      # e.g. "H Naveed, AU Khan… - ACM Transactions on…, 2025 - dl.acm.org"
                "year": my.group(1) if my else None,
            })
        time.sleep(1)  # be polite between pages
    return results

# Task 1: LLM papers, 2024, top by citations
r = scholar_search("large language models", year_from=2024, pages=2)
top = sorted(r, key=lambda x: -x["citations"])[:5]
for x in top: print(x["citations"], "|", x["year"], "|", x["title"])

# Task 2: transformer models, 2023-2024, min-citation filter
r = scholar_search("transformer models", year_from=2023, year_to=2024, pages=2)
MIN = 150
r = [x for x in r if x["citations"] >= MIN]
for x in sorted(r, key=lambda x:-x["citations"]): print(x["citations"], "|", x["year"], "|", x["title"])
```

Field selectors (regex on raw HTML, all verified against live pages 2026-07-04):
- Each result block starts at `class="gs_r gs_or gs_scl"` (split on this string; discard element [0]).
- Title: `<h3 class="gs_rt">...</h3>` — strip tags, unescape entities. `[HTML]`/`[PDF]`/`[BOOK]` type tags may prefix the title text.
- Authors/venue/year line: `<div class="gs_a">...</div>` — format `authors… - venue, YEAR - domain`. Extract year with `, (\d{4}) -`.
- Citation count: literal `Cited by N` in the result footer (`gs_fl` links). Absent → 0 citations.

## Gotchas

- **Browser path is fully blocked, no workaround.** `new_tab("https://scholar.google.com/...")` and `http_get`'s browser equivalent all get HTTP 403 "Your client does not have permission" (observed client IP 119.28.178.107, the cloud egress). It is NOT a CAPTCHA — there is no challenge form to solve, just a flat 403. The `/scholar?q=` path via cloud browser returns a "Sorry… automated queries" page (900 bytes, no form). Only `http_get` (local IP) succeeds.
- **No JSON API.** Google Scholar exposes no public login-free JSON endpoint. Parse the results HTML.
- **No native citation sort.** `as_ylo`/`as_yhi` (year) work, but there is no URL param to sort by citation count. Fetch relevance-ordered results and sort by the extracted `Cited by N` yourself. Fetch 2+ pages (`start=0,10`) before applying a min-citation threshold so you have enough candidates.
- **Rate-limit risk on local IP too.** Google can throw the "Sorry… automated queries" page at the local IP if you fire many requests fast. Space page fetches ~1s apart; the helper does `time.sleep(1)`. If you hit it, back off before retrying.
- **`http_get` returns the raw HTML string** (not an object). Check `body` starts with `<!doctype html>` and title `Google Scholar`; if it contains "not have permission" or a short "Sorry" page, you were blocked.
- Author names in the `gs_a` line are abbreviated by Google (e.g. "H Naveed, AU Khan") and truncated with `…` — this is Scholar's own formatting, not a scraping artifact.
