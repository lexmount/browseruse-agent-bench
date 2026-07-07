Field-tested on 2026-07-04
docin.com (豆丁网) — Chinese document-sharing site; search returns a static HTML list, per-doc views + upload date live on the detail page. Everything works over plain `http_get` from the local China IP — no cloud browser needed.

## Do this first
1. Hit the search endpoint (GET `search.do`) with the filter params below. The response is static HTML — parse it directly with regex (`http_get`), no JS render needed.
2. From the list you get, in ranked order: **doc id + title + page count**. That's enough to filter by page count (`20-50页`) without opening any detail page.
3. For **exact view count (阅读) and upload date (上传于)** — which are NOT reliable/present on the list — fetch each surviving doc's detail page `/p-<id>.html` and regex them out.
4. Sort/filter client-side. See gotcha on `od=2` — it does NOT give a trustworthy view-count ranking; you must enrich then sort yourself.

## Search endpoint + filter params (all verified)
Base: `https://www.docin.com/search.do` (GET). `nkey` = URL-encoded keyword. Params compose in one URL:
- `searchcat=1001` — standard doc search (always include)
- `dt=3` — **format PPT** (dt=1 doc, dt=2 pdf, dt=3 ppt, dt=4 xls, dt=5 txt)
- `od=2` — sort 最多阅读 (most-read); `od=1` newest, `od=0` relevance
- `numpage=1` = 1-8页, `numpage=2` = **9-100页** (this bucket contains 20-50), `numpage=3` = 100页以上
- `yearType=1` = 2026, `yearType=2` = 2025, `yearType=3` = 2024及以前 (single value only; can't OR two years — see gotcha for "近1年")

Example (PPT, most-read, 9-100 pages):
`https://www.docin.com/search.do?nkey=%E5%B8%82%E5%9C%BA%E8%90%A5%E9%94%80%E6%96%B9%E6%A1%88&searchcat=1001&dt=3&od=2&numpage=2`

## Runnable extractor (verified end-to-end)
Parses the ranked list, filters page count from the list, then enriches each survivor from its detail page for exact views + date. Runs entirely on `http_get`.

```python
import urllib.parse, re, json
from datetime import date

def get(u):
    r = http_get(u)
    return (r.get('body','') if isinstance(r, dict) else str(r))

def num(s):
    if not s: return 0
    s = s.replace(',', '')
    if '万' in s: return int(float(s.replace('万','')) * 10000)
    return int(float(s))

kw = urllib.parse.quote("市场营销方案")
# PPT + most-read + 9-100page bucket (covers 20-50)
search = ("https://www.docin.com/search.do?nkey=%s"
          "&searchcat=1001&dt=3&od=2&numpage=2" % kw)
body = get(search)

# ordered id+title pairs from the title anchor; pagenos in same doc order
pairs   = re.findall(r'title="([^"]+\.pptx?)"[^>]*href="/p-(\d+)\.html"', body, re.I)
pagenos = re.findall(r'class="pageno">(\d+)<', body)
items = [{'id': i, 'title': t, 'pages': int(pagenos[k]) if k < len(pagenos) else None}
         for k, (t, i) in enumerate(pairs)]

def detail(did):
    b  = get("https://www.docin.com/p-%s.html" % did)
    mv = (re.search(r'<a[^>]*class="top_num[^"]*"[^>]*><em>([\d,\.]+[万]?)</em>', b)
          or re.search(r'([\d,\.]+[万]?)阅读', b))
    mp = re.search(r'<span class="info_txt"><em>(\d+)</em>页', b)
    md = re.search(r'上传于\s*(\d{4}-\d{1,2}-\d{1,2})', b)
    return {'views': num(mv.group(1)) if mv else 0,
            'pages': int(mp.group(1)) if mp else None,
            'upload': md.group(1) if md else None}

# filter page count from the LIST first (cheap), then enrich survivors
cand = [it for it in items if it['pages'] and 20 <= it['pages'] <= 50]
enr = [{**it, **detail(it['id'])} for it in cand]

def recent(d):  # "近1年" from 2026-07-05
    if not d: return False
    y, m, dd = map(int, d.split('-'))
    return date(y, m, dd) >= date(2025, 7, 5)

enr = [e for e in enr if recent(e['upload'])]
enr.sort(key=lambda x: x['views'], reverse=True)   # true most-viewed, computed here
print(json.dumps(enr[:3], ensure_ascii=False, indent=1))
```

## Field-verified selectors / regexes
Detail page `/p-<id>.html` (block `<div class="doc_active_info">`):
- Views: `<a class="top_num ..."><em>N</em>阅读` → regex `<a[^>]*class="top_num[^"]*"[^>]*><em>([\d,\.]+[万]?)</em>`. May be shown as `1.2万` — convert (see `num()`).
- Pages: `<span class="info_txt"><em>N</em>页` → regex `<span class="info_txt"><em>(\d+)</em>页`.
- Upload date: literal `上传于YYYY-MM-DD` → regex `上传于\s*(\d{4}-\d{1,2}-\d{1,2})`.
- Title: `<meta property="og:title" content="...">` (clean, no `.pptx` suffix).

Search list page (raw HTML from `http_get`):
- Ordered id+title: `title="([^"]+\.pptx?)"[^>]*href="/p-(\d+)\.html"` (10 docs/page).
- Page count per item: `class="pageno">(\d+)<` — in the same document order as the title anchors; equals the detail-page `pages`, so trust it for the 20-50 filter.

## Gotchas
- **`od=2` (最多阅读) is NOT a reliable view ranking for fresh docs.** Verified: with real single-digit view counts the list order was NOT monotonic by views (e.g. an 11-view doc sat below a 9-view one). To answer "浏览量最高", you MUST enrich each candidate's detail page and sort by `views` yourself. Do not trust list order.
- **Views/date are absent from the list page.** The list has a `热度:` label but its `span.viewhot3` is an empty CSS bar (a level, not a number), and there is no upload date in the list markup. Both only exist on `/p-<id>.html`.
- **"近1年" can't be expressed in one `yearType`.** `yearType` takes a single bucket (2026 / 2025 / 2024及以前). For a rolling 12-month window spanning two calendar years, either omit `yearType` and filter `上传于` dates client-side (as above), or run yearType=1 and yearType=2 separately and merge.
- **Page buckets are coarse (1-8 / 9-100 / 100+).** There is no exact 20-50 filter; use `numpage=2` to narrow, then filter `pages` in code.
- **`http_get` (local China IP) is the primary and sufficient path** for both search and detail — no cloud browser required. Detail HTML is ~50KB static; search HTML ~66KB static, both fully parseable without JS.
- **List container class differs between raw HTML and rendered DOM.** In the live cloud DOM each result is `<dl class="clear dl<docid>">`, but the raw `http_get` body does NOT contain that `clear dl` class — do not key off it when parsing `http_get` output; use the `title="...pptx" ... href="/p-<id>.html"` anchor pattern instead (works on the raw body).
- **`docin.com` did not require login** for search or detail-page reading. Download is gated, but all four target fields (title, pages, upload time, views) are free.
- View counts on very new uploads are tiny (single digits) and tick up over minutes; the exact number is whatever the detail page shows at fetch time.
