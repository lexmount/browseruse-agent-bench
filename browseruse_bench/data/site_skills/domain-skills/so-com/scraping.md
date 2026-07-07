# 360搜索 (so.com) — 搜索结果提取

Field-tested on 2026-07-04. Verified live with queries "Python编程入门教程" and "个人所得税计算器".

**What this covers:** given a query, get the natural (organic) web results from 360搜索 as
`{title, url, display_domain, desc}` — with real destination URLs (not encrypted redirects),
and with 广告/推广 (ads) reliably excluded.

**No login. No captcha.** Both the local `http_get` path and the cloud-browser path returned
full HTML with zero anti-scraping challenges. `captcha`/`yzm` markers = 0 on both queries.

---

## Do this first (最优路径)

Use the **cloud browser** (`goto_url` + `js`) and select on `h3.res-title > a[data-mdurl]`.
The `data-mdurl` attribute holds the **plaintext real destination URL** — no need to decrypt the
`www.so.com/link?m=...` redirect. This selector already excludes ads, image cards, and video cards.

```python
import urllib.parse
q = "个人所得税计算器"          # your query
goto_url("https://www.so.com/s?q=" + urllib.parse.quote(q))
wait_for_load(15); wait(2)

results = js("""
(function(){
  var out=[];
  document.querySelectorAll('h3.res-title > a[data-mdurl]').forEach(function(a){
    var li   = a.closest('li');
    var cite = li ? li.querySelector('cite') : null;          // display domain
    var desc = li ? li.querySelector('.res-desc') : null;     // 摘要
    out.push({
      title:  a.innerText.trim(),
      url:    a.getAttribute('data-mdurl'),                   // REAL destination
      display_domain: cite ? cite.innerText.replace('反馈','').trim() : '',
      desc:   desc ? desc.innerText.trim() : ''
    });
  });
  return out;
})()
""")
for r in results:
    print(r["title"], "|", r["display_domain"], "|", r["url"])
```

Verified output for "Python编程入门教程" (page 1): 4 clean results —
菜鸟教程 `www.runoob.com/python/python-tutorial.html`, 阿里云 `aliyun.com`, 2× CSDN blog.
For "个人所得税计算器": 7 clean results with real domains (gerensuodeshui.cn, bendibao.com, ...).

---

## URL pattern

- Search page: `https://www.so.com/s?q=<url-encoded query>`
- Encode Chinese with `urllib.parse.quote(...)`.
- Page 2+: add `&pn=2` (page number). Page 1 typically shows ~4–10 natural web results.

---

## Fast fallback: http_get + regex (local IP)

`http_get` runs from the LOCAL machine IP (not the cloud browser). so.com does **not** block it,
so it's the fastest path when the cloud browser isn't needed:

```python
import re, urllib.parse
q = "Python编程入门教程"
body = http_get("https://www.so.com/s?q=" + urllib.parse.quote(q))
# natural result anchors: an h3.res-title <a> that has BOTH a /link redirect href AND data-mdurl
blocks = re.findall(
    r'<h3 class="res-title[^"]*"\s*>\s*<a\s+href="https://www\.so\.com/link\?m=[^"]*"'
    r'\s+data-mdurl="([^"]+)"[\s\S]*?>([\s\S]*?)</a>', body)
for url, title_html in blocks:
    title = re.sub(r'<[^>]+>', '', title_html).strip()
    print(title, "->", url)
```

Returns ~820KB HTML fully populated (results are server-rendered, not JS-only), so regex works.
**Prefer the DOM selector over a bare `data-mdurl="..."` regex** — see Gotcha below.

---

## Result anatomy (natural result `<li class="res-list">`)

```html
<li class="res-list">
  <h3 class="res-title ">
    <a href="https://www.so.com/link?m=...ENCRYPTED..."
       data-mdurl="http://www.runoob.com/python/python-tutorial.html"   <!-- REAL url -->
       data-res='{"tp":12,"fr":"kvdb",...}' target="_blank">
       <em>Python</em> 基础教程| 菜鸟教程</a>          <!-- title (<em> highlights query) -->
  </h3>
  <p class="res-desc">...摘要...</p>                    <!-- summary -->
  <p class="g-linkinfo"><cite><a class="g-linkinfo-a">www.runoob.com</a></cite>...</p>  <!-- display domain -->
</li>
```

- The visible click target `href` is an **encrypted** `www.so.com/link?m=<base64...>` redirect — do
  NOT use it as the destination. Use `data-mdurl`, which is the plaintext final URL.
- `<cite>`/`.g-linkinfo-a` gives the display domain; strip a trailing "反馈" (feedback) label.

---

## Gotchas — identifying & skipping 推广/广告 (ads) and non-web cards

- **Ads (推广)** render in `<li class="e-newsl-box">` (and other `e-*` classes) and link out via
  **`http://e.so.com/search/eclk?p=...`** or `e.so.com/search/mid?p=...`. Their title anchors have
  **NO `data-mdurl`**. So `h3.res-title > a[data-mdurl]` already excludes them. If you must detect
  ads explicitly: `href` contains `e.so.com/search/eclk` or the `<li>` class starts with `e-`, or the
  block carries a "广告" label.
- **Do NOT extract with a bare `data-mdurl="..."` regex over the whole page.** `data-mdurl` also
  appears on page chrome and media cards — e.g. `hao.360.com`, `bing.com`, `tv.360kan.com`, and
  `python编程入门教程_360图片` (image card). The `h3.res-title > a[data-mdurl]` DOM selector (or the
  http_get regex that requires the `www.so.com/link?m=` href *before* `data-mdurl`) filters these out.
  In one test, the loose regex returned 12 hits vs 7 true web results.
- **Image / video / short-video cards** (`_360图片`, `短视频大全`, `res-rich` media blocks) are NOT
  `h3.res-title > a` web results and are correctly skipped by the recommended selector.
- **Aggregator/SEO sites are legitimately indexed**, not ads (e.g. gerensuodeshui.cn, geshuiba.com for
  the tax-calculator query). They carry no ad marker. When a task wants "官方/权威" results, filter by
  domain yourself (e.g. prefer `.gov.cn`, known brands like runoob.com/aliyun.com/imooc.com) — the
  scraper returns everything organic; authority judgment is the caller's job.
- **`http_get` vs cloud browser:** `http_get` uses the LOCAL IP and currently works unblocked (fast).
  The cloud browser (`goto_url`/`js`) uses the cloud IP. Both succeeded here with no captcha; use the
  browser path if you ever see `http_get` start returning a verification page.
