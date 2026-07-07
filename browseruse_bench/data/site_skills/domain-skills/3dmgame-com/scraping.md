Field-tested on 2026-07-04 — 3DM Mod站 (mod.3dmgame.com) is a Nuxt SPA; all MOD search/list data comes from one JSON API, `POST /api/search/getModlist`.

## Do this first (fastest, no login, no browser render needed)

The whole "search a game's MODs + find the most-downloaded one" task is one JSON endpoint. It works from BOTH the local China IP (urllib/requests/http_get-style) AND the cloud browser (js fetch) — no anti-bot, no auth, no cookies. The API **ignores all sort params**, so you paginate everything and sort client-side.

Endpoint: `POST https://mod.3dmgame.com/api/search/getModlist`
Body (JSON): `{"search": "<关键词>", "page": <1-based>}`
- Only `search` filters. `keyword`/`name`/`limit` are silently ignored (`limit` does NOT change page size — always 24/page). Sort params (`sort`/`order`/`sortBy`) are ignored too.
- Response: `{success, msg, data:{ mods:[...], count:<fuzzy total>, games:[...] }}`
- `count` is the FUZZY total (title OR game_name matches across many games), so for "艾尔登法环" count=375 but only ~359 are actually game_name=="艾尔登法环".

Per-mod fields (all task fields are in the list — no detail call needed):
- `mods_title` — name
- `mods_download_cnt` — download count (the "下载量" the task asks to rank by)
- `mods_updateTime` — update time (ISO UTC, e.g. `2023-01-05T22:15:50.000Z`; page shows it as Beijing time +8h → 2023年1月5日 14:15:50... note: page label reads 14:15 which is a display quirk, the ISO field is authoritative)
- `mods_desc` — functional intro (for simple tool mods this may just repeat the title)
- `mods_click_cnt` (views), `mods_mark_cnt` (favorites), `id`, `game_name`, `mods_type_name`, `user_nickName`

### Runnable — cloud browser (js fetch), full pagination + client-side max-download

```python
res = js("""
(async () => {
  const kw='艾尔登法环';
  const first = await fetch('/api/search/getModlist',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({search:kw,page:1})}).then(r=>r.json());
  const pages = Math.ceil(first.data.count/24);          // 24 per page, fixed
  const reqs=[]; for(let p=2;p<=pages;p++) reqs.push(
    fetch('/api/search/getModlist',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({search:kw,page:p})}).then(r=>r.json()).then(j=>j.data.mods||[]));
  const rest = await Promise.all(reqs);                  // PARALLEL — sequential 16-page loop TIMES OUT the CDP call
  let all=first.data.mods.slice(); rest.forEach(a=>all=all.concat(a));
  const er = all.filter(m=>m.game_name===kw)             // drop fuzzy cross-game matches
               .sort((a,b)=>b.mods_download_cnt-a.mods_download_cnt);
  const t = er[0];
  return {id:t.id, title:t.mods_title, download:t.mods_download_cnt,
          updateTime:t.mods_updateTime, desc:t.mods_desc, url:'https://mod.3dmgame.com/mod/'+t.id};
})()
""")
print(res)
```
Verified result for 艾尔登法环 (2026-07-04): id **192561**, "艾尔登法环 v1.02-v1.08风灵月影34项修改器", download **104617**, updateTime 2023-01-05T22:15:50Z, url https://mod.3dmgame.com/mod/192561.

### Runnable — local IP fallback (Python urllib, POST). http_get() is GET-only so use urllib for the POST.

```python
import json, urllib.request
def get_page(kw, page):
    req = urllib.request.Request("https://mod.3dmgame.com/api/search/getModlist",
        data=json.dumps({"search":kw,"page":page}).encode(),
        headers={"Content-Type":"application/json","User-Agent":"Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())

kw="艾尔登法环"; first=get_page(kw,1); import math
pages=math.ceil(first["data"]["count"]/24)
mods=list(first["data"]["mods"])
for p in range(2,pages+1): mods += get_page(kw,p)["data"]["mods"]
er=[m for m in mods if m["game_name"]==kw]
top=max(er, key=lambda m:m["mods_download_cnt"])
print(top["id"], top["mods_title"], top["mods_download_cnt"], top["mods_updateTime"])
```
Confirmed reachable from local China IP (status 200, count 375 identical to cloud). Local and cloud return the same data — no HK-region skew observed on this endpoint.

## Locating the game / UI path (if you must use the page instead of the API)
- Search box on homepage: `input.v-field__input` (placeholder "在这里搜索任何您想要的模组..."). Type + press Enter → navigates to `https://mod.3dmgame.com/mods?search=<urlencoded-kw>`, which fires the same getModlist API.
- MOD detail page: `https://mod.3dmgame.com/mod/<id>`. It server-renders every field into `document.body.innerText` (title, 作者, 更新 time, download count, view count, version, size). Read via `js("document.body.innerText")` — reliable, no extra API needed.

## Gotchas
- **API ignores sorting.** No `sort`/`order`/`sortBy`/`sortType` param works — always paginate all pages and sort by `mods_download_cnt` in your own code.
- **Page size is hard 24.** `limit`/`pageSize` in the body do nothing. Compute pages = ceil(count/24).
- **`count` is fuzzy.** Includes other games whose title contains the keyword. Filter `m.game_name === '<exact game>'` before ranking, or you may pick a mod from the wrong game.
- **Sequential page loop times out the CDP `js()` call.** Fetching ~16 pages one-by-one inside a single `js()` await-loop hit `Runtime.evaluate timed out`. Fix: fire all pages with `Promise.all` (parallel) — one `js()` call then returns fine.
- **Detail API param unknown.** `/api/mods/getModInfo` exists (returns `{"success":false,"msg":"作品不存在或已被删除"}` for id/mods_id GET or POST — wrong param name). Didn't crack it, and didn't need to: the list API already carries title/desc/download/updateTime, and the `/mod/<id>` page renders full detail as text. Use those instead.
- **`mods_desc` can be thin.** For simple tool/trainer mods it just repeats the title. If you need a richer functional writeup, load `/mod/<id>` and read the rendered body text.
- **GET on getModlist = 404.** It is POST-only with a JSON body; a GET (even with `?search=`) returns a Nuxt 404 page.
- No anti-bot / no rate-limit hit during full 16-page parallel pulls on both IPs.
