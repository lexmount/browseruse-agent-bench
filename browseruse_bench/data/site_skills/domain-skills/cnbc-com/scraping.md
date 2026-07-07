Field-tested on 2026-07-04 — CNBC site search (articles + videos) is served by a public Queryly JSON API; no login, no anti-bot, works from both local and cloud IP.

# CNBC (cnbc.com) — search & video extraction

Task shape this covers: "Search market analysis on <topic> on CNBC, find the latest video, record title / analyst / date."

## Do this first — the Queryly JSON API (best path, no browser render needed)

CNBC's on-site search is powered by `api.queryly.com`. One GET returns fully structured results (title, type, date, author, url, duration, summary). This is far more reliable than scraping the search DOM.

Endpoint (verified 2026-07-04, HTTP 200, totalresults=44306 for "tech stocks"):
```
https://api.queryly.com/cnbc/json.aspx?queryly_key=31a35d40a9a64ab3&query=<URLENC_QUERY>&endindex=0&batchsize=20&showfaceted=false&extendeddatafields=creationtime,imageurl,cn:liveURL,author,cn:type,cn:dateFirstPublished&timezoneoffset=480
```
- `queryly_key=31a35d40a9a64ab3` is CNBC's fixed public key (embedded in their search page JS). Reused verbatim across calls — no auth.
- `batchsize` up to at least 20; paginate with `endindex` (0,20,40...). `metadata.totalpage` / `totalresults` tell you the size.
- Default sort = **relevance** (recommended for "market analysis on X"). Add `&sort=date` for strict newest-first, but note that pulls in loosely-related items — for a topical "analysis" task, relevance-sorted results are already recent AND on-topic.

### Fields per result (verified keys)
- `cn:title`  — the headline/title (NOTE: it's `cn:title`, NOT `title`).
- `cn:type`   — `cnbcvideo` for videos, `cnbcnewsstory` for articles. **Filter on this to get "video analysis".**
- `datePublished` / `cn:lastPubDate` — ISO8601 publish time (datePublished is UTC; lastPubDate offset already applied). Sorted newest-first within relevance.
- `author`    — analyst/reporter name. **Often empty ("") for videos** — see Gotchas for how to get the analyst.
- `url`       — canonical article/video URL. Video URLs contain `/video/`.
- `duration`  — video length in seconds. `section` — the show/desk eyebrow (e.g. "Mad Money", "Access Middle East"). `summary`/`description` — synopsis.

### Run it — from the cloud browser (js fetch, cloud HK IP; verified working)
```python
new_tab("https://www.cnbc.com/"); wait_for_load(); wait(1)   # any cnbc origin, for same-origin comfort (fetch is cross-origin to queryly but CORS-open)
r = js("""
(async function(){
  var u='https://api.queryly.com/cnbc/json.aspx?queryly_key=31a35d40a9a64ab3&query=tech%20stocks'
        +'&endindex=0&batchsize=20&showfaceted=false'
        +'&extendeddatafields=creationtime,imageurl,cn:liveURL,author,cn:type,cn:dateFirstPublished&timezoneoffset=480';
  var j = await (await fetch(u)).json();
  var vids = j.results.filter(function(x){return x['cn:type']==='cnbcvideo';});
  return JSON.stringify(vids.slice(0,5).map(function(v){
    return {title:v['cn:title'], author:v.author, date:v.datePublished, section:v.section, url:v.url, dur:v.duration};
  }), null, 2);
""")
print(r)   # results[0] of the video filter = latest relevant video analysis
```

### Run it — from local machine (http_get / urllib; backup path)
The API is also reachable from the local (CN) IP and returns identical data. **BUT** the built-in `http_get` and plain urllib fail with `SSL: CERTIFICATE_VERIFY_FAILED` on this host — you must disable cert verification:
```python
import ssl, urllib.request, json
ctx = ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
u = ("https://api.queryly.com/cnbc/json.aspx?queryly_key=31a35d40a9a64ab3"
     "&query=tech%20stocks&endindex=0&batchsize=20"
     "&extendeddatafields=author,cn:type&timezoneoffset=480")
req = urllib.request.Request(u, headers={"User-Agent":"Mozilla/5.0"})
d = json.loads(urllib.request.urlopen(req, context=ctx, timeout=25).read())
vids = [r for r in d["results"] if r.get("cn:type")=="cnbcvideo"]
print(vids[0]["cn:title"], "|", vids[0]["datePublished"], "|", vids[0].get("author") or "(author empty)")
```

## Getting the analyst / date for a specific video (detail page, verified)

When you need to confirm title/date or the author field is empty, open the video URL and read its JSON-LD `VideoObject` (exactly 1 block, clean):
```python
new_tab("https://www.cnbc.com/video/2026/07/03/why-david-kuo-is-betting-on-boring-stocks-as-tech-rally-cools.html")
wait_for_load(); wait(3)
r = js("""
(function(){
  var o = JSON.parse(document.querySelector('script[type=\"application/ld+json\"]').textContent);
  return JSON.stringify({name:o.name, uploadDate:o.uploadDate, author:o.author||null,
                         h1:(document.querySelector('h1')||{}).innerText});
})()
""")
print(r)
# -> name = title, uploadDate = ISO publish date, h1 = same title. author usually absent on videos.
```
The **analyst name** is almost always the person named in the title/description (e.g. "David Kuo", "Patrick Moorhead", "Jim Cramer"), not a structured field. Read it from `cn:title` + `description`/`summary`. For show segments (Mad Money etc.) the `author`/host is populated (e.g. "Jim Cramer").

## Search-page DOM fallback (only if the API ever changes)
`https://www.cnbc.com/search/?query=<q>&qsearchterm=<q>` renders result cards after ~4s. Verified selectors:
- Cards: `.SearchResult-searchResult` (10 per page, infinite-scroll for more).
- Per card: `.Card-title` (title — **empty for video cards**, use the `a.resultlink` innertext instead), `.SearchResult-author` (author), an eyebrow element with the `section`, a date string like `7/2/2026 11:25:42 PM PST`, and `a.resultlink[href]`. A card is a video if its text contains `VIDEO` / its href contains `/video/`.
Prefer the JSON API — the DOM is messier (video titles land in different nodes).

## Gotchas
- Title key is `cn:title`, not `title`. Type key is `cn:type` (`cnbcvideo` vs `cnbcnewsstory`).
- `author` is frequently `""` on video results — do NOT report "no analyst"; the analyst is named in the title/summary. Only news stories reliably fill `author`.
- `&sort=date` drags in weakly-related videos (matched on one term). For an "analysis of <topic>" task, keep default relevance sort and take the first `cnbcvideo` — it's both on-topic and recent.
- Local `http_get`/urllib to api.queryly.com throws `CERTIFICATE_VERIFY_FAILED` — must set `ssl` context with `verify_mode=CERT_NONE`. Cloud-browser `fetch()` has no such issue.
- No anti-bot, no CAPTCHA, no login seen on search API, search page, or video pages. Cloud exit IP is Hong Kong; CNBC served normally (US-market content, no geo-block observed) — no HK blocking for this site.
- Times in the search-page DOM are rendered "PST"; the API `datePublished` is UTC (`+0000`) while `cn:lastPubDate` is offset-adjusted. Use `datePublished` and convert if you need local time.
