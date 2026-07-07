Field-tested on 2026-07-04 (re-verified 2026-07-06)
jiemian.com (界面新闻) keyword search: results are server-rendered HTML on a.jiemian.com; grab title/author/time/url with plain HTTP or browser JS. No login, no anti-bot on the search endpoint.

## Do this first (fastest, no browser needed)

The search is server-rendered — a local `http_get` from China IP works and is the cheapest path.

**Search URL (the only working one):**
```
https://a.jiemian.com/index.php?m=search&a=index&opt=new&msg=<URL-encoded keyword>
```
- Keyword param is **`msg`**, NOT `keyword`. `opt=new` = 新版搜索 (relevance+recency), `opt=old` = 旧版搜索.
- Results are sorted latest-first by date, so item[0] is the newest matching article.
- Paginate with `&page=2`, `&page=3`, ...

```python
import urllib.parse, re
kw = "科技创新"
u = "https://a.jiemian.com/index.php?m=search&a=index&opt=new&msg=" + urllib.parse.quote(kw)
r = http_get(u, headers={"User-Agent": "Mozilla/5.0"})
body = r.get("body") if isinstance(r, dict) else str(r)

blocks = re.split(r'<div class="news-view">', body)[1:]
def clean(s): return re.sub(r'<[^>]+>', '', s).strip()
items = []
for b in blocks:
    title  = (re.search(r'title="([^"]+)"', b) or [None, ''])[1]
    href   = (re.search(r'href="(https://www\.jiemian\.com/article/\d+\.html)"', b) or [None, ''])[1]
    author = (re.search(r'class="author">(.*?)</span>', b, re.S) or [None, ''])[1]
    date   = (re.search(r'class="date">([^<]+)</span>', b) or [None, ''])[1]
    items.append({"title": title, "url": href,
                  "author": clean(author).strip('· ').strip(), "date": date})
# task fields: items[0] = newest matching article -> title / author / date
print(items[0])
# -> {'title': '中国—上海合作组织科技创新合作中心西安中心落地西安石油大学',
#     'url': 'https://www.jiemian.com/article/14671157.html',
#     'author': '界面陕西', 'date': '2026/06/30 12:43'}
```
Verified: 10 items per page, each with title+url+author+date. HTTP status ok, ~70KB HTML, contains `news-view` blocks.

## Same extraction via cloud browser (fallback if local IP ever blocked)

```python
import urllib.parse
kw = "科技创新"
new_tab("https://a.jiemian.com/index.php?m=search&a=index&opt=new&msg=" + urllib.parse.quote(kw))
wait_for_load(); wait(3)
items = js("""(function(){
  return [...document.querySelectorAll('.news-view')].map(function(v){
    var a = v.querySelector('.news-header a');
    return {
      title:  a ? a.getAttribute('title') : '',
      url:    a ? a.href : '',
      author: (v.querySelector('.news-footer .author a')||{textContent:''}).textContent.trim(),
      time:   (v.querySelector('.news-footer .date')||{textContent:''}).textContent.trim()
    };
  });
})()""")
print(items[0])  # newest matching article
```

### Result-item DOM (both paths)
- Container: `.news-view`
- Title: `.news-header h3 a` — use the `title` attribute (the visible text has `<em>` keyword highlights inside it)
- URL: same anchor's `href` → `https://www.jiemian.com/article/<id>.html`
- Author/账号: `.news-footer .author a`  (e.g. 界面快报, 界面陕西, 有连云)
- Time: `.news-footer .date`  (format `YYYY/MM/DD HH:MM`)

## Gotchas

- **"筛科技频道" (filter to 科技/tech channel) is NOT supported by search.** The search results page has result-type tabs only — 全部 / 新闻(`type=news`) / 作者(`type=authors`) / 标签(`type=tags`) — and no channel/频道 filter param. Article detail pages also expose no channel breadcrumb (the `/lists/65.html` "科技" links on any page are just the global nav, not the article's own channel). So keyword search returns articles across ALL channels. For the benchmark task, report the latest keyword-matching article's title/author/time from item[0]; there is no site feature to constrain the search to the 科技 channel. If a true channel-only latest list is needed, browse the 科技 channel page `https://www.jiemian.com/lists/65.html` (channel id 65; 硬科技 = 923), but that page is keyword-agnostic and its markup is a messy swiper/carousel mix (author+time are inline in text, not in clean `.author`/`.date` spans) — much harder to parse than search.
- **The OLD search / `keyword=` param is broken — do not use it.** URLs like `.../m=search&a=index&type=news&keyword=科技创新` (param name `keyword`) ignore the query entirely and return the latest 快报/newsflash feed (verified: 特斯拉 and 科技创新 returned identical 导弹试射/火灾 headlines). Always use `opt=new&msg=<kw>`.
- **`https://www.jiemian.com/search.html?keyword=...` is a hard 404** (real 404 page "此处为世界尽头", not a JS route). It appears in the DOM as a leftover but never renders results. Ignore it.
- The homepage search box (`#search-input`) is JS-driven (no `<form>`): on Enter it calls `searchResult(val, dataType)` which does `window.open("https://a.jiemian.com/index.php?m=search&a=index&opt="+dataType+"&msg="+val, "_blank")`. Simulating Enter via synthetic KeyboardEvent does NOT fire its jQuery `keyup` handler reliably in the cloud browser — build the `a.jiemian.com` URL directly instead of driving the box.
- Page titles carry a `🐴` emoji prefix (e.g. `🐴 界面新闻...`); harmless, just cosmetic.
- No anti-bot on the search endpoint from either local China IP (`http_get`) or the HK cloud browser IP — both return full HTML. Prefer `http_get` (faster, no session).
- Article detail pages: author is in a `.author`-class element and timestamps render as relative ("1小时前") in some slots; the search results page gives the absolute `YYYY/MM/DD HH:MM` date, so read the date from the search result, not the detail page.
