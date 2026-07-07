Field-tested on 2026-07-04

Bloomberg news search + result extraction via cloud browser only. `http_get` (local China IP) is hard-blocked (connection reset); article detail pages hit the PerimeterX "Are you a robot?" wall almost instantly. The **search results page** is the one reliably-scrapeable surface — it already carries headline + full summary + a date in every article URL slug, which is enough for headline/date/summary tasks without ever opening the article.

## Do this first

Load the search page in the cloud browser and extract structured records straight from the DOM. Do NOT open article detail pages (they get robot-walled). Sort client-side by the URL-slug date to find the most recent — the default "Best match" sort is NOT chronological.

```python
# 1) Load search. URL pattern: /search?query=<url-encoded terms>. One clean nav at a time.
new_tab("https://www.bloomberg.com/search?query=Federal%20Reserve%20interest%20rates")
wait_for_load(); wait(6)

# Bail out if walled (see Gotchas for recovery)
if "robot" in page_info()['title'].lower() or js("/robot|unusual activity/i.test(document.body.innerText)"):
    raise SystemExit("robot wall - wait ~75s and retry once")

# 2) Extract every result: headline, full summary, href, and date from the URL slug.
records = js(r"""
(() => {
  const heads = Array.from(document.querySelectorAll('[data-component="headline"]'));
  const rows = heads.map(h => {
    // climb to the container that also holds the summary
    let n = h;
    for (let i=0;i<7;i++){ if(n.parentElement) n=n.parentElement; if(n.querySelector('[data-component="summary"]')) break; }
    const a = n.querySelector('a[href*="/articles/"]');
    const s = n.querySelector('[data-component="summary"]');
    const e = n.querySelector('[data-component="optional-eyebrow"]'); // "Opinion" / "Listen" etc.
    const m = a ? a.href.match(/\/(\d{4}-\d{2}-\d{2})\//) : null;    // date is in the URL slug
    return {
      headline: h.innerText.trim(),
      summary:  s ? s.innerText.trim() : null,
      href:     a ? a.href : null,
      date:     m ? m[1] : null,
      eyebrow:  e ? e.innerText.trim() : null
    };
  }).filter(r => r.href && r.date);   // drop audio/"Listen" cards with no article link
  rows.sort((a,b) => b.date.localeCompare(a.date));   // MOST RECENT FIRST
  return rows;
})()
""")

most_recent = records[0]   # {headline, date, summary, href, eyebrow}
```

Verified output (2026-07-04, query "Federal Reserve interest rates"):
- most recent by slug-date: headline "Charting the Global Economy: US Hiring Slows, Eurozone CPI Cools", date 2026-07-04, summary "US hiring slowed sharply in June after three months of better-than-expected jobs reports, and investors scaled back bets on a Federal Reserve interest-rate increase this year.", href .../news/articles/2026-07-04/charting-the-global-economy-us-hiring-slows-eurozone-cpi-cools
- 7 clean article records returned for this query.

## Field reference (search results page — all verified working)

- Result container: climb up from `[data-component="headline"]` until you reach an ancestor containing `[data-component="summary"]`.
- Headline: `[data-component="headline"]` innerText.
- Summary: `[data-component="summary"]` innerText — this is the FULL article dek, not truncated. Good enough for "summary" tasks; no need to open the article.
- Article link + publish date: `a[href*="/articles/"]`. The date is the `YYYY-MM-DD` segment in the path (`/news/articles/2026-07-04/...`, `/opinion/articles/...`). This is the reliable date source — there is no usable `<time>` element or `datetime` attr on results.
- Section/type: `[data-component="optional-eyebrow"]` ("Opinion", "Listen", etc.).
- The human-readable "14 hr ago" / "July 2, 2026" strings ARE in `document.body.innerText` but are not cleanly bound to individual result nodes — use the slug date instead.

## Gotchas (all observed live)

- **Default sort is "Best match", NOT chronological.** Top result was 2026-07-04 but the 2nd was 2026-06-17 and 3rd 2026-07-02. Always sort by the slug date yourself; never trust result order for "latest/most recent".
- **The `&sort=time:desc` URL param INSTANTLY trips the robot wall.** Do not use query-string sort options — sort client-side instead.
- **Anti-bot is PerimeterX ("Are you a robot?", title shows a 🐴 horse + "Bloomberg - Are you a robot?").** Trigger conditions seen: (a) rapid back-to-back navigations, (b) any article detail page (`/news/articles/...`, `/opinion/articles/...`) — these wall almost immediately, (c) the sort param above. Detect with `/robot|unusual activity/i.test(document.body.innerText)` or checking `page_info()['title']`.
- **Recovery:** the block is session-sticky but temporary. A ~15-20s wait was NOT enough after repeated hits; a **~75s cooldown + a single clean reload of the plain `/search?query=` URL** reliably restored full results. Space out navigations; do one nav, extract everything you need in that single js() call, don't re-navigate.
- **`http_get` does NOT work for bloomberg.com** — it runs on the local China IP and gets `ConnectionResetError(54)` (hard-blocked). Cloud browser (`new_tab`+`js`) is the ONLY viable route. The two-IP fallback strategy does not apply here.
- **Do not open article detail pages to get the body.** They robot-wall on first load (no JSON-LD, no `og:` meta, no `__NEXT_DATA__` returned — you get the wall's `<h1>Bloomberg</h1>` instead). Everything the "headline/date/summary" task needs is already on the search results page.
- No login wall on search results themselves (subscription paywall is only on full article bodies, which we skip anyway).
- Cloud egress IP is Hong Kong; Bloomberg served English/US content normally here (no region redirect observed), but expect possible geo variance.
