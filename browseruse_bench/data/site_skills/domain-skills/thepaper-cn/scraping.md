# thepaper.cn (澎湃新闻) — search & article scraping

Field-tested on 2026-07-04 (re-verified 2026-07-06). Full-text news search on 澎湃新闻 via one un-authenticated JSON POST endpoint; time-sorted results with title / publish-time / source.

## Do this first — the JSON search API (no login, no browser needed)

Everything the search page shows comes from a single POST endpoint. It answers over
**local IP** (plain `urllib`, no cookies/token/referer) and over the cloud browser
(`js` fetch) equally — no anti-bot on it. Prefer the local `urllib` path; it is the fastest.

```
POST https://api.thepaper.cn/search/web/news
Content-Type: application/json
body: {"word":"气候变化","orderType":1,"pageNum":1,"pageSize":10,"searchType":1}
```

`orderType` controls the sort (verified by inspecting returned pubTime):
- `1` = **按时间，从新到旧 (newest first)**  ← use this for "按时间排序 / 最近发布"
- `2` = 按时间，从旧到新 (oldest first — returns 2014 articles at top)
- `3` = 按相关度 (relevance, this is the site default)

`searchType:1` = 新闻/news. `pageSize` up to 10 works; page with `pageNum`.
`data.total` caps at 10000, `data.hasNext` / `data.nextPageNum` drive pagination.

### Local-IP one-shot (fastest, verified working from CN mainland IP)

```python
import json, urllib.request

def paper_search(word, order=1, page=1, size=10):
    body = json.dumps({"word": word, "orderType": order, "pageNum": page,
                       "pageSize": size, "searchType": 1}).encode()
    req = urllib.request.Request(
        "https://api.thepaper.cn/search/web/news", data=body,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())

import re
def clean(s): return re.sub(r"<[^>]+>", "", s or "")   # strip <font> highlight tags

j = paper_search("气候变化", order=1)          # newest first
for x in j["data"]["list"]:
    print(x["pubTime"],                          # display time: "5小时前" or "2026-06-28"
          "|", (x.get("nodeInfo") or {}).get("name"),   # source / 栏目, e.g. "全球速报"
          "|", clean(x["name"]),                 # title
          "| https://www.thepaper.cn/newsDetail_forward_" + str(x["contId"]))
```

### Field map (per item in `data.list`)
- `name` — title. **Contains `<font color="#00a5eb">…</font>` highlight tags around the
  matched keyword; strip them** (`re.sub(r"<[^>]+>","",name)`).
- `pubTime` / `pubTimeNew` — human display time: relative ("5小时前", "6天前") for recent,
  absolute "YYYY-MM-DD" for older. `pubTimeLong` — epoch **ms**, use it to sort/compare reliably.
- `nodeInfo.name` — the source column / 栏目 (e.g. "全球速报", "快看", "上直播"). `nodeInfo`
  can be null on some cards — guard with `(x.get("nodeInfo") or {}).get("name")`.
- `contId` — article id. Detail URL = `https://www.thepaper.cn/newsDetail_forward_{contId}`.
- `summary` — snippet (also carries `<font>` tags).
- `praiseTimes` — like count; `pic` — thumbnail.

Note: with `orderType:1` the top items are the newest articles whose **body** matches the
keyword, so a title may not literally contain the word (the match is in `summary`). That is
correct behavior for "sort by time" — the task wants recency, not relevance.

## Alternative — capture via the cloud browser (only if the API path is ever blocked)

The search UI is a Next.js SPA; the `?searchWord=` URL param seeds the same POST above.

```python
new_tab("https://www.thepaper.cn/searchResult?searchWord=" + urllib.parse.quote("气候变化"))
wait_for_load(); wait(3)
# results render into .searchresult__* > ul > li > .mdCard
res = js(r"""(async () => {
  const r = await fetch('https://api.thepaper.cn/search/web/news',{
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({word:'气候变化', orderType:1, pageNum:1, pageSize:10, searchType:1})});
  const j = await r.json();
  return j.data.list.map(x=>({
    title: x.name.replace(/<[^>]+>/g,''),
    time: x.pubTime,
    source: (x.nodeInfo||{}).name,
    url: 'https://www.thepaper.cn/newsDetail_forward_'+x.contId }));
})()""")
```

DOM fallback (if you must scrape rendered cards instead of calling the API): each result is
a `.mdCard` containing `a[href^="/newsDetail_forward_"]`, `h2` (title), and a trailing `<p>`
whose two/three `<span>`s are `[source, time, commentCount]`.

## Gotchas
- **`http_get()` helper is GET-only** — it cannot hit this POST endpoint. A bare GET to the
  URL returns `{"code":99998,"desc":"系统繁忙"}` (this only proves the host is reachable, it is
  NOT the search result). Use `urllib.request` with `data=` (POST) as shown above.
- The `searchResult` page's own React handler fires the POST **after** hydration; hooking
  `XMLHttpRequest`/`fetch` only catches it if the hook is installed *before* you trigger a new
  search (typing a fresh term + Enter). A hook installed after results already rendered sees
  nothing. Easier to just call the API directly.
- `orderType` default on the site is `3` (relevance). If you forget to set it, results are NOT
  time-sorted — always pass `orderType:1` for "最近发布 / 按时间".
- Titles and summaries always carry `<font color="#00a5eb">` highlight spans — strip HTML tags
  before using the text.
- Cloud browser exit IP is Hong Kong, but this endpoint is not geo-fenced and returns identical
  data from local CN IP and HK cloud IP — no regionalization observed.
- `data.total` is clamped to 10000 even when more exist; treat it as "≥10000", not exact.
```
