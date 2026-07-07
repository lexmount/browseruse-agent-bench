# Dailymotion (dailymotion.com) — scraping

Field-tested on 2026-07-04. Extract search results (title/channel/views/date) — the public REST API `api.dailymotion.com/videos` is auth-free and JSON, so skip the HTML entirely.

## Do this first — public REST API (no login, no token)

`https://api.dailymotion.com/videos` returns clean JSON. It works from the **cloud browser via in-page `fetch`** (China local IP is blocked — `http_get` gets connection-reset, see Gotchas). Use `js()` to run `fetch` in the page.

Key params (all verified 200):
- `search=<free text>` — full-text query. NOTE: matches broadly, e.g. "piano" also hits French/Italian *piano* ("plan"/"slowly"). See Gotchas.
- `sort=relevance` — the site's search relevance ranking (best topical quality). Other values that returned 200: `visited`, `recent`, `trending`, `rated`, `random`, `old`.
- `created_after=<unix_seconds>` — the "last N months" date filter. 6 months = `now - 15552000`.
- `limit=1..100`, `page=1..N`, `has_more` in response tells you when to stop.
- `fields=id,title,views_total,owner.screenname,created_time,url,duration` — pick fields; `views_total` = view count, `owner.screenname` = channel name.

### Task recipe: "classical piano, last 6 months, sort by most viewed, top N"
The API's `sort=visited` (by views) matches too broadly. Best fidelity: fetch with `sort=relevance` + `created_after` (topical + date-filtered), page through, then **sort client-side by `views_total`**. Run this via `js()` in the cloud tab (open any dailymotion page first so fetch has origin):

```python
import time, json
new_tab("https://www.dailymotion.com/"); wait_for_load(); wait(2)
six_mo_ago = int(time.time()) - 15552000
r = js("""
(async function(){
  var six=%d, all=[], seen={};
  for(var p=1;p<=3;p++){                 // 3 pages = up to 300 candidates; >4 pages can time out CDP eval
    var url="https://api.dailymotion.com/videos?search=classical%%20piano&sort=relevance"
      +"&limit=100&page="+p
      +"&fields=id,title,views_total,owner.screenname,created_time,url&created_after="+six;
    var resp=await fetch(url); if(resp.status!==200) return {status:resp.status,page:p};
    var j=await resp.json();
    (j.list||[]).forEach(function(v){ if(!seen[v.id]){seen[v.id]=1; all.push(v);} });
    if(!j.has_more) break;
  }
  all.sort(function(a,b){return b.views_total-a.views_total;});
  return {fetched:all.length, top:all.slice(0,10).map(function(v){return {
    title:v.title, channel:v['owner.screenname'], views:v.views_total,
    created:new Date(v.created_time*1000).toISOString().slice(0,10), url:v.url };})};
})()
""" % six_mo_ago)
print(json.dumps(r, ensure_ascii=False, indent=1))
```
Verified output shape: `{fetched: 194, top:[{title, channel, views, created, url}, ...]}`.

## Alternative: GraphQL token (verified obtainable, schema not solved)
The website uses `https://graphql.api.dailymotion.com/`. A public web-client credential grant returns a bearer token (HTTP 200, verified):
```
POST https://graphql.api.dailymotion.com/oauth/token
Content-Type: application/x-www-form-urlencoded
client_id=f1a362d288c1b98099c7&client_secret=eea605b96e01c796ff369935357eca920c5da4c5&grant_type=client_credentials
```
BUT: introspection is disabled and my guessed `search{videos(query,sort:VIEW_COUNT)}` shape returned `totalCount:0` — the real field/enum names differ. Not solved. Prefer the REST API above. If you must use GraphQL, capture the site's own POST body first (`Network.enable` via `cdp`, then reload `/search/<q>/videos` and read the request payload).

## HTML fallback (only if both APIs die)
- Search URL: `https://www.dailymotion.com/search/<url-encoded-query>/videos` (loads, title = "`<q>` videos - Dailymotion").
- Results lazy-load; initial `document.body.innerText` is tiny (~1.5k chars). Video cards are `a[href*="/video/"]` (each id appears twice — dedupe). There's a `Filters` button in the UI for sort/date, but the API path avoids needing it.

## Gotchas (all observed this session)
- **Local `http_get` is dead here**: `api.dailymotion.com` over the China local IP returns `ConnectionResetError [Errno 54]` at TLS handshake. Everything must go through the cloud browser (`js()` in-page `fetch`). The cloud out-of-region IP reaches the API fine (HTTP 200).
- **`search` is broad full-text**: "classical piano" leaks non-music hits — French *"L'Accident de piano"*, Italian *"piano"* (=slowly/plan). Client-side sort by views surfaces these to the top. If you need stricter music results, keep `sort=relevance` (drops most junk) and/or post-filter titles; do NOT rely on `sort=visited` (worst leakage — pulls unrelated high-view news clips).
- **CDP eval timeout**: a single `js()` doing 5 sequential paged fetches timed out the Runtime.evaluate IPC. Keep it to ≤3 pages per `js()` call, or split across calls.
- **`created_after` is unix seconds**, not ms. 6 months ≈ 15,552,000 s.
- Response field name for views is `views_total` (REST), channel is `owner.screenname`.
