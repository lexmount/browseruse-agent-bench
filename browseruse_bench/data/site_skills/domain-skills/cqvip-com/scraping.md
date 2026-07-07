Field-tested on 2026-07-04
维普网 (cqvip.com) — Chinese academic literature search. It is a Nuxt/Vue SPA; results load via one signed XHR `POST /newsite/advanceSearch`. You cannot call that API standalone (missing signature headers → HTTP 200 with an EMPTY body). The reliable path is to drive the SPA UI so the page fires its own signed request, then read the JSON response you intercept in-page. Extraction is JSON, not DOM-scraping.

## Do this first (verified end-to-end path)
Search runs on the cloud (HK) IP fine — no anti-bot block hit. Article **detail** pages require login, but the **search results JSON has every field you need** (title, authors, journal, year, citations), so never open a detail page.

1. Go straight to the results URL: `https://www.cqvip.com/search?k=<URL-encoded term>`.
2. Install an XHR hook that captures the `advanceSearch` response body into `window.__resp`.
3. Click the literature-type tab (期刊论文 = journal articles) and the sort control — each click makes the page fire a fresh signed `advanceSearch` and repopulate `window.__resp`.
4. Parse `window.__resp` → `data.rows[]` and read fields directly.

```python
# term = 量子计算.  Reuse an existing tab if possible; new_tab's wait_for_load can hang on
# this SPA (keeps sockets open) — use a fixed wait() instead of wait_for_load().
import urllib.parse
term = "量子计算"
new_tab("https://www.cqvip.com/search?k=" + urllib.parse.quote(term)); wait(6)

# 1) hook the search XHR response (idempotent; safe to re-run)
js("""(function(){window.__resp=null;var p=XMLHttpRequest.prototype,_o=p.open,_s=p.send;
p.open=function(m,u){this.__u=u;return _o.apply(this,arguments)};
p.send=function(b){if(String(this.__u).includes('advanceSearch')){this.addEventListener('load',function(){window.__resp=this.responseText})}return _s.apply(this,arguments)}})();'ok'""")

# 2) click 期刊论文 tab (journal-only -> body types:[1]); fires a request
js("""(()=>{let e=[...document.querySelectorAll('*')].filter(x=>x.childElementCount===0&&(x.innerText||'').trim().startsWith('期刊论文'));if(e.length)e[0].click()})()""")
wait(4)

# 3) set sort. Only 3 sorts exist: 相关度 (relevance), 时效性 (timeliness), 被引量 (citations).
#    THERE IS NO 下载量 / download-count sort anywhere on this site (see Gotchas).
js("""[...document.querySelectorAll('span.conditionCss')].find(e=>e.innerText.trim()==='被引量').click()""")
wait(4)

# 4) extract top-N straight from the JSON response
out = js(r"""(()=>{const j=JSON.parse(window.__resp);
return JSON.stringify({total:j.data.total, rows:j.data.rows.slice(0,3).map(d=>({
  title:d.title,
  authors:(d.authorInfo||[]).map(a=>a.name).join('; '),
  journal:d.journalInfo&&d.journalInfo.name,
  issue:d.journalInfo?(d.journalInfo.year+'年第'+d.journalInfo.num+'期'):null,
  isCore:d.journalInfo&&d.journalInfo.isCore,   // 1 = 核心期刊 (e.g. 北大核心/CSCD)
  year:d.year,
  citations:d.byRefCnt                          // 被引量; NO download field exists
}))},null,1)})()""")
print(out)
```

Verified output (journal tab + citation sort, term 量子计算): total 27181; rows[0] = 《分子轨道成分的计算》 / 卢天; 陈飞武 / 化学学报 / isCore 1 / citations 763.

## Filters — all facet-based, applied by clicking chips (each click re-fires the search)
The request body (captured live) uses these params; the UI sets them by chip click. There are no filter params in the URL — the SPA holds all state in memory.

- **Literature type**: `types` array. 1=期刊论文(journal), 2=学位(thesis), 3=会议(conference), 5=专利(patent), 6=标准(standard), 18=报纸(newspaper). Journal-only = `[1]`.
- **Year**: `aggsParams.Y` = list of year strings, e.g. `["2023","2024"]` for a 2023-2024 range. Set by clicking the year chips under 年份 (2026/2025/2024/2023/2022…). NOTE this is a facet list, NOT the `yearStart`/`yearEnd` fields (those exist in the body but stay "" via the UI).
- **Core journals** (核心收录): `aggsParams.C`. Chips are 北大核心 / CSCD / CSSCI / CA / 卓越期刊. Click 北大核心 to restrict to 北大核心 (Peking Univ. core). Each result also carries `journalInfo.isCore` (1/0) and `journalInfo.range` (e.g. ["BDHX","CSCD",...]) so you can post-filter in the JSON too.
- **Sort**: body `sort` = `by_ref_cnt` (被引量/citations) or `""` (相关度/relevance) or timeliness. `order:false` = descending.

