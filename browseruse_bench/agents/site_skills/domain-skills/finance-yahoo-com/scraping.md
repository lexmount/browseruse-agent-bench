Field-tested on 2026-07-04 — finance.yahoo.com stock quote extraction (ticker price, day change, 52w hi/lo, P/E, market cap, news). Quote page lives at `https://finance.yahoo.com/quote/<TICKER>/`.

## BLOCKED on both IP paths (2026-07-04) — read this first
Neither of the harness's two egress IPs could reach Yahoo Finance today. Both were tested live:

- **Cloud browser (HK datacenter IP)** via `new_tab`/`js`: every finance.yahoo.com page AND the query1/query2 API hosts return an Akamai edge throttle — `document.title` becomes `🐴` and body text is literally `Edge: Too Many Requests` (HTTP 429). This affects the whole domain (homepage, `/quote/NVDA/`, and `query1.finance.yahoo.com/v8/...`), not just one path. Waited 45s, then 100s, and retried — still 429. The HK datacenter ASN is rate-limited/blocklisted at Yahoo's edge.
- **Local IP (China mainland)** via `http_get`: every Yahoo host returns `HTTP Error 403: Forbidden` with a Chinese-language HTML error page (`<html lang="zh">... Ya...`). This is a geo/IP block. Confirmed 403 on `fc.yahoo.com`, `/v1/test/getcrumb`, `query1` and `query2` `v7/finance/quote`, `v8/finance/chart`, `v7/finance/spark`, `v10/finance/quoteSummary`.

So the crumb+cookie API dance can't even start (getcrumb itself 403s locally, 429s on cloud). **Conclusion: with the current two IPs, this site is not reachable. A US/EU residential or clean cloud IP is required.** Do not conclude "data unavailable" globally — it's IP-availability, not a site-wide outage.

## Do this first (when you have a working, non-blocked IP)
Yahoo geo-fences and edge-throttles aggressively. Try the JSON API before the HTML page — it's one request and returns every field:

```python
import urllib.request, urllib.error, http.cookiejar
UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36'
cj=http.cookiejar.CookieJar()
op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
def g(url):
    req=urllib.request.Request(url, headers={'User-Agent':UA,'Accept':'*/*'})
    try:
        r=op.open(req,timeout=20); return r.status, r.read().decode('utf-8','replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]

g("https://fc.yahoo.com")                                   # seed A1/A3 cookies (ignore 404)
_,crumb=g("https://query1.finance.yahoo.com/v1/test/getcrumb")   # short token, e.g. "abc123.-"
import urllib.parse
code,body=g("https://query1.finance.yahoo.com/v7/finance/quote?symbols=NVDA&crumb="+urllib.parse.quote(crumb))
# body JSON path: quoteResponse.result[0] has:
#   regularMarketPrice, regularMarketChange, regularMarketChangePercent,
#   fiftyTwoWeekHigh, fiftyTwoWeekLow, trailingPE, marketCap
```
NOTE: this crumb flow is the documented correct approach but was NOT verifiable today (both IPs blocked before a crumb could be obtained). Treat the exact response shape as expected, not confirmed-this-run.

For the **HTML quote page** (fallback, and the source for news headlines which the quote API lacks), the cloud browser is the right tool (residential-flavored requests, sets consent cookies naturally):
```python
new_tab("https://finance.yahoo.com/quote/NVDA/"); wait_for_load(); wait(4)
# if a GDPR consent interstitial appears, click "Accept all" then re-navigate.
info = js(r"""
(()=>{
  const q=s=>document.querySelector(s);
  const t=s=>{const e=q(s);return e?e.innerText.trim():null};
  const price=t('[data-testid="qsp-price"]');
  const change=t('[data-testid="qsp-price-change"]');
  const changePct=t('[data-testid="qsp-price-change-percent"]');
  // right-hand stats: each row is <li> with a label + value; grab by label text
  const stats={};
  document.querySelectorAll('[data-testid="quote-statistics"] li').forEach(li=>{
    const k=(li.querySelector('.label')||li.children[0]);
    const v=(li.querySelector('.value')||li.children[1]);
    if(k&&v) stats[k.innerText.trim()]=v.innerText.trim();
  });
  const news=[...document.querySelectorAll('a[href*="/news/"] h3, section a h3')]
              .map(h=>h.innerText.trim()).filter(Boolean).slice(0,3);
  return {price,change,changePct,
          fiftyTwoWeekRange:stats['52 Week Range']||stats['52 Wk Range'],
          pe:stats['PE Ratio (TTM)'], marketCap:stats['Market Cap']||stats['Market Cap (intraday)'],
          news};
})()
""")
print(info)
```
Selectors above (`[data-testid="qsp-price"]`, `[data-testid="qsp-price-change"]`, `[data-testid="quote-statistics"] li`) are Yahoo's current stable testids as of prior runs, but were NOT re-confirmed today because the page never loaded (429). Verify `[data-testid]` names against a live DOM before trusting; Yahoo renames testids periodically.

## Gotchas
- **Two-IP block is the headline finding (2026-07-04):** cloud HK = 429 `Edge: Too Many Requests` (title shows `🐴`) across the whole domain incl. API hosts; local CN = 403 Forbidden HTML (`lang="zh"`) across all API endpoints. Verified live, both directions. Retrying/waiting on the HK IP did not clear the 429 within ~3 min.
- **Cloud-side `fetch()` to query1/query2 fails with `TypeError: Failed to fetch`** even when not rate-limited — CORS blocks cross-origin JSON from the finance.yahoo.com page context. To use the JSON API in the cloud browser you must `new_tab` directly onto the API URL (same-origin), not `fetch` it. (Today even that direct nav 429'd.)
- **v7 quote API requires crumb+cookie**; a bare request 403s regardless of IP. Seed cookies via `fc.yahoo.com`, then `/v1/test/getcrumb`, then pass `&crumb=`. If getcrumb returns HTML instead of a short token, your IP is blocked — switch paths.
- **Region skew:** Yahoo localizes by egress IP. A HK/EU IP can return a non-US market/currency context and a GDPR consent interstitial. If a consent wall appears, accept it (sets `GUC`/`A1` cookies) before scraping.
- **News headlines** are only on the HTML page, not in the `v7/finance/quote` JSON. Use the browser DOM path (or `v2/finance/news`/RSS) for the 3 headlines.
