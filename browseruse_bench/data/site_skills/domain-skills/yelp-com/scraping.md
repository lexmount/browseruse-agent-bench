Field-tested on 2026-07-04 (re-verified 2026-07-06)
Yelp = business reviews (restaurants, ratings, review counts). Direct scraping is BLOCKED by DataDome on every path we control; the working fallback is reading Yelp's own snippet (ranked restaurant NAMES + review counts) off a search-engine SERP in the cloud browser, and the only clean STAR rating data comes from the key-gated Yelp Fusion API. No login is needed for the search-snippet path.

## Do this first — use DuckDuckGo HTML, NOT Bing (2026-07-06 change)
Do NOT try to load yelp.com directly — it will fail (see Gotchas). Get the restaurant list via `html.duckduckgo.com/html/` in the cloud browser, then read `.result` text. This reliably returns Yelp's ranked NAME list AND per-restaurant review counts.

```python
# CLOUD BROWSER (new_tab/js). DDG HTML endpoint reachable from HK cloud egress; Yelp is not.
new_tab("https://html.duckduckgo.com/html/?q=best+Italian+restaurants+near+Times+Square+New+York+yelp")
wait_for_load(20); wait(3)
import json
res = js("""
Array.from(document.querySelectorAll('.result__body, .result')).map(r=>(r.innerText||'').replace(/\\n+/g,' | ').slice(0,400)).filter(t=>/yelp/i.test(t))
""")
print(json.dumps(res, ensure_ascii=False, indent=1))
# VERIFIED 2026-07-06 output: Yelp's snippet lists the ranked names, e.g.
# "Top 10 Best Italian Restaurants Times Square in New York, NY - ... - Yelp -
#  Carmine's - Time Square, Tony's Di Napoli, Trattoria Trecolori, Gatsby's Landing
#  Times Square, Buchette Del Vino, Osteria Al Doge, La Masse"
# -> parse the "- Yelp - A, B, C, ..." tail of the snippet for the ranked top list.
```

For a per-restaurant review count, a targeted DDG query surfaces one (not a clean star rating). VERIFIED 2026-07-06: Tony's Di Napoli = 3044 Reviews, Trattoria Trecolori = 2965 Reviews:
```python
new_tab("https://html.duckduckgo.com/html/?q=Tony%27s+Di+Napoli+Times+Square+yelp+reviews")
wait_for_load(20); wait(3)
res = js("""
Array.from(document.querySelectorAll('.result__body, .result')).map(r=>(r.innerText||'').slice(0,200)).filter(t=>/yelp/i.test(t)&&/review/i.test(t))
""")
# VERIFIED to yield e.g. "TONY'S DI NAPOLI - Updated July 2026 - 3260 Photos & 3044 Reviews - Yelp".
# Star rating is usually absent from the snippet -> Fusion API for that field.
```

## Clean structured data: Yelp Fusion API (needs an API key)
`api.yelp.com/v3` is NOT DataDome-blocked (it answers from `envoy`, not DataDome). It just requires auth. If you have a Yelp API key, this is the only path that returns real rating + review_count fields:
```python
# LOCAL http_get is fine here (api.yelp.com is reachable, not IP-blocked).
h={"Authorization":"Bearer <YELP_API_KEY>"}
r = http_get("https://api.yelp.com/v3/businesses/search?location=Times+Square,NY&term=italian&sort_by=rating&limit=3", headers=h)
# Response JSON: businesses[].name, .rating, .review_count, .location, .url
```
VERIFIED without a key: returns HTTP 400 `{"error":{"code":"VALIDATION_ERROR","description":"Authorization is a required parameter."}}` — confirms the endpoint is live and only auth-gated, not blocked.

## Gotchas
- **Yelp is fully behind DataDome (Server: DataDome, X-DataDome: protected).** Both egress IPs are blocked:
  - Local http_get (China IP): `HTTP 403 Forbidden`, DataDome captcha HTML body ("Please enable JS and disable any ad blocker", loads `ct.captcha-delivery.com/c.js`).
  - Cloud browser new_tab (Hong Kong IP, `X-Served-By: cache-hkg...-HKG`): navigation fails at the network layer → page is `chrome-error://chromewebdata/` with title "Access to www.yelp.com was denied / HTTP ERROR 403". Because the navigation itself fails, DataDome's JS challenge never even runs — waiting 30s+ does NOT clear it. This is a hard block, not a solvable challenge.
  - Affected hosts (all 403/DataDome): `www.yelp.com`, `m.yelp.com`, `www.yelp.com/search/snippet` (the internal JSON search endpoint), `www.yelp.ca`.
- **jina.ai reader (`r.jina.ai/<url>`) does NOT bypass it** — returns "This page maybe requiring CAPTCHA". (Also: `r.jina.ai` connection-resets from the local China IP; only reachable via the cloud browser, where it still hits the captcha.)
- **Bing link hrefs are useless** — every `li.b_algo` result wraps its href in Bing click-tracking, so `a.href` never contains `yelp.com`. The yelp.com URL is only present as visible text in `cite`/innerText. Extract from `.innerText`, not from `href`.
- **Bing SERP went stale (2026-07-06): use DuckDuckGo HTML instead.** As of 2026-07-06 the Bing path no longer works from the HK cloud egress: (a) severe region tint — a "best italian restaurants..." query gets interpreted as the word "BEST" and returns Vietnamese logistics co. "BEST Express" / dictionary results, and adding "Times Square" without "New York NY" returns Billings, MT results; (b) even after forcing `&setmkt=en-US&cc=US` and appending "New York NY", the Yelp `li.b_algo` result renders only the bare TITLE line ("TOP 10 BEST Italian Restaurants in Times Square in New York, NY … - Yelp") with NO description paragraph, so the ranked name list the old example relied on is gone. Switched primary path to `html.duckduckgo.com/html/?q=...` which returns the full Yelp snippet (ranked names + review counts). Keep "New York" in the query to avoid geo-tinting to the cloud egress region.
- **`site:yelp.com` Bing queries returned an empty/interstitial page** (innerText ~105 chars). Use a natural-language query instead.
- **SERP snippets give names + review counts, not star ratings.** It's enough to identify the top-3 restaurants by name/rank, but per-restaurant star rating + review_count are not consistently present. For those exact fields you need the Fusion API key. If the task only needs "top 3 restaurants", the Bing path alone suffices.
- Cloud egress is Hong Kong, so Bing may return region-tinted results; the Yelp snippet content itself is US (Times Square) as queried.
