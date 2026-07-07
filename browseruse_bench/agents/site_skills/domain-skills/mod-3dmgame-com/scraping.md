Field-tested on 2026-07-04 — mod.3dmgame.com (3DM Mod站) is a Vue SPA; ALL search/list data comes from one un-authed JSON API `POST /api/search/getModlist`, so skip the browser entirely.

## Do this first (fastest, verified path)

The site search box (`https://mod.3dmgame.com/mods?search=<kw>`) is backed by a single POST endpoint that returns every field the task needs (title, download count, update time, description) in one call. It works from the LOCAL machine IP too — no IP block observed on the API — so you do NOT need the cloud browser for this task. Use a plain urllib POST.

```python
import urllib.request, json

def search_mods(keyword, order=1, page=1, page_size=24):
    # order: 0=latest(default) | 1=hottest(by clicks) | 2=by download count
    body = json.dumps({
        "page": page, "pageSize": page_size, "search": keyword,
        "original": 0, "time": 0, "order": order,
    }).encode()
    req = urllib.request.Request(
        "https://mod.3dmgame.com/api/search/getModlist",
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())["data"]["mods"]

for kw in ["艾尔登法环", "巫师3", "赛博朋克2077"]:
    print("==", kw)
    for m in search_mods(kw, order=1)[:5]:   # top-5 hottest
        print(
            m["mods_title"],
            "| 下载量:", m["mods_download_cnt"],
            "| 更新:", m["mods_updateTime"][:10],
            "| 简介:", m["mods_desc"][:60],
            "| detail: https://mod.3dmgame.com/mod/%s" % m["id"],
        )
```

## Response fields (from `data.mods[*]`, all confirmed present)

- `mods_title` — MOD 名称
- `mods_download_cnt` — 下载量 (integer)  ← task's 下载量
- `mods_updateTime` — 更新时间 (ISO 8601, e.g. `2026-07-01T17:03:06.201Z`)
- `mods_desc` — 简介 / 描述 (full text, no need to open detail page)
- `mods_click_cnt` — 点击量;  `mods_mark_cnt` — 收藏数
- `id` — mod id → detail URL `https://mod.3dmgame.com/mod/<id>`
- `game_name`, `game_path` — 所属游戏;  `mods_type_name` — 类型
- `user_nickName` — 作者
Wrapper: `{"success": true, "msg": "...", "data": {"mods": [...]}}`.

## The `order` parameter (verified by comparing outputs)

- `order=0` — latest by update time (this is what the website's default search uses)
- `order=1` — hottest, sorted by click count (best for "热门 MOD")
- `order=2` — sorted by download count
For the task "搜索热门游戏的 MOD" use `order=1` (or `order=2` if you specifically want highest downloads).

## Cloud-browser fallback (only if the local POST ever gets blocked)

If the local urllib POST starts failing (IP block / TLS reset), run the identical POST from *inside* a cloud page (uses the cloud IP). Load the site once, then fetch:

```python
new_tab("https://mod.3dmgame.com/"); wait_for_load(); wait(2)
r = js("""(async () => {
  const resp = await fetch('https://mod.3dmgame.com/api/search/getModlist', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({page:1,pageSize:24,search:'艾尔登法环',original:0,time:0,order:1})
  });
  return (await resp.json()).data.mods;
})()""")
```

## Gotchas

- **GET on this endpoint returns HTTP 404** (`{"error":true,"statusCode":404}`). It is POST-only with a JSON body — do not try to pass params in the query string.
- **`http_get` helper is GET-only**, so it cannot hit this endpoint. Use `urllib.request` with a POST body (shown above) instead. That is why the "Do this first" block uses urllib, not http_get.
- The page is a **Vue SPA** — the homepage has no `<form>`, the search box is `<input>` with a volatile id like `input-v-0-1-1` (changes across renders). Don't rely on DOM scraping or a fixed input selector; hit the API directly.
- Search matches **titles broadly** (fuzzy); passing the game name as `search` returns that game's mods. There is no separate "search query too short" gate observed.
- `mods_image_url` / `game_imgUrl` are relative paths under `https://mod.3dmgame.com` if you need images.
- No login/cookie required for search — the API responded 200 with no auth header, from a fresh local process.
