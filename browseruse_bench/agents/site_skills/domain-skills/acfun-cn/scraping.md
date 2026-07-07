Field-tested on 2026-07-04 (re-verified 2026-07-06)
AcFun (acfun.cn) video search: extract title / UP (uploader) / play-count, sorted by views — via one no-login JSON endpoint. No anti-bot seen; both the China-IP `http_get` path and the HK cloud-browser path work.

## Do this first (best path: no-login JSON endpoint, works from http_get)

AcFun search has an `ajaxpipe=1` XHR endpoint that returns `{"html": "<fragment>"}`. Set `sortType=2` to get the **view-sorted** list (highest play count first). The sorted results live in the `.normal-list` fragment inside the returned HTML. This endpoint needs NO login and returned 200 from BOTH the local China IP (`http_get`) and the cloud (HK) browser — pick either.

Endpoint (URL-encode the keyword; `鬼畜` = `%E9%AC%BC%E7%95%9C`):
```
https://www.acfun.cn/search?sortType=2&channelId=0&type=video&keyword=<KW>&quickViewId=video-list&reqID=1&ajaxpipe=1&t=<epoch_ms>
```
`sortType` map (verified by capturing the tab XHR): 1=最多评论/relevance-ish, **2=最多观看 (most views)**, 3=最多收藏, 4=最新发布. Default page load = relevance.

### Fastest: pure http_get (China IP), no browser
```python
import time, json, re
kw = "鬼畜"
from urllib.parse import quote
u = ("https://www.acfun.cn/search?sortType=2&channelId=0&type=video&keyword="
     + quote(kw) + "&quickViewId=video-list&reqID=1&ajaxpipe=1&t=" + str(int(time.time()*1000)))
r = http_get(u, headers={"X-Requested-With":"XMLHttpRequest",
                         "Referer":"https://www.acfun.cn/",
                         "User-Agent":"Mozilla/5.0"})
body = str(r.get("body") if isinstance(r, dict) else r)
# NOTE (2026-07-06): ajaxpipe now appends "/*<!-- fetch-stream -->*/" after the JSON,
# so json.loads(body) raises "Extra data". Decode ONLY the first JSON object:
data, _ = json.JSONDecoder().raw_decode(body)   # {"html": "...normal-list fragment..."}
html = data["html"]
# Each result block: <div class="search-video" data-exposure-log='{"title":..,"content_id":..,"authorId":..}'>
# play count in <span class="info__view-count">255.6万次播放</span>
titles = re.findall(r'"title":"(.*?)"', html)          # from data-exposure-log, in list order (title repeats ~3x/block)
# UP name (2026-07-06): the /u/ link now wraps an <img> avatar, so the old
# `/u/\d+"[^>]*>([^<]+)</a>` grabs nothing. Read the dedicated span instead:
ups    = re.findall(r'<span class="user-name">([^<]+)</span>', html)
plays  = re.findall(r'info__view-count[^>]*>([^<]+)<', html)
print(titles[0], "| UP:", ups[0], "|", plays[0])
# -> 2017鬼畜调教年度金曲精选 | UP: AcFun专题 | 255.6万次播放
```
NOTE: `.normal-list` in the fragment is the view-sorted list; item[0] is the top-viewed video. The task answer for `鬼畜` was **title="2017鬼畜调教年度金曲精选", UP="AcFun专题", 255.6万次播放**.

### Cloud-browser variant (same endpoint, same-origin fetch + DOMParser — cleanest parse)
Use if you're already in the browser or want DOM parsing instead of regex:
```python
new_tab("https://www.acfun.cn/"); wait_for_load()   # establish origin/cookies
rows = js("""(async function(){
  var kw=encodeURIComponent('鬼畜');
  var u='/search?sortType=2&channelId=0&type=video&keyword='+kw+'&quickViewId=video-list&reqID=1&ajaxpipe=1&t='+Date.now();
  var resp=await fetch(u,{headers:{'X-Requested-With':'XMLHttpRequest'}});
  var data=JSON.parse(await resp.text());
  var doc=new DOMParser().parseFromString(data.html,'text/html');
  var vids=doc.querySelector('.normal-list').querySelectorAll('.search-video');
  var out=[];
  for(var i=0;i<Math.min(vids.length,10);i++){var v=vids[i];
    var log={};try{log=JSON.parse(v.getAttribute('data-exposure-log'))}catch(e){}
    var ua=v.querySelector('.user-name')||v.querySelector('a[href*="/u/"]'); // 2026-07-06: name moved into span.user-name
    var pc=v.querySelector('.info__view-count');
    out.push({title:log.title, up:ua?ua.textContent.trim():'',
              play:pc?pc.textContent.trim():'', acid:'ac'+log.content_id});
  }
  return out;
})()""")
print(rows[0])   # top-viewed
```

## Alternate: full search page (if you must click the UI)
Load `https://www.acfun.cn/search?type=video&keyword=<KW>`, then the page shows tabs 相关/最多观看/最多评论/最多收藏/最新发布. Putting `sortType=2` in the page URL loads the sorted set but leaves the RELEVANCE list (`#complex-list`, display:block) visible — the sorted list sits hidden in `.normal-list` (display:none). To make the sorted list visible click the tab:
```python
js("(function(){var e=[].find.call(document.querySelectorAll('li'),x=>x.textContent.trim()==='最多观看');e&&e.click();})()")
wait(3)
```
Selectors on the page: result block `.search-video`; title in its `data-exposure-log` JSON attr (`title`, `content_id`, `authorId`); UP link `a[href*="/u/"]`; play count `span.info__view-count`.

