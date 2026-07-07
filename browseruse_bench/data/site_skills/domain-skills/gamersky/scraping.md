# 游民星空 (gamersky.com) — Scraping Skill

Field-tested on 2026-07-04. Search + article extraction for 游民星空, a Chinese
single-player-game portal: game guides (攻略/handbook), news (资讯), and reviews (评测).

## Do this first (fastest path)

**`http_get` (local IP) is NOT blocked here — use it for everything.** No login,
no anti-bot, pages are UTF-8. The cloud browser (`new_tab`/`js`) is only a fallback
you will not need. All three task types (guide search, review lookup, any-game guides)
run through the two functions below.

### Search — pick the category, then regex the result list

Search host is `https://so.gamersky.com/`. Category is a path segment; query is URL-param `s`:

| Category | Path | Use for |
|---|---|---|
| 攻略 (guides)   | `/all/handbook` | game guides / walkthroughs |
| 资讯 (news)     | `/all/news`     | news **and most reviews (评测)** |
| 众评 (user rev) | `/all/ku`       | user-score capsules |
| 下载 (download) | `/all/down`     | game downloads |

Result rows in every category share the same markup: `<div class="t2"><a href="URL">TITLE</a></div>`
(the query term is wrapped in `<font>` tags inside the title — strip them). Article URLs
look like `https://www.gamersky.com/{handbook|news|review|zl}/YYYYMM/NNNNNNN.shtml`.
Pagination: add `&p=2`, `&p=3`, ...

```python
import urllib.parse, re

def gs_search(query, category="handbook", page=1):
    """Return [(url, title), ...] for a gamersky search. category in
    {handbook, news, ku, down}. VERIFIED 2026-07-04 via http_get."""
    q = urllib.parse.quote(query)
    url = f"https://so.gamersky.com/all/{category}?s={q}"
    if page > 1:
        url += f"&p={page}"
    html = http_get(url)
    rows = re.findall(r'<div class="t2"><a href="([^"]+)"[^>]*>(.*?)</a>', html)
    return [(u, re.sub(r'<[^>]+>', '', t).strip()) for u, t in rows]

# guides:  gs_search("黑神话悟空", "handbook")
# reviews: gs_search("赛博朋克2077 评测", "news")   # append 评测 to bias to reviews
```

### Extract article body (guide or review)

Body container is `.Mid2L_con`; title is the page `<h1>`. Works for `/handbook/`,
`/news/`, `/review/`, `/zl/` article types alike.

```python
import re

def gs_article(url):
    """Return {title, text} for a gamersky article. VERIFIED 2026-07-04."""
    html = http_get(url)
    title = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.S)
    title = re.sub(r'<[^>]+>', '', title.group(1)).strip() if title else ""
    # Grab .Mid2L_con block, strip scripts/tags to plain text.
    m = re.search(r'<div class="Mid2L_con"[^>]*>(.*)', html, re.S)
    body = m.group(1) if m else html
    body = re.split(r'更多相关内容|<div class="tequ', body)[0]
    body = re.sub(r'<script.*?</script>', '', body, flags=re.S)
    body = re.sub(r'<style.*?</style>', '', body, flags=re.S)
    text = re.sub(r'<[^>]+>', '', body)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return {"title": title, "text": text[:8000]}
```

For an **IGN/评分 score**, run a regex over the extracted text:
```python
art = gs_article(url)
scores = re.findall(r'[^\n]{0,25}(?:IGN|评测|评分)[^\n]{0,40}?(\d+(?:\.\d+)?)\s*分', art["text"])
```
(游民's own review score is stated in the body, e.g. "游民评测9.1分"; external scores
like "GI评测9分" / "Fami通" are also written inline.)

## Typical task recipes (all validated)

- **黑神话悟空 新手攻略 → 战斗/技能/装备**: `gs_search("黑神话悟空","handbook")` returns
  图文攻略, 全BOSS打法指南, 武器图鉴, 珍玩图鉴, 攻略路线推荐. Open the 图文攻略 or
  全BOSS打法指南 and `gs_article()` the body. Big guides are multi-page — see Gotchas.
- **赛博朋克2077 评测 + IGN评分**: `gs_search("赛博朋克2077 评测","news")` surfaces the IGN
  review, plus 游民评测9.1分 (`/review/`), GI评测9分, Fami通 evaluations. `gs_article()`
  then regex the score.
- **any game guides**: `gs_search("<游戏名>","handbook")` — verified with 艾尔登法环
  (returned 全地图探索图文白金攻略, 全武器收集, 全NPC一览).

## Gotchas

- **Guide-detail pages are paginated.** Big guides split content across
  `..._2.shtml`, `..._3.shtml` (the 全BOSS指南 had ~195 pages). The page list lives in
  `<span ... class="pagecss">` with `<a href=".../{id}_N.shtml">`. `gs_article()` only
  reads page 1. To get more, extract the `_N.shtml` links and loop:
  ```python
  html = http_get(url)
  extra = re.findall(r'href="(https://www\.gamersky\.com/\w+/\d+/\d+_\d+\.shtml)"', html)
  ```
  For a summary of combat/skills/gear, page 1 (intro + first sections) is usually enough;
  the 图文攻略 index guide packs sections into fewer pages than the per-BOSS guide.
- **so.gamersky.com pagination encodes the query oddly.** In-page next-page links use
  `%uXXXX` (JS escape) encoding, not standard %-encoding. Don't reuse those hrefs blindly —
  rebuild the URL with your own `urllib.parse.quote(query)` + `&p=N` (as `gs_search` does).
- **Title `<font>` highlight tags.** Search-result titles wrap the matched query in
  `<font style="color:#e11d03">...</font>`. Always strip tags (`re.sub(r'<[^>]+>','',t)`).
- **`news` category holds most reviews.** There is a `/review/` URL type (游民's own
  评测) and `/zl/` (专栏/columns, incl. player & third-party reviews), but they all appear
  inside the `news` search category — no separate "评测" search tab. Append 评测 to the
  query to bias results toward reviews.
- **Homepage search box → so.gamersky.com.** The site's search `<input name="s">` posts to
  `https://so.gamersky.com/` (data-action). There's a second `<input name="q">` that goes to
  Baidu (搜百度) — ignore it. No form action attribute is set, so you can't scrape the URL from
  a `<form action>`; use the fixed `so.gamersky.com/all/{cat}?s=` pattern above.
- **No public JSON API needed.** The HTML endpoints are un-gated and stable; parsing them via
  `http_get` is faster and simpler than hunting for an internal JSON API.
