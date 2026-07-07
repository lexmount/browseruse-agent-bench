Field-tested on 2026-07-05
tv.sohu.com = Sohu Video. Movie/album metadata lives on the item detail page (`tv.sohu.com/item/<base64id>.html`) and in the GBK-JSON playlist API `pl.hd.sohu.com/videolist`. The in-site search path (`so.tv.sohu.com/...`) is anti-reptile'd from BOTH the cloud (HK) IP and the local (CN) IP, so do NOT search on-site — find the item URL via an external search engine instead.

## Do this first (fastest verified path)

Sohu's own search endpoints are all blocked/deprecated. To locate a title's play/detail page, query Bing (works from the cloud HK IP) restricted to `site:tv.sohu.com`, grab the `tv.sohu.com/item/...html` link, then load THAT page in the cloud browser (item pages are NOT anti-reptile'd).

```python
# 1) Locate the item page via Bing (cloud browser)
import urllib.parse
q = urllib.parse.quote("长安三万里 site:tv.sohu.com")
new_tab(f"https://www.bing.com/search?q={q}"); wait_for_load(); wait(2)
item_urls = js("""
[...document.querySelectorAll('a')].map(a=>a.href)
  .filter(h=>/tv\\.sohu\\.com\\/item\\//.test(h))
""")
item_url = item_urls[0]          # e.g. https://tv.sohu.com/item/MTI5MjgwNw==.html
print(item_url)

# 2) Load the item detail page (loads clean in cloud browser, no anti-reptile)
new_tab(item_url); wait_for_load(); wait(3)

# 3) Extract the labelled fields from the info block by regex on innerText
fields = js(r"""
(function(){
  var body=document.body.innerText, out={};
  function g(re){var m=body.match(re);return m?m[1].trim():null;}
  out.title    = document.title.replace(/^🐴\s*/,'').split('-')[0]; // "长安三万里"
  out.year     = g(/上映时间：([^\n]+)/);   // "2023"
  out.area     = g(/地区：([^\n]+)/);        // "内地"
  out.type     = g(/类型：([^\n]+)/);        // "历史片/动画片"
  out.director = g(/导演：([^\n]+)/);        // "谢君伟/ 邹靖"
  out.playCnt  = g(/总播放：([^\n]+)/);      // "13万"
  // playlistId is embedded in an inline script -> use it for the JSON API below
  var scr=[...document.querySelectorAll('script')].map(s=>s.textContent).join('\n');
  var m=scr.match(/playlistId["']?\s*[:=]\s*["']?(\d+)/);
  out.playlistId = m?m[1]:null;              // "9797741"
  return out;
})()
""")
print(fields)
```

## Structured JSON (the authoritative source: album + per-clip)

`pl.hd.sohu.com/videolist?playlistid=<id>` returns **GBK-encoded** JSON (optionally JSONP-wrapped if you pass `&callback=`). NOT anti-reptile'd — works from local `http_get` (CN IP), which is the clean way to get raw bytes for GBK decoding. Decode with `gbk`, not utf-8.

```python
import urllib.request, json
pid = "9797741"   # from the item page above
u = f"https://pl.hd.sohu.com/videolist?playlistid={pid}&order=0&pagesize=30&page=1"
raw = urllib.request.urlopen(urllib.request.Request(
        u, headers={"User-Agent":"Mozilla/5.0","Referer":"https://tv.sohu.com/"}), timeout=15).read()
s = raw.decode("gbk","ignore")
if s.lstrip()[:2] in ("x(",):        # strip jsonp wrapper only if you added &callback=
    s = s[s.find("(")+1 : s.rfind(")")]
d = json.loads(s)

# Album-level verified fields:
d["albumName"]     # "长安三万里片花"  (this playlist is the TRAILER album)
d["area"]          # "内地"
d["categories"]    # ["动画片","历史片"]
d["directors"]     # ["谢君伟","邹靖"]
d["actors"]        # ["未知"]   <-- Sohu genuinely has no cast for this animation
d["publishYear"]   # 2023
d["defaultPageUrl"]# http://tv.sohu.com/v/....html  (a clip play page)

# Per-clip fields (each trailer/featurette):
v = d["videos"][0]
v["name"]         # clip title
v["playLength"]   # duration in SECONDS as float, e.g. 15.019
v["publishTime"]  # "2023-01-17"
v["pageUrl"]      # clip play page
```

## What Sohu actually has for movies like 长安三万里 (verified)

- title, 上映时间/publishYear, 地区/area, 类型/categories, 导演/directors, 总播放/playCount — ALL present and reliable (item page + videolist JSON agree).
- 主演 (main cast): Sohu lists `actors:["未知"]` for this animated film — genuinely absent, not a scrape failure. Do NOT trust the "主演：..." string on the item page's `热门电影` sidebar; that belongs to unrelated recommended films, not the current title. There is no reliable main-cast for this title on Sohu.
- 用户评分 (user rating): NOT present anywhere on the Sohu item page or the album JSON for this title. Sohu does not surface a rating here.
- 视频时长: only per-CLIP `playLength` (trailers ≈15s–2min) exists — Sohu hosts ONLY trailers/featurettes for this movie. The "播放正片" (full film) button links out to **iqiyi.com**, so the full-film runtime is not on Sohu.

If the task needs 主演/评分/full-runtime for such a title, report them as "not available on Sohu (trailers only; full film on iQiyi)" rather than fabricating.

## Gotchas

- **Search is walled off.** `https://so.tv.sohu.com/mts?wd=...` and every `so.tv.sohu.com/list_...` search path redirect to `tv.sohu.com/upload/static/special/anti-reptile/index.html` (title "🐴 爬虫forbid") from BOTH the cloud HK IP AND local CN `http_get`. Do not try to search on-site; use Bing `site:tv.sohu.com` from the cloud browser.
- **The homepage search box is a `<sh-search>` web component with a shadow DOM.** Its input is at `document.querySelector('sh-search').shadowRoot.querySelector('input.hd-input')`. Setting `.value` + dispatching `input`/Enter does NOT fire the AJAX suggest or navigate (and even if it did, it targets the walled-off so.tv.sohu.com). Not usable — skip it.
- **Deprecated JSON search APIs.** `api.tv.sohu.com/v4/search/all.json` responds 200 but always `{"data":{"is_empty":1}}` regardless of plat/key params. `api.tv.sohu.com/search/mobile/keyword`, `pl.hd.sohu.com/videohot`, `search.vrs.sohu.com/hui_search` all 200 but return the homepage HTML (redirected). None yield search results. `/v4/album/info/<id>.json` 404s.
- **Encoding:** Sohu pages/APIs are `charset=GBK`. `http_get` chokes decoding as utf-8 (`0xcb ... invalid continuation byte`) — read raw bytes with `urllib.request` and `.decode("gbk","ignore")`. In-browser `js()` fetch of the videolist returns garbled text for the same reason; prefer local `urllib` for this endpoint.
- **fetch() inside the anti-reptile page context fails** with `TypeError: Failed to fetch` (CSP). The `pl.hd.sohu.com/videolist` fetch only succeeds when run from a *clean* Sohu page context — but local `urllib` (CN IP) is simpler and always works for it.
- **IP routing recap for this host:** cloud=HK IP, local `http_get`=CN IP (verified `pv.sohu.com/cityjson` -> `116.130.175.99`, CN). Neither IP bypasses the search anti-reptile. Item pages + `pl.hd.sohu.com/videolist` work from either; Bing needs the cloud browser.
- **Item URL id is base64**, e.g. `MTI5MjgwNw==` decodes to `1292807`. You don't need to construct it — take it from Bing results.
