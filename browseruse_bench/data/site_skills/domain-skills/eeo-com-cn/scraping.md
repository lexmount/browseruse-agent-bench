# eeo.com.cn (经济观察网) — scraping

Field-tested on 2026-07-04 (re-verified 2026-07-06). Search 经济观察网 for a keyword, list results (title/time/url) newest-first, then read an article's title/publish-time/source.

## Do this first (fastest, no browser needed)

The search endpoint accepts **GET** and returns the full result HTML. `http_get` (local IP, China mainland) works on it with **no anti-bot** — no cloud browser required for either search or article pages.

```python
import urllib.parse, re

KW = "房地产市场"   # search keyword
url = f"https://app.eeo.com.cn/?app=search&controller=index&action=search_new&kw={urllib.parse.quote(KW)}&ms=54"
html = http_get(url, headers={'User-Agent': 'Mozilla/5.0'})

# Results live in <ul class="new_list"><li>… — parse each <li> that has an article <a>
rows = []
for li in re.findall(r'<li[^>]*>(.*?)</li>', html, re.S):
    a = re.search(r'<a[^>]*href="([^"]+\.shtml)"[^>]*>(.*?)</a>', li, re.S)
    if not a:
        continue
    title = re.sub(r'<[^>]+>', '', a.group(2)).strip()
    href  = a.group(1)
    t = re.search(r'(\d{4}年\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2})', li)
    if href.endswith('.shtml') and '/epaper/' not in href:  # skip the 电子版 header link
        rows.append({'title': title, 'url': href, 'time': t.group(1) if t else None})

# Results are already sorted newest-first.
# Task: newest article whose TITLE contains the keyword:
hit = next((r for r in rows if KW in r['title']), None)
print(hit)   # -> {'title':'房地产市场呈现明显复苏迹象','url':'http://www.eeo.com.cn/2026/0701/936977.shtml','time':'2026年7月1日 0:50'}
```

Verified 2026-07-04: `kw=房地产市场` returned 15 article rows newest-first; exactly one title contained "房地产市场" → `房地产市场呈现明显复苏迹象`, `2026年7月1日 0:50`, url `.../2026/0701/936977.shtml`.

## Article detail page (title / publish time / source)

Article URLs follow `http://www.eeo.com.cn/YYYY/MMDD/ID.shtml`. Header block is `<div class="xd-b-b">` → `<h1>` title + a `<p>` with `[source] 关注 <span>YYYY-MM-DD HH:MM</span>`.

```python
import re
html = http_get(article_url, headers={'User-Agent': 'Mozilla/5.0'})

title = re.sub(r'<[^>]+>', '', re.search(r'<h1>(.*?)</h1>', html, re.S).group(1)).strip()

m = re.search(r'(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2})', html)
pub_time = m.group(1) if m else None          # e.g. '2026-07-01 00:50'

# source (来源) sits right before the 关注 follow-link; None for original 经观 content
ms = re.search(r'data-focus=[\'"]1[\'"]></span>\s*([^<>]*?)\s*<a[^>]*onclick="hideFocus\(\)"', html, re.S)
source = (ms.group(1).strip() or None) if ms else None
print(title, pub_time, source)
```

Verified 2026-07-04:
- `936977.shtml` → title `房地产市场呈现明显复苏迹象`, time `2026-07-01 00:50`, source `None` (original 经济观察网 content, no external 来源 shown).
- `944706.shtml` → source `证券日报` (reprinted content shows the outlet name here).

Note the two time formats: **search list** shows `2026年7月1日 0:50`; **detail page** shows `2026-07-01 00:50`. Same instant.

## Cloud-browser fallback (only if local IP ever gets blocked)

`new_tab` + `js` on the cloud (Hong Kong) IP work too. Navigate the same GET URL, then extract with the DOM (more robust than regex if markup shifts):

```python
import urllib.parse
new_tab(f"https://app.eeo.com.cn/?app=search&controller=index&action=search_new&kw={urllib.parse.quote(KW)}&ms=54")
wait_for_load(); wait(2)
rows = js(r"""(() => [...document.querySelectorAll('ul.new_list > li')]
  .filter(li => li.querySelector('a[href$=".shtml"]') && !li.querySelector('a[href*="/epaper/"]'))
  .map(li => { const a = li.querySelector('a');
    const m = li.innerText.match(/(\d{4}年\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2})/);
    return {title:a.innerText.trim(), url:a.href, time:m?m[1]:null}; }))()""")
```

Article page via DOM: `document.querySelector('.xd-b-b h1').innerText` for title; the `.xd-b-b p` containing a date holds source + `<span>` datetime.

## Gotchas

- **Search UI is a POST form** (`form#search`, fields `kw` + hidden `ms=54`, action `https://app.eeo.com.cn/?app=search&controller=index&action=search_new`). You do NOT need to fill/submit it — the same endpoint accepts **GET** with `kw` and `ms=54` in the query string. Skip the form entirely.
- `ms=54` is a required magic constant on the search endpoint (it was the hidden field's value). Keep it.
- **Cross-origin `fetch` from a `www.eeo.com.cn` page to `app.eeo.com.cn` fails** (CORS "Failed to fetch"). Don't try in-page fetch across those hosts — navigate to the search URL directly, or use `http_get`.
- `ul.new_list > li` has **separator `<li>` with no `<a>`** interleaved (raw `querySelectorAll` returns ~30 nodes for ~15 results). Always filter to `<li>` that contain an article `<a>`.
- The first `.shtml` link in results is the site header **电子版** (`/epaper/eeocover/1.shtml`) — exclude `/epaper/` and the header link.
- **Source (来源) is often absent**: original 经济观察网 articles show no outlet name; only reprinted pieces (e.g. 证券日报) do. Treat `None` as valid.
- Homepage/search/article pages all load fine on both the local IP and the cloud (Hong Kong) IP — no anti-bot observed on any of them 2026-07-04.