Click chips ONE AT A TIME, re-querying the DOM after each (the whole facet panel re-renders on every click, so a stale node reference or a too-fast second click gets lost). After each click, verify it landed by reading the fired request body:
```python
# optional: hook request bodies too, to confirm a facet applied
js("""(function(){window.__lastbody=null;var p=XMLHttpRequest.prototype,_o=p.open,_s=p.send;
p.open=function(m,u){this.__u=u;return _o.apply(this,arguments)};
p.send=function(b){if(String(this.__u).includes('advanceSearch'))window.__lastbody=b?String(b):null;return _s.apply(this,arguments)}})();'ok'""")
# after a chip click + wait(4):
print(js("window.__lastbody?JSON.parse(window.__lastbody).aggsParams:null"))  # -> {Y:["2024"], C:[...], ...}
```
Verified: clicking 2024 chip → body `aggsParams.Y:["2024"]`, total 1249 (journal, term 量子计算). Clicking 期刊论文 → `types:[1]`. Clicking 被引量 → `sort:"by_ref_cnt"`.

## Result-row JSON fields (all present anonymously, no login)
`data.rows[]` each has: `id`, `title`, `year`, `abstr` (abstract), `firstAuthor.name`, `authorInfo[].name` (all authors, ordered), `journalInfo.name` (journal), `journalInfo.num`/`vol`/`issn`/`isCore`/`range`, `byRefCnt` (被引量 citations), `collectedCnt`, `praisedCnt`, `refCnt`, `pageCnt`, `keywordInfo`, `classInfo` (CLC codes). `data.total` = hit count.

## Gotchas (things that FAILED or will bite you)
- **NO download-count data anywhere, anonymously.** The result rows expose `byRefCnt` (citations), `collectedCnt`, `praisedCnt` — but no download/read count field, and the sort UI offers only 相关度/时效性/被引量 (no 下载量 option) on both the general and 期刊论文 views. A task that says "按下载量排序 / sort by download count" cannot be satisfied on cqvip without login; the closest anonymous proxy for popularity is `byRefCnt` (citations). Record this substitution explicitly if you use it.
- **Standalone API call fails silently.** `POST /newsite/advanceSearch` without the client-computed headers (`cqvip-sign`, `signature`, `appId`, `cqvip-ts`, `timestamp`) returns **HTTP 200 with a zero-length body** — not an error, just empty. Do not try to reconstruct these signatures; drive the UI and intercept instead. (Headers seen live, for reference only: `dt:pc`, `cqvipenv:zs`, `cqvip-type:sm`, plus the rotating sign/timestamp/signature.)
- **http_get gets only the SSR shell.** `http_get("https://www.cqvip.com/search?k=…")` (local IP) returns a valid Nuxt HTML page (`data-n-head-ssr`) with `<title>文献检索结果</title>`, but the result list is client-rendered from the signed XHR — the HTML contains NO article data. So neither the local-IP nor the cloud-IP http_get path yields results; only in-page `js()` after driving the SPA works.
- **Detail pages are login-walled.** `https://www.cqvip.com/doc/journal/<id>` 302-redirects to `/account/?serve=…` (login). Get everything from the search-results JSON instead — you never need the detail page for title/authors/journal/year/citations.
- **new_tab + wait_for_load can hang** on this SPA (it holds long-lived connections), timing out the harness call. Use `new_tab(url)` then a fixed `wait(6)` instead of `wait_for_load()`.
- **The search box is Element-UI.** Selector for the query input: `input.el-input__inner` (placeholder 请输入检索词); the search button is `button.s-btn`. But you rarely need them — navigate directly to `/search?k=<encoded term>`.
- **Autocomplete endpoint** `GET /newsite/word/search-prefix?content=<t>&types=1&types=2…` returns suggestions and is unsigned/reachable, but it is prefix suggestions only, not search results.
- **Cloud egress is HK**, but cqvip did not block it (no 403). Results are the same mainland catalogue.
