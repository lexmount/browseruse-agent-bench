Field-tested on 2026-07-04
Baidu web search (baidu.com): drive the `/s?wd=<query>` results page over the cloud browser, extract each result's title/link/full-snippet from the container, then follow the Baidu redirect link to read the source document. `http_get` is blocked by anti-bot — must use the browser path (cloud IP).

## Do this first (verified best path)
1. Build the search URL: `https://www.baidu.com/s?wd=<URL-encoded query>`. You can append refiners inline, e.g. `新能源汽车补贴政策2024 政府官网`.
2. `goto_url(url); wait_for_load(); wait(3)` — the cloud IP is not challenged; local `http_get` is.
3. Extract results by reading each result container's `innerText` (this already contains the answer snippet with dates/numbers — often enough to satisfy the task without opening the source).
4. If you need the full document (application conditions, validity, procedure), follow the Baidu redirect `link` — `goto_url(link)` resolves the `www.baidu.com/link?url=...` hop to the real source URL (e.g. `ndrc.gov.cn`, `gov.cn`), then read the article body.

Prefer results whose source label / final URL is a `.gov.cn` / 党政机关 domain (国家统计局, 中国政府网, 财政部, 发改委) for authoritative data.

## Search + extract results (verified, returns title/link/snippet)
```python
import json, urllib.parse
q = "2024年中国GDP增长率"   # or "新能源汽车补贴政策2024 政府官网"
goto_url("https://www.baidu.com/s?wd=" + urllib.parse.quote(q)); wait_for_load(); wait(3)
res = js(r"""
(function(){
  var out=[];
  document.querySelectorAll('#content_left > div[class*="result"], #content_left > div.c-container').forEach(function(n){
    var h=n.querySelector('h3'); if(!h) return;              // skip ad/inline blocks with no h3
    var a=n.querySelector('h3 a');
    out.push({
      title: h.innerText.trim(),
      link:  a ? a.href : '',                                 // www.baidu.com/link?url=... redirect
      snippet: n.innerText.replace(/\s+/g,' ').trim().slice(0,300)  // full container text = answer text
    });
  });
  return out.slice(0,8);
})()
""")
print(json.dumps(res, ensure_ascii=False, indent=1))
```
The `snippet` field is the whole container text and reliably contains the numeric answer, e.g. for the GDP query it yields 国家统计局: "2024年...国内生产总值(GDP)...达到1349084亿元,按不变价格计算,比上年增长5.0%" and 发布时间 "2025年1月17日".

## Open a source document for full detail (verified)
```python
link = "http://www.baidu.com/link?url=..."   # the .link from a result above
goto_url(link); wait_for_load(); wait(3)
print("FINAL URL:", js("location.href"))      # resolves to real domain, e.g. www.ndrc.gov.cn/...
print("TITLE:", js("document.title"))
body = js(r"(document.querySelector('.article, #UCAP-CONTENT, .pages_content, .TRS_Editor, article, .content') || document.body).innerText.replace(/\s+/g,' ').trim().slice(0,2000)")
print(body)
```
Verified on 汽车以旧换新补贴实施细则 (商消费函〔2024〕75号): the body cleanly yields 补贴标准 (报废旧车购新能源乘用车补贴1万元 / 燃油乘用车7000元), 有效期 (自细则印发之日至2024年12月31日), 申请条件, 发文机关与日期. The gov article-body selector list above matched via the generic `.content`/`body` fallback; if a specific site strips it, `document.body.innerText` still works.

## Gotchas
- `http_get` is BLOCKED. Local-IP `http_get("https://www.baidu.com/s?wd=...")` returns a 1.4 KB "百度安全验证" page (`wappass`), no results. Always use `goto_url`/`new_tab` (cloud IP) — that path is NOT challenged.
- Result links are Baidu redirects (`www.baidu.com/link?url=...`), not the real URL. You only learn the true domain after `goto_url(link)` resolves the hop (read `location.href`). To judge authority before clicking, use the on-page source label instead: `n.querySelector('.c-showurl, [class*="source"]')` shows e.g. "国家统计局\n党政机关".
- Selector must be scoped to `#content_left` and require an `h3`; `#content_left` also contains ad/aladdin blocks (one returned empty title/link in testing) — the `if(!h) return;` guard drops them.
- Baidu injects an emoji into `document.title` (e.g. "🐴 ...百度搜索") — cosmetic, ignore; use it only as a rough load check.
- No clean unauthenticated JSON search API was found/needed; the HTML results page over the cloud browser is the reliable path and its container text already carries the answer.
