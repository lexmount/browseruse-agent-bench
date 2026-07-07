Field-tested on 2026-07-05 — wenku.baidu.com (百度文库) doc search: title / pageNum / uploadTime / summary / viewCount / downloadCount / rating / author / fileType all come pre-rendered in `window.pageData`, no login, no API auth. `http_get` (local IP) is NOT blocked — use it, it is the fastest path.

## Do this first (fastest, no cloud browser needed)

Search results are server-side rendered into `window.pageData` inside the search HTML. Fetch the search page with `http_get` and parse the JSON blob directly. Every field the tasks need is there — no per-doc detail fetch required.

```python
import re, json, urllib.parse, datetime

def _extract_pagedata(html):
    """Pull the `window.pageData = {...}` object out of the search HTML via brace matching."""
    i = html.find("window.pageData")
    j = html.find("=", i) + 1
    while html[j] in " \t\r\n": j += 1
    depth = 0; start = j
    for k in range(j, len(html)):
        c = html[k]
        if c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return html[start:k+1]
    return None

def _clean(s):  # strip <em> highlight tags etc.
    return re.sub("<[^>]+>", "", s or "")

def wenku_search(word, lm=None, pn=1):
    """word=query string. lm=format filter (see mapping below), pn=page number (1-based).
       Returns list of dicts with all doc fields."""
    q = urllib.parse.quote(word)
    url = f"https://wenku.baidu.com/search?word={q}&pn={pn}"
    if lm is not None:
        url += f"&lm={lm}"
    h = http_get(url)
    body = h["body"] if isinstance(h, dict) else str(h)
    pd = json.loads(_extract_pagedata(body))
    items = pd["sulaData"]["__sula_prefetchData"]["items"]["PCSearch"]["result"]["items"]
    out = []
    for it in items:
        d = it.get("data")
        if not d or not d.get("title"):
            continue  # skip ad / empty / non-doc slots
        out.append({
            "title":         _clean(d["title"]),
            "fileType":      d["fileType"],                          # numeric code, see mapping
            "pageNum":       d["pageNum"],                           # 页数
            "createTime":    d["createTime"],                        # unix ts = 上传时间
            "uploadDate":    datetime.datetime.fromtimestamp(d["createTime"]).strftime("%Y-%m-%d"),
            "qualityScore":  round(d.get("qualityScore", 0), 1),     # 评分 (0-5)
            "downloadCount": d.get("downloadCount"),                 # 下载量
            "viewCount":     d.get("viewCount"),                     # 阅读量
            "author":        (d.get("authorInfo") or {}).get("name", "") or "(未署名)",  # 上传者，常为空
            "docID":         d["docID"],
            "url":           d["url"],                               # /view/<docID>.html
            "summary":       _clean(d.get("content", "")),           # 简介/正文摘要
        })
    return out
```

### Task 1 pattern — 搜索关键词，找阅读量最高的文档 (标题/页数/上传时间/简介)

```python
alld = []
for pn in (1, 2, 3):                 # ~12 docs/page; scan a few pages for a real "highest"
    alld += wenku_search("项目管理制度", pn=pn)
seen = set()
uniq = [x for x in alld if not (x["title"] in seen or seen.add(x["title"]))]
best = max(uniq, key=lambda x: x["viewCount"] or 0)
print(best["title"], best["pageNum"], "页", best["uploadDate"], best["viewCount"], "阅读")
print("简介:", best["summary"][:120])
# Verified 2026-07-05: top = 项目管理制度(通用5篇) | 16页 | 2022-03-27 | 2564阅读
```

### Task 2 pattern — 筛选Word格式 10-30页，按下载量取前3 (标题/页数/上传者/下载量/评分)

```python
docs = wenku_search("商业计划书模板", lm=1)          # lm=1 = Word (doc/docx) server-side filter
filtered = [d for d in docs if 10 <= d["pageNum"] <= 30]
top3 = sorted(filtered, key=lambda x: x["downloadCount"] or 0, reverse=True)[:3]
for d in top3:
    print(d["title"], d["pageNum"], "页", "下载", d["downloadCount"], "评分", d["qualityScore"], "上传者", d["author"])
# Verified 2026-07-05: 商业计划书模板(5篇) 12页 dl=133 score=4.0；(标准版)精选 11页 dl=1 4.5；(四篇) 12页 dl=0 3.3
```

## Field reference (all confirmed present in `pageData` PCSearch items)

| task field   | pageData key            | notes |
|--------------|-------------------------|-------|
| 标题         | `title`                 | wrap in `_clean()` to strip `<em>` highlight tags |
| 页数         | `pageNum`               | int |
| 上传时间     | `createTime`            | unix seconds → `datetime.fromtimestamp()` |
| 简介         | `content`               | body excerpt, `_clean()` it |
| 阅读量       | `viewCount`             | int |
| 下载量       | `downloadCount`         | int |
| 评分         | `qualityScore`          | float 0-5 |
| 上传者       | `authorInfo.name`       | **often empty string** — most docs have no author name; report "(未署名)" |
| 格式         | `fileType`              | numeric code (see lm mapping) |
| 链接         | `url` / `docID`         | detail page = `https://wenku.baidu.com/view/<docID>.html` |

## Format filter `lm` mapping (verified by fetching each value)

Add `&lm=N` to the search URL for **server-side** format filtering:

- `lm=1` → **Word** (fileType 1, 4)  ← use for "Word格式"
- `lm=2` → PDF (fileType 7)
- `lm=3` → PPT (fileType 3, 6)
- `lm=4` → Excel (fileType 5)
- `lm=5` → TXT (fileType 8)
- omit `lm` → all formats mixed

## Gotchas

- **`fileType=doc` URL param does NOT work.** The real UI param is `lm` (integer), not `fileType`. Passing `&fileType=doc` returns unfiltered mixed results. Confirmed: use `lm=1` for Word.
- **`http_get` is fine here — do NOT reach for the cloud browser for search.** `http_get` from the local IP returns the full 765KB HTML with `window.pageData` intact (status OK, no captcha, no IP block). This is much faster than `new_tab`+`js`. The cloud-browser path (`new_tab` then `js("window.pageData...")`) also works identically if you ever need it, but is unnecessary.
- **Results are ~12 docs per page.** `pn=1,2,3…` paginates (page 1 gave 12, pages 2-3 gave 10 each). For "阅读量最高/下载量最高" scan 2-3 pages then pick the max — a single page's top is not necessarily the global top.
- **`authorInfo.name` is almost always empty** for these template/制度 docs. That's real site data, not an extraction bug — report "未署名/空".
- **In the live cloud-browser DOM, doc containers have NO `<a href>`** — navigation is JS-driven (each `[class*=doc-item]` carries `sula-resource-id` = the docID). Do not scrape the rendered DOM for links; use `pageData` (or `url`/`docID` from it) instead.
- **The `PCSearch` scene is the one you want.** `pageData.sulaData.__sula_prefetchData.items` also has `PCResource`, `PCSearchVipcard`, `wkAdData` etc. — only `PCSearch.result.items` holds the clean doc records with the fields above.
- **Filter each item on `data.title`** — the items array contains ad / empty / vipcard slots with no `data.title`; skip those.
