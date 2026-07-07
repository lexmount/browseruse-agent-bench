Field-tested on 2026-07-04 (re-verified 2026-07-06) — 第一财经 (yicai.com): homepage carries live index quotes (incl. NASDAQ Composite) server-rendered in HTML; site search is a clean免登录 JSON API. Both work from local-IP http_get (China mainland); no cloud browser needed.

## Do this first

The dataset task ("查今日纳斯达克综合指数涨跌 + 用站内搜索找解释报道") is fully solvable with two plain local-IP `http_get` calls — no browser, no login, no JS rendering.

### 1) Today's NASDAQ Composite quote — from the homepage HTML (server-rendered)

The homepage embeds a live quote strip. The NASDAQ Composite is `<li class="stock-IXICGI" data-value="IXIC.GI">` with `.stockname / .close / .updown / .updownper`. It is in the **raw HTML** (not JS-injected), so a plain fetch + regex works.

```python
import re
h = http_get("https://www.yicai.com/")          # local IP is fine, returns full HTML
hb = h if isinstance(h, str) else h.get('body', '')
m = re.search(
    r'stock-IXICGI.*?stockname">(.*?)</td>.*?class="close">(.*?)</td>'
    r'.*?class="updown">(.*?)</td>.*?class="updownper">(.*?)</td>',
    hb, re.S)
name, close, updown, pct = m.groups()
print(name, close, updown, pct)   # 纳斯达克综合指数 25832.67 -207.36 -0.80%
```
Direction: the `<li>` also carries a class `stock_green` (down) or `stock_red` (up) — CN convention is green=down, red=up. The sign on `.updown` / `.updownper` is authoritative regardless.
(The homepage strip also has 上证指数, 深证成指, 恒生指数, 道琼斯, 标普500 etc. as `stock-XXXX` li's — same 4-cell table shape, swap the `stock-` id.)

### 2) Site search —免登录 JSON API (best path for "找相关报道")

```python
import json
from urllib.parse import quote
kw = "纳斯达克"
url = "https://www.yicai.com/api/ajax/getSearchResult?keys=" + quote(kw)
# NOTE: local http_get needs a Referer header or it 400s (see Gotchas)
r = http_get(url, headers={"Referer": "https://www.yicai.com/"})
body = r if isinstance(r, str) else r.get('body', '')
j = json.loads(body)
print(j["results"]["numFound"])            # 4347
for d in j["results"]["docs"]:             # ~20 per page
    print(d["title"], "https://www.yicai.com" + d["url"], d["creationDate"])
```
Each `docs[]` item has: `title, url` (relative, e.g. `/news/103260632.html`), `desc` (summary snippet), `author` (list), `creationDate` (e.g. `"昨天 09:15"` or `"07-04 10:04"`), `channelid`, `source`, `tags`, `topics`, `id`, `weight`, `previewImage`, `pdfUrl`.
Paging: `&page=N&pagesize=M` (verified `&page=2&pagesize=5` returns page 2). Default page size ≈ 20.

### 3) (optional) Read a result article — detail page

Article body lives in `.m-text`; title in `<h1>`. Both present in raw HTML from local `http_get`.
```python
import re
hb = http_get("https://www.yicai.com/news/103260632.html")
hb = hb if isinstance(hb, str) else hb.get('body', '')
title = re.search(r'<h1[^>]*>(.*?)</h1>', hb, re.S).group(1).strip()
# body: parse the .m-text container (contains <p> paragraphs)
```
Via cloud browser instead: `js("document.querySelector('.m-text').innerText")` and `js("document.querySelector('h1').innerText")` — both verified.

## Gotchas (field-tested)

- **Search API 400 from local http_get unless you send a `Referer` header.** Bare `http_get(".../getSearchResult?keys=...")` → `HTTP Error 400`. Adding `headers={"Referer": "https://www.yicai.com/"}` fixes it. In-page cloud `fetch()` works without the header (same-origin sends Referer automatically).
- **Search param is `keys=`, NOT `q=`.** The HTML search page is `https://www.yicai.com/search?keys=<urlencoded>`. Passing `?q=...` is silently ignored and returns the full unfiltered corpus (497k results) — a false "success" that looks like it matched everything. Always use `keys=`.
- **NASDAQ quote is server-rendered**, so `http_get` homepage + regex is reliable — no need to spin up the cloud browser for the quote. The whole task can run local-IP only.
- **No IP block observed** on yicai.com from either path. Local-IP (China mainland) http_get reached the homepage (~1MB HTML) and the search API fine; cloud browser (HK exit) also reached everything. This site does not need the cloud fallback, but the cloud `new_tab`+`js` path works identically if local ever gets throttled.
- `creationDate` is a display string ("昨天 09:15", "07-04 10:04", "2分钟前"), not an ISO timestamp — no absolute date/year in the API. If you need the exact date, open the detail page.
- The search HTML page has no `q=`/searchable `<a href*=search>` nav link to scrape for the URL pattern; go straight to the `keys=` URL or the JSON API.
