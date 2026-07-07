Field-tested on 2026-07-04
Scrape SpringerLink (Springer Nature Link) search results — the search page is fully server-side rendered, so a single HTTP GET returns all 20 result cards; no login, no JS, no anti-bot challenge.

## Do this first (fastest — plain HTTP GET, works from local China IP)

The search URL takes all filters as query params and renders 20 result cards server-side. `http_get` (local IP) returned full HTML (~340 KB) with all fields present — no block, no captcha. Prefer this over the browser.

URL pattern (query + content-type facet + year range + sort + page):
```
https://link.springer.com/search?query=<q>&facet-content-type=%22Chapter%22&date-facet-mode=between&facet-start-year=2024&facet-end-year=2024&sortBy=newestFirst&page=1
```
- `facet-content-type="Chapter"` — MUST be URL-encoded `%22Chapter%22` (with literal quotes). Other values: `"Article"`, `"ConferencePaper"`, `"Book"`. NOTE: the "Chapter" facet also returns items whose type is "Conference paper" (Springer groups them), so filter on the per-item type text if you need pure chapters.
- Date range: `date-facet-mode=between&facet-start-year=2024&facet-end-year=2024` (set start==end for a single year).
- `sortBy=newestFirst` (also `oldestFirst`; default is relevance).
- `page=N` — 20 results per page. Total count is in the HTML as `... results`.

Verified extraction from the raw HTML (regex, no browser):
```python
import re
u = ("https://link.springer.com/search?query=renewable+energy"
     "&facet-content-type=%22Chapter%22&date-facet-mode=between"
     "&facet-start-year=2024&facet-end-year=2024&sortBy=newestFirst&page=1")
html = http_get(u, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})

total = re.search(r'([\d,]+)\s+results', html)          # e.g. "13,039 results"
titles = re.findall(r'<h3[^>]*data-test="title"[^>]*>(.*?)</h3>', html, re.S)  # 20 blocks
authors = re.findall(r'data-test="authors"[^>]*>([^<]+)</span>', html)         # "A, B, ... C"
for tb in titles:
    a = re.search(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', tb, re.S)
    if a:
        title = re.sub(r'<[^>]+>', '', a.group(2)).strip()
        href  = a.group(1)                               # relative, e.g. /chapter/10.1007/...
        print(title, "https://link.springer.com" + href)
```
Verified output (renewable energy / Chapter / 2024): "Renewable Energy" → /chapter/10.1007/978-981-99-9676-6_13, authors "Pen-Chi Chiang, Hwong-wen Ma, ... Chun-hsu Lin", etc. All 20 cards parsed.

## Alternative: cloud browser + JS (use if local IP ever gets blocked)

Same URL via `new_tab` + `wait_for_load()`, then extract per-card with `js()`. Verified selectors:
- Result cards: `li[data-test="search-result-item"]` (20 per page).
- Title text: `h3[data-test="title"]`  (this is the robust one — present on every page).
- Title link/href: `a.app-card-open__link` (NOT `a[data-test="title-link"]` — that attr is missing on some pages/cards; do not rely on it).
- Authors: `[data-test="authors"]` (renders as `"A, B, ... C"`, truncated with an ellipsis for long lists).
- Parent book title: `a[data-test="parent"]`.
- Content type: `[data-test="content-type"]` (e.g. "Chapter", "Conference paper").
- Year/date: `[data-test="published"]` (e.g. "2024").
- Total count element: `[data-test="results-data-total"]` (text "Showing 1-20 of 13,209 results").

```python
new_tab(URL); wait_for_load(); wait(2)
rows = js("""
(() => [...document.querySelectorAll('li[data-test="search-result-item"]')].map(li => ({
  title:   li.querySelector('h3[data-test="title"]')?.innerText.trim() || null,
  href:    li.querySelector('a.app-card-open__link')?.href || null,
  authors: li.querySelector('[data-test="authors"]')?.innerText.trim() || null,
  book:    li.querySelector('a[data-test="parent"]')?.innerText.trim() || null,
  type:    li.querySelector('[data-test="content-type"]')?.innerText.trim() || null,
  year:    li.querySelector('[data-test="published"]')?.innerText.trim() || null,
})))()
""")
```
This produced clean, complete rows on both page 1 and page 2. Example task ("renewable energy", book chapters, 2024): top result "Renewable Energy" by Pen-Chi Chiang et al., in "Introduction to Green Science and Technology for Green Economy", 2024.

## Gotchas
- No免登录 JSON API. Tried `?format=json` with `Accept: application/json` (via cloud fetch) — returns HTML (status 200, content-type text/html), not JSON. Parse the HTML instead.
- The "Chapter" facet is a superset: with `facet-content-type="Chapter"` the results included 3 "Conference paper" items among 17 real chapters on page 1. If you need only chapters, drop items whose `[data-test="content-type"]` != "Chapter".
- The `facet-content-type` value MUST keep its double-quotes URL-encoded (`%22...%22`); without the quotes the facet is ignored.
- Total-count number drifts a little between requests (13,209 via browser vs 13,039 via http_get, same query) — it's the live index count, not a discrepancy in filtering. Both request paths applied the Chapter+2024 filters correctly (verified: all 20 cards were year 2024).
- `a[data-test="title-link"]` was present on page 1 but MISSING on page 2 cards — use `h3[data-test="title"]` (text) + `a.app-card-open__link` (href) instead for reliability.
- Authors field is truncated for long author lists (shows "First, Second, ... Last"). For the full list you'd need to open the chapter detail page.
- No anti-bot / captcha / rate-limit hit on either path in this session (China local IP via http_get, and HK cloud IP via browser both worked).