## Gotchas
- **(2026-07-06) ajaxpipe body is no longer a bare JSON object.** It now ends with a trailing `/*<!-- fetch-stream -->*/` marker, so `json.loads(body)` raises `JSONDecodeError: Extra data`. Use `json.JSONDecoder().raw_decode(body)` and take the first object (the browser/DOMParser variant is unaffected — `fetch(...).text()` there still parses because it reads only the streamed html chunk... actually use `raw_decode` if you ever `JSON.parse` the raw body).
- **(2026-07-06) UP-name markup changed.** Each `<a href="/u/<id>">` now wraps an `<img class="user-avatar">` followed by `<span class="user-name">NAME</span>`, so the old `/u/\d+"[^>]*>([^<]+)</a>` matches the `<img` and yields nothing. Grab the name from `re.findall(r'<span class="user-name">([^<]+)</span>', html)` (or, in the DOM variant, `v.querySelector('.user-name').textContent`). The `authorId` is still in the exposure-log JSON if you only need the id.
- **The default/visible search list is NOT view-sorted.** The page keeps two lists: `#complex-list` (relevance, shown by default) and `.normal-list` (the sortType list, hidden). If you read `.search-video` blindly off the loaded page you'll grab the relevance order. Always target `.normal-list` (or use the ajaxpipe endpoint, whose fragment IS `.normal-list`).
- **Don't regex `次播放` off a whole `.search-video` innerText** — it also contains "N条弹幕" (danmaku count), and greedy/global matches concatenate numbers (saw bogus "7122.5万"/"233374.3万"). Use the dedicated `span.info__view-count` element, or parse each field from its own node. Play counts are strings like `255.6万次播放` (万 = 10k); convert if you need a number.
- **Play count is not monotonic vs. relevance rank** — proof the sort matters: relevance rank #2 had 14.5万 while a lower relevance item had 151.8万. Only `sortType=2`'s `.normal-list` is truly descending by views.
- `content_id` in the exposure log → video URL `https://www.acfun.cn/v/ac<content_id>`; `authorId` → `https://www.acfun.cn/u/<authorId>`.
- **Anti-bot: none observed.** Homepage, search page, and the ajaxpipe JSON all returned 200 on first try from both the China IP (`http_get`) and the HK cloud browser. No captcha, no login wall for search. (Cloud exit is HK; AcFun did not geo-block it here, unlike Google/Youku.)
- Keyword must be URL-encoded. `reqID` and `t` (epoch ms) are cache-busters; any values work. `quickViewId=video-list` and `ajaxpipe=1` are required to get the JSON fragment instead of the full HTML page.
