Field-tested on 2026-07-04
iQIYI (爱奇艺) movie/TV metadata, cast, episode durations, and hot comments — all reachable through open JSON APIs; **no cloud browser needed, plain `http_get` from local IP works** (all 4 endpoints below returned 200/A00000 over `http_get`).

## Do this first
For ANY iqiyi scraping task, skip the browser. The site is a heavy SPA whose detail-page DOM is nearly empty on load (~600–700 chars, everything lazy-loads), but iqiyi's own JSON APIs are unauthenticated and NOT IP-blocked. Chain: **search API → get `qipuId`/`albumId` → metadata/episode/comment APIs.**

Two id types matter and they differ:
- `playQipuId` / mesh `qipuId` (the big ~16-digit **tvid**, e.g. `7292991076670500`) → use for the **comment** API.
- `albumId` (the ~9-digit id, e.g. `252449101`, found in `pageUrl` resolution or album baseinfo) → use for **baseinfo** and **episode list** APIs. For a movie the mesh result's top-level `qipuId` is the tvid; for a series get `albumId` from the album baseinfo `albumId` field or the `a_*.html` url.

## Step 1 — Search (get the title's ids + inline metadata)
`http_get` this (URL-encode the keyword). Returns rich `albumInfo` per result.
```python
import json, urllib.parse
kw = urllib.parse.quote("流浪地球2")
url = f"https://mesh.if.iqiyi.com/portal/lw/search/homePageV3?key={kw}&current_page=1&mode=1&source=input&pageNum=1&pageSize=10&os=&osShortName=win10&scale=125"
j = json.loads(http_get(url))
# pick first template that has albumInfo (and whose title matches)
tpl = next(t for t in j["data"]["templates"] if t.get("albumInfo"))
ai = tpl["albumInfo"]
# Movie (流浪地球2) verified fields:
ai["title"]            # "流浪地球2"
ai["rating"]           # 9.0  -> iqiyi's OWN score (NOT douban, see Gotchas)
ai["score"]            # 9.0  (same)
ai["hot"]              # 1414 -> iqiyi 热度 value (the public "观看/热度" figure; NOT raw play count)
ai["directors"]["value"]   # [{"qipuId":..,"title":"郭帆"}]
ai["actors"]["value"]      # [{"title":"吴京"}, {"title":"刘德华"}, ...]
ai["category"]["value"]    # "灾难,科幻,冒险"
ai["timeLength"]["value"]  # "02:53:11"  (movie runtime)
ai["releaseTime"]["value"] # "2023-01-22"
ai["qipuId"]               # 7292991076670500  -> tvid for comment API
# metaTags carries the "豆瓣高分" TAG (string), e.g. ai["metaTags"][2]["name"] == "豆瓣高分"
# For a SERIES (隐秘的角落) the albumInfo also has:
ai["totalNumber"]          # 12  (episode count)
ai["pageUrl"]              # https://www.iqiyi.com/v_2ffkws0bgr0.html
ai["videos"]               # [{number, duration(ms), pageUrl, title}, ...] (first page only)
```

## Step 2 — Album baseinfo (director + full main cast + description; needs albumId)
```python
albumId = 252449101   # 隐秘的角落
d = json.loads(http_get(f"https://pcw-api.iqiyi.com/album/album/baseinfo/{albumId}"))["data"]
d["name"]                       # "隐秘的角落"
d["score"]                      # 9.1  (iqiyi score)
d["videoCount"] / d["latestOrder"]   # 12  (episode count)
d["period"]                     # "2020-11-20"
d["description"]                # full synopsis
d["people"]["director"]         # [{"name":"辛爽"}]
d["people"]["main_charactor"]   # [{"name":"秦昊","character":["张东升"]}, ...]  (full cast, with roles)
d["categories"]                 # [{"name":"悬疑","subName":"类型"}, {"name":"内地","subName":"地区"}, ...]
d["firstVideo"]["timeLength"]   # "01:16:47"
```

## Step 3 — Episode list with per-episode durations (needs albumId)
```python
al = json.loads(http_get(f"https://pcw-api.iqiyi.com/albums/album/avlistinfo?aid={albumId}&page=1&size=50"))["data"]
al["total"]                     # 12
for v in al["epsodelist"]:
    print(v["order"], v["duration"], v.get("shortTitle") or v["name"], v["pageUrl"])
# verified: 1 01:16:47 ; 2 49:29 ; 3 36:42 ; ... 12 39:26  (durations vary per episode)
```

## Step 4 — Top hot comments (needs the tvid = mesh qipuId / playQipuId)
```python
tvid = 7292991076670500          # 流浪地球2 (from Step-1 ai["qipuId"])
c = json.loads(http_get(
  "https://sns-comment.iqiyi.com/v3/comment/get_comments.action"
  f"?agent_type=118&agent_version=9.11.5&business_type=17&content_id={tvid}&page=1&page_size=5&types=hot"
))
c["data"]["hotTotalCount"]       # 30
for cm in c["data"]["hot"][:5]:
    print(cm["userInfo"]["uname"], cm.get("likes"), cm["content"])
# verified top comments incl. likes counts (917, 2752, 3370, ...); types=hot = 最热
```

## Gotchas
- **No numeric 豆瓣评分 anywhere in iqiyi's data.** iqiyi only exposes its OWN rating (`albumInfo.rating`/`score`, `baseinfo.score`, e.g. 流浪地球2=9.0, 隐秘的角落=9.1) and a *tag string* `"豆瓣高分"` in `metaTags`. There is NO douban numeric score field. If a task demands the real douban number, you must go to douban.com separately — iqiyi cannot supply it. Do not report iqiyi's 9.0 as a douban score.
- **No raw play count / 观看人数.** The public figure is `albumInfo.hot` (iqiyi 热度, e.g. 1414/1614), not a real view count. Tried `pcw-api…/hotplaycount/<tvid>` and `mesh…/barrage/count` — both `Failed to fetch` (blocked). Report `hot` as the heat/热度 value and note it is not a literal person count.
- **No 编剧 (writers) field.** `baseinfo.people` only has `director`, `main_charactor`, `actor`. Neither the search API nor baseinfo exposes screenwriters. Not obtainable from these endpoints.
- **`content_id` for comments must be the tvid** (the big 16-digit `qipuId`/`playQipuId`), NOT the 9-digit albumId. Using albumId returns empty.
- **Detail pages (`/v_*.html`) are useless for scraping** — SPA, initial DOM ~700 chars, `豆瓣`/`编剧`/`导演` absent from `body.innerText`. Always use the JSON APIs above, never DOM-scrape.
- **`http_get` (local IP) works for all 4 APIs** — verified. No need to spin up the cloud browser for iqiyi. If iqiyi ever starts IP-blocking, the same URLs also work via in-page `fetch()` on the cloud browser (`js(async fetch...)`); use an `AbortController` 7s timeout because long awaits can drop the CDP socket.
- Search: `so.iqiyi.com/so/q_<encoded>` redirects to `www.iqiyi.com/so/q_<encoded>` and renders results, but prefer the mesh JSON API over scraping that page. The mesh `to_page_url` redirect links are unreliable (resolve to trailers/documentaries), don't follow them to find the canonical page — read ids straight from `albumInfo`.
