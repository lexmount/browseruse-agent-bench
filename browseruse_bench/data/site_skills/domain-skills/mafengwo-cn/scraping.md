Field-tested on 2026-07-04 (retested 2026-07-06; re-verified 2026-07-06 + found per-destination guide feed)
马蜂窝(mafengwo.cn) is aggressively anti-bot: desktop www is WAF/captcha-walled from every non-mainland IP, so use the **mobile site m.mafengwo.cn on the cloud browser** and extract guide (游记/攻略) fields from article pages. Site-wide keyword search is NOT machine-reachable — see Gotchas.

## Do this first (guide article → title + author + read-count)
The cloud HK IP is captcha-blocked on www.mafengwo.cn but NOT on m.mafengwo.cn.
Article pages `/i/{id}.html` need JS to render, and only render **after you warm the mobile home first** (sets the JSL/anti-bot cookie). Order matters.

```python
# 1) WARM: load mobile home first so the anti-bot cookie is set (article renders blank without this)
new_tab("https://m.mafengwo.cn/"); wait_for_load(); wait(3)
assert js("document.body.innerText.length") > 1000, "home blank -> retry / reopen session"

# 2) Load the guide article on the mobile domain (NOT www — www = captcha)
new_tab("https://m.mafengwo.cn/i/24845346.html"); wait_for_load(); wait(6)
assert "TCaptcha" not in js("document.documentElement.outerHTML"), "hit captcha"

# 3) Extract title / read-count(热度) / author
data = js("""(function(){
  var t=document.body.innerText;
  var i=t.indexOf('阅读');
  var title=document.title.replace(/^\\uD83D\\uDC34\\s*/,'')   // strip 🐴 prefix
                          .replace(/，旅游攻略 - 马蜂窝$/,'')
                          .replace(/ - 马蜂窝$/,'');
  var seg=i>=0?t.slice(Math.max(0,i-60),i+30):'';
  var read=(seg.match(/([\\d.]+[万WwKk]?)阅读/)||[])[1];        // e.g. "1.2W" = 12000
  var date=(seg.match(/(20\\d\\d[.\\-]\\d\\d?[.\\-]\\d\\d?)发布/)||[])[1];
  var lines=t.slice(i,i+60).split(String.fromCharCode(10)).map(s=>s.trim()).filter(Boolean);
  return JSON.stringify({title:title, read:read, date:date, author:lines[1]||null});
})()""")
print(data)
# -> {"title":"云南入门徒步路线集合 | 不躲懒…","read":"1.2W","date":"2026.04.19","author":"Molly的小茉莉"}
```

Verified on two articles (24845346 → author "Molly的小茉莉", read "1.2W"; 24870227 → author "吉米在云游", read "2.8W"). The `document.title` reliably holds the clean guide title (emoji prefix + "，旅游攻略 - 马蜂窝" suffix stripped). The author nickname is the line immediately after the `…发布·{read}阅读` line. `阅读` count = 热度/popularity proxy ("W"=万=×10000).

## Mobile list pages that render (for discovering article IDs)
These m.mafengwo.cn pages render on the cloud browser (and even via local http_get for the top-level ones):
- `https://m.mafengwo.cn/`        home, "推荐攻略" feed with `/i/{id}.html` links
- `https://m.mafengwo.cn/note/`   游记 (travel-notes) feed, many `/i/{id}.html` links
- `https://m.mafengwo.cn/mdd/`    destinations grid; each city → `/mdd/{mddid}` (三亚 = 10030, 云南昆明 etc.)
- **`https://m.mafengwo.cn/yj/{mddid}/`  per-destination 游记/攻略 feed — RENDERS on the cloud (NOT captcha), unlike the www `/yj/` path in Gotchas.** e.g. `https://m.mafengwo.cn/yj/10030/` (三亚) lists that city's guide `/i/{id}.html` links with title+author+read inline. This is the way to get destination-scoped guides when keyword search is dead. Note: the very first `/i/` link in the feed (id 6643673 "什么是宝藏？" by 游记总编辑) is a **site-wide banner, not a destination guide** — skip it.

Collect candidate article links from a list page:
```python
new_tab("https://m.mafengwo.cn/note/"); wait_for_load(); wait(2)
ids = js("""JSON.stringify(Array.from(document.querySelectorAll('a[href*="/i/"]'))
              .map(a=>a.href).filter((v,i,s)=>s.indexOf(v)===i).slice(0,30))""")
```

## Gotchas (paths that FAILED — do not retry these blind)
- **www.mafengwo.cn is 100% captcha-walled on the cloud HK IP.** Every desktop path tested returns a Tencent WAF page (`TCaptcha.js`, body length 0): `/`, `/search/q.php?q=三亚&t=info`, `/gonglve/…`, `/mdd/citylist/…`, `/yj/{mddid}/`, `travel-scenic-spot/…`. Reloading 3× does NOT auto-pass; warming a mobile cookie does NOT let www through. The captcha is a Tencent slider needing interactive solve.
- **Local http_get (mainland IP) hits a different wall on www + all detail/search routes:** returns a 209-byte stub with `var buid="fff…"` + `/C2WF946J0/probe.js` (JSL JS challenge). http_get can't run JS, so these never yield data. Only the mobile *top-level* list pages (`m.mafengwo.cn/`, `/note/`) return full HTML via local http_get; article `/i/{id}.html` returns the probe stub even locally.
- **Keyword search is NOT machine-reachable.** No免登录 search JSON found. Desktop `/search/q.php?q=…&t=info` = captcha. Mobile form action `/group/s.php?type=1&key=三亚` returns a **blank shell** (`<html><head><title>🐴</title></head><body></body>`) both on cloud render and local http_get. The mobile home search button (`#_j_topsearchform .s-btn`, type=button) only toggles a UI panel and fires no search XHR. `m.mafengwo.cn/search/index.php` and `/search/s.php` 404. So the dataset task "搜三亚攻略按热度排序" cannot be done end-to-end via *search* — instead reach the destination's guide feed at `m.mafengwo.cn/yj/10030/` (三亚) and rank by the `阅读` count (heat proxy). Verified 2026-07-06: top 三亚 guide on that feed = "三亚+陵水|带娃海岛游的正确打开方式——好好陪你，也美好自己" by **sakura立可**, 9.8W阅读 (next: 《亚囧记》5W, 三亚七天六晚蜜月游 1.3W).
- **Mobile mdd detail redirects to www→captcha.** `/mdd/10030`, `/mdd/citylist/10030.html`, `/travel-scenic-spot/mafengwo/10030.html` all 302 to www and hit the captcha. BUT the per-destination *guide feed* lives at `/yj/{mddid}/` (see list-pages section) and DOES render — use that, not `/mdd/`.
- **Article render is order-dependent + flaky.** If you open `/i/{id}.html` before warming the mobile home, or right after a fresh session, body renders length 0 (probe.js hasn't set the cookie). Always warm `m.mafengwo.cn/` first, then `wait(6)`. The CDP link also dropped a couple times mid-session under rapid js() calls — keep js() payloads small and re-open the session (`--close` then rerun) if the daemon reports "WebSocket connection closed".
- **Cloud region bias:** cloud exit is Hong Kong; www being captcha-walled is largely IP-driven. A mainland-resident browser would see the normal desktop guide search — this skill's mobile path is the workaround for the HK/non-mainland cloud IP.
