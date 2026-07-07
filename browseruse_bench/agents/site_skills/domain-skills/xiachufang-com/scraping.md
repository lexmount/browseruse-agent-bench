Field-tested on 2026-07-04 (re-verified 2026-07-06) — xiachufang.com (下厨房): search recipes and read name/author/综合评分 from the server-rendered search results HTML.

## Do this first (fastest, no browser needed)

The search page is fully server-rendered and **NOT blocked from the local China IP** — pull it with `http_get` and parse with regex. No login, no JS, no anti-bot on this path.

- Search URL: `https://www.xiachufang.com/search/?keyword=<URL-encoded query>`
- `http_get(url)` returns the raw HTML **as a plain `str`** (not a dict — do not call `.get()` on it).
- Each result is a `<div class="recipe recipe-215-horizontal ...">` block containing:
  - name: `<p class="name"><a href="/recipe/<id>/">NAME</a>` (name is on its own indented line — regex must be DOTALL + strip whitespace)
  - score: `综合评分&nbsp;<span class="score ...">8.7</span>` → the site's 综合评分 (overall rating, 0–10)
  - author: `<p class="author">...<a ...>AUTHOR</a>`

```python
import urllib.parse, re
html = http_get("https://www.xiachufang.com/search/?keyword=" + urllib.parse.quote("红烧肉"))
# html is a str
blocks = re.split(r'<div class="recipe recipe-215-horizontal', html)[1:]
items = []
for b in blocks:
    hm = re.search(r'<p class="name">\s*<a href="(/recipe/\d+/)"[^>]*>\s*(.*?)\s*</a>', b, re.S)
    if not hm:
        continue
    href, name = hm.group(1), re.sub(r'\s+', ' ', hm.group(2)).strip()
    sm = re.search(r'综合评分.*?<span class="score[^"]*">\s*([\d.]+)', b, re.S)
    score = float(sm.group(1)) if sm else None
    am = re.search(r'<p class="author">.*?<a[^>]*>\s*(.*?)\s*</a>', b, re.S)
    author = re.sub(r'\s+', ' ', am.group(1)).strip() if am else ''
    items.append({'name': name, 'author': author, 'score': score, 'href': href})

# "highest rated" = max 综合评分
items.sort(key=lambda x: (x['score'] or 0), reverse=True)
top = items[0]
print(top)  # -> {'name':'家庭版红烧肉','author':'喜欢吃美食的小胖纸','score':9.2,'href':'/recipe/106074497/'}
```

Verified 2026-07-04: `keyword=红烧肉` returns 15 results; highest 综合评分 is **家庭版红烧肉 / 喜欢吃美食的小胖纸 / 9.2** (`/recipe/106074497/`).

## Recipe detail page (if you need to open one recipe)

`http_get("https://www.xiachufang.com/recipe/<id>/")` also works from the local IP. Two reliable extraction points:

```python
import re, json
html = http_get("https://www.xiachufang.com/recipe/106074497/")
# Score: .stats > .score > .number
score = re.search(r'<div class="score float-left">\s*<span class="number">\s*([\d.]+)', html, re.S).group(1)  # "9.2"
cooked = re.search(r'<div class="cooked[^"]*">\s*<span class="number">\s*(\d+)', html, re.S).group(1)          # "68" (人做过)
# Name + author from the ld+json Recipe block (cleanest):
lj = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1)
rec = json.loads(lj)
name, author = rec['name'], rec['author']['name']   # '家庭版红烧肉', '喜欢吃美食的小胖纸'
```
The detail page's ld+json is `@type":"Recipe"` with `name` and `author.name` — the most robust way to get title/author. Note: the visible `.page-title <h1>` gives the name too, but author is NOT near an "作者" label, so use ld+json for author.

## Fallback: cloud browser (only if local IP ever gets blocked)

Same fields via the cloud Chrome DOM. Confirmed identical result (家庭版红烧肉 / 9.2). Use only if `http_get` starts failing — otherwise it's slower for no benefit.

```python
new_tab("https://www.xiachufang.com/search/?keyword=" + __import__('urllib.parse',fromlist=['quote']).quote("红烧肉"))
wait_for_load(); wait(2)
data = js(r"""
(function(){
  var out=[];
  document.querySelectorAll('div.info.pure-u').forEach(function(el){
    var a=el.querySelector('p.name a'); if(!a) return;
    var stats=el.querySelector('.stats')?el.querySelector('.stats').textContent.replace(/\s+/g,' ').trim():'';
    var m=stats.match(/综合评分\s*([\d.]+)/);
    out.push({name:a.textContent.trim(), href:a.getAttribute('href'),
              author:el.querySelector('.author a')?el.querySelector('.author a').textContent.trim():'',
              score:m?parseFloat(m[1]):null});
  });
  out.sort(function(a,b){return (b.score||0)-(a.score||0);});
  return out;
})()
""")
top = data[0]
```
In the live DOM the score lives only inside `.stats` text (`综合评分 X.X`), NOT in a `.score .number` element — parse it out of the stats string. In the raw `http_get` HTML the score IS in `<span class="score">`; the two sources have slightly different markup, so keep the parsers separate.

## Gotchas

- **`http_get` returns a `str`, not a response object.** `r.get('status')` throws `AttributeError: 'str' object has no attribute 'get'`. Just use the string directly; check `len(html)` / substring presence for success.
- **Local China IP is NOT blocked** for xiachufang search + detail pages (tested 2026-07-04). Prefer `http_get` — it's faster and needs no cloud session. Cloud IP (Hong Kong) also works; no 403 seen here.
- **"评分" on xiachufang = 综合评分** (overall rating, 0–10 scale), shown on both search cards and detail pages. That is the field to sort on for "highest rated".
- Search-card name is on its own indented line inside `<p class="name">` — regex MUST use `re.S` (DOTALL) and strip `\s+`, or it returns 0 matches (this bit me: a non-DOTALL name regex found 0 blocks while author/score regexes found 15).
- Result count is modest (15 for 红烧肉) and already the full first page; no pagination needed for a "top rated" pick. For more, append `&page=2` etc.
- Images are lazy-loaded as base64 placeholders with the real URL in `data-src` (not `src`) — irrelevant for name/author/score but note it if you ever scrape images.
