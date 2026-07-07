Field-tested on 2026-07-04 (re-verified 2026-07-06)
airbnb.cn (爱彼迎) — capture destination search autocomplete/联想 suggestions via a public no-login JSON endpoint.

## Do this first (BEST path): hit the autocomplete JSON API directly

`GET https://www.airbnb.cn/api/v2/autocompletes-personalized` returns clean `autocomplete_terms[]`.
No login, no cookies, no anti-bot. Works from BOTH the local China IP (`http_get`) AND the
cloud HK IP (in-page `fetch`) — pick either. The public web `key` param is REQUIRED
(omitting it → HTTP 400). This key is baked into the site's JS and was stable during testing:
`d306zoyjsyarp7ifhu67rjxn52tv0t20`.

### Local http_get (China mainland IP) — simplest, verified
```python
import json, urllib.parse
KEY = "d306zoyjsyarp7ifhu67rjxn52tv0t20"

def airbnb_autocomplete(term, n=8):
    q = urllib.parse.quote(term)
    url = ("https://www.airbnb.cn/api/v2/autocompletes-personalized"
           "?locale=zh-CN&currency=CNY&country=CN"
           "&key=" + KEY +
           "&language=zh&num_results=" + str(n) +
           "&user_input=" + q +
           "&api_version=1.2.0&vertical_refinement=homes&region=-1")
    r = http_get(url, headers={"X-Airbnb-API-Key": KEY, "Accept": "application/json"})
    body = r.get("body", "") if isinstance(r, dict) else str(r)
    j = json.loads(body)
    out = []
    for t in j.get("autocomplete_terms", []):
        loc = t.get("location", {}) or {}
        out.append({
            "display_name": t.get("display_name"),           # e.g. "巴黎", "纽约市"
            "sub_title":    t.get("sub_title"),               # e.g. "美国, 纽约州 · City"
            "location_name": loc.get("location_name"),        # full path "美国, 纽约州, 纽约市"
            "type":         t.get("suggestion_type"),         # LOCATION / ...
            "country_code": loc.get("country_code"),          # US / CN / FR ...
            "place_id":     loc.get("google_place_id"),
        })
    return out

for city in ["巴黎", "东京", "纽约"]:
    print(city, "->", [s["display_name"] for s in airbnb_autocomplete(city)])
```
Verified outputs (2026-07-04, local IP):
- 巴黎 → ['巴黎', '埃菲尔铁塔', '第14区', '巴黎北站', '巴黎歌剧院']
- 上海 → ['上海', '上海市', '南京东路步行街', '外滩', '徐家汇']
- 纽约 → ['纽约市', '长岛市', '曼哈顿', '法拉盛', '长岛', '卡茨基尔', '水牛城', '布鲁克林']

### Same call from the cloud browser (in-page fetch, HK IP) — verified backup
Use if the local IP ever gets blocked. Note the HK cloud IP personalizes ranking slightly
differently and returns CNY/CN-region metadata (as requested by the params above).
```python
res = js(r"""
(async function(){
  var KEY="d306zoyjsyarp7ifhu67rjxn52tv0t20";
  var url="https://www.airbnb.cn/api/v2/autocompletes-personalized?locale=zh-CN&currency=CNY&country=CN&key="+KEY+"&language=zh&num_results=8&user_input="+encodeURIComponent("东京")+"&api_version=1.2.0&vertical_refinement=homes&region=-1";
  var r=await fetch(url); var j=await r.json();
  return j.autocomplete_terms.map(function(t){return {name:t.display_name, sub:t.sub_title, cc:(t.location||{}).country_code};});
})()
""")
```
Verified (2026-07-04, cloud): 东京 → 东京都 / 东京 / 池袋 / 新宿站 / 上野站.

## Fallback path: type into the search box and scrape the dropdown (cloud browser)

If the API contract ever changes, drive the real UI. The homepage loads fine on the HK cloud
IP (no geo-block). The search input is `[data-testid=structured-search-input-field-query]`
(placeholder "搜索目的地"); rendered suggestions are `[role=option]`.
```python
new_tab("https://www.airbnb.cn/"); wait_for_load(); wait(3)
js("document.querySelector('[data-testid=structured-search-input-field-query]').focus()")
wait(1)
type_text("巴黎")          # types into the focused input
wait(3)
opts = js("Array.from(document.querySelectorAll('[role=option]')).map(function(e){return e.innerText.replace(/\\n+/g,' | ').trim();})")
# -> ["巴黎 | 法国 · 城市 | ...", "第14区 | ...", "巴黎北站 | ...", "巴黎第一区 | ...", "埃菲尔铁塔 | ..."]
```
The DOM path returns exactly 5 options by default (the UI cap); the API path lets you request
up to `num_results=8+`. Each `[role=option]` innerText duplicates its sub-line twice — split on
`|` and de-dup if you only want name + one descriptor.

## Gotchas
- **`key` param is mandatory.** Without `&key=d306zoyjsyarp7ifhu67rjxn52tv0t20` the endpoint
  returns HTTP 400. The key is a public web key embedded in site JS, not a user secret; it was
  stable across all test calls. If it 400s in future, re-scrape it: load the homepage in the
  cloud browser and read `performance.getEntriesByType('resource')` — the autocomplete URL (with
  the current key) shows up after you type into the search box.
- **Finding the endpoint via a fetch/XHR hook FAILED** — the autocomplete request fires from a
  worker/early bundle and my `window.fetch` monkey-patch (installed post-load) missed it. Use
  `performance.getEntriesByType('resource')` instead; it captured the URL regardless of hook
  timing. That's how the endpoint above was discovered.
- **No anti-bot / no login** encountered on either the homepage or the API. Homepage `innerText`
  is heavily padded with repeated banner text ("从现在起，你将直接看到税前行程总价…") — ignore it;
  the real data is in `[role=option]` or the JSON.
- **HK cloud IP is NOT geo-blocked** by airbnb.cn (unlike Google/优酷 which hard-block HK). Both
  the local-China `http_get` and the cloud `fetch` succeed, so you have two independent paths.
- Response also carries `bounding_box` (lat/lng), `google_place_id`, and `explore_search_params.place_id`
  per term if you need to build a follow-up stays-search URL.
