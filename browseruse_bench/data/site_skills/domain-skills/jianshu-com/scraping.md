Field-tested on 2026-07-04 (re-verified 2026-07-06) (jianshu.com) — site search returns SEO-spam-heavy notes; extract title/author/read-count from the server-rendered `ul.note-list` DOM, and use the "热门文章" (top) sort tab for hottest ranking.

## Do this first (verified path: navigate + click sort tab + DOM extract via js)

The search page is a SPA that server-renders the first page of results into `ul.note-list`. Load it, click the "热门文章" tab to re-sort by hotness, then read the DOM. Do NOT rely on the JSON API — it is aggressively rate-limited (see Gotchas).

```python
import urllib.parse, time
q = "人工智能"
url = "https://www.jianshu.com/search?q=" + urllib.parse.quote(q) + "&page=1&type=note"
new_tab(url); wait_for_load(); wait(3)

# Re-sort by "热门文章" (hottest). The 4 tabs are: 综合排序 · 热门文章 · 最新发布 · 最新评论.
# They are <a> tags with empty href driven by the SPA router — click by text, not href.
clicked = js("""(function(){
  var as=document.querySelectorAll('a');
  for(var i=0;i<as.length;i++){
    if(as[i].innerText.trim().indexOf('热门文章')===0){as[i].click();return 'clicked';}
  }
  return 'no-tab';
})()""")
time.sleep(4)  # SPA re-renders the list in place

# Extract. Icon classes identify each meta number reliably:
#   i.ic-list-read = 阅读量(views), i.ic-list-comments = 评论, i.ic-list-like = 赞
results = js("""(function(){
  function num(li,cls){var i=li.querySelector('i.'+cls);return i?i.parentElement.innerText.trim():null;}
  var lis=document.querySelectorAll('ul.note-list li');var res=[];
  for(var k=0;k<lis.length;k++){var li=lis[k];
    res.push({
      title:(li.querySelector('a.title')||{}).innerText||null,   // full displayed title (no title attr)
      author:(li.querySelector('a.nickname')||{}).innerText||null,
      reads:num(li,'ic-list-read'),
      comments:num(li,'ic-list-comments'),
      likes:num(li,'ic-list-like'),
      href:li.querySelector('a.title')?li.querySelector('a.title').getAttribute('href'):null // e.g. /p/19bce5c1c33d
    });
  }
  return JSON.stringify(res);
})()""")
print(results)  # results[0] = hottest article for the task
```

Verified output (top result for "人工智能", hottest sort):
- title starts "Manus 人工智能软件通用Agent（自主智能体）百度云下载 …", author "苗苗的优惠劵", reads 246, href /p/19bce5c1c33d.
Total-result count label is on the page: `document.body.innerText` contains e.g. "8925 个结果".

## Field reference (all verified in DOM)
- Result container: `ul.note-list li` — first page holds 10 items, server-rendered.
- Title: `a.title` innerText (this IS the full title; there is no `title=` attribute; long spammy titles show in full).
- Author: `a.nickname` innerText.
- Views/comments/likes: inside `.meta`, disambiguated by icon class `ic-list-read` / `ic-list-comments` / `ic-list-like` (in that visual order). Read the parent element's innerText and trim.
- Article link: `a.title` href, relative `/p/<id>`.

## JSON API (exists but do NOT depend on it)
The sort tab fires: `POST /search/do?q=<enc>&type=note&page=1&order_by=top` (order_by=top for hottest; comprehensive sort omits order_by). Content-type is `application/json`. BUT calling it (even once via in-page fetch on the cloud IP) returns HTTP 429 `{"error":[{"message":"搜索过于频繁，请稍等一下再试吧 :D","code":2401}]}` almost immediately, and the cooldown is long (>40s did not clear it in testing). GET returns 404 — it must be POST. Treat the DOM path above as primary; the API is only a theoretical fallback and was never gotten to return data in testing.

## Gotchas
- **"热门文章" (order_by=top) is a hotness score, NOT a pure read-count sort.** Verified: with the top sort applied, item #1 had reads=246 while item #2 had reads=509. If a task literally asks for "most-read", you must sort the extracted `reads` numbers yourself; if it asks for "最热排序"/"hottest", the tab order is the site's own answer and item[0] is correct.
- **Search results are dominated by SEO/网盘 spam** (fake "破解版下载", movie-piracy notes). This is the real content of jianshu search — the top article genuinely is spam, not a bug.
- Sort tabs are empty-href `<a>` elements controlled by the SPA router — clicking by `href` won't work; click by matching innerText prefix "热门文章".
- Clicking the tab mutates `ul.note-list` in place; wait ~4s before extracting. The URL bar does not change (stays `type=note&page=1`).
- Pagination: append `&page=N` and reload (`new_tab`) rather than clicking, since page 1 is what's server-rendered.
- Env for the cloud browser: `BH_LEX_REGION=zh BH_LEX_ISOLATED=1 BU_NAME=wfjianshucom ./bh-lex`. No login required for search. Cloud (HK) IP served results fine — no geo-block observed on jianshu.
