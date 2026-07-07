# 51job.com (前程无忧) scraping

Field-tested on 2026-07-04 (re-run 2026-07-06) (re-verified 2026-07-06). Job search on 51job: the results list lives in a Vue SPA at `we.51job.com/pc/search`; extract each posting from the `sensorsdata` JSON attribute on the card. Detail pages and the raw JSON API are both anti-bot walled — do everything from the search results list.

## Do this first (the only reliable path)

Cloud browser only. Navigate to the SPA search URL, click the experience filter in-page (URL params for filters are ignored), then read the structured `sensorsdata` attribute off each `.joblist-item-job` card. No login needed.

```python
# 1. Search: keyword + city. jobArea=020000 is Shanghai. searchType=2 is job search.
new_tab("https://we.51job.com/pc/search?keyword=Java&searchType=2&jobArea=020000")
wait_for_load(); wait(4)

# 2. Apply an experience filter by CLICKING the option label (URL ?workYear= is ignored by the SPA).
#    Valid labels: 无需经验 / 1年以下 / 1-3年 / 3-5年 / 5-10年 / 10年以上
js("""(function(){
  var t=null;
  document.querySelectorAll('*').forEach(function(e){
    if(e.children.length===0 && (e.innerText||'').trim()==='5-10年') t=e;
  });
  if(t){ t.click(); return true; } return false;
})()""")
wait(3)

# 3. Extract every card from its sensorsdata JSON attribute (cleanest source of truth).
rows = js("""(function(){
  var cards = document.querySelectorAll('.joblist-item-job[sensorsdata]');
  var out = [];
  cards.forEach(function(c){
    try{
      var d = JSON.parse(c.getAttribute('sensorsdata'));
      out.push({title:d.jobTitle, salary:d.jobSalary, area:d.jobArea,
                year:d.jobYear, degree:d.jobDegree, companyId:d.companyId, jobId:d.jobId});
    }catch(e){}
  });
  return out;
})()""")
print(rows[:3])   # first 3 = the top results
```

`sensorsdata` fields per card: `jobId, jobTitle, jobSalary, jobArea, jobYear, jobDegree, companyId, jobTime, jobSource, funcType`. One page = 20 cards.

### Verified output (Shanghai "Java", after clicking 5-10年 filter), top 3

| # | 职位 | 薪资 | 地点 | 经验 |
|---|------|------|------|------|
| 1 | Java 开发工程师（全栈方向） | 1.5-2.5万 | 上海·杨浦区 | 5-10年 |
| 2 | 后端(全栈)开发人员 | 1.2-2万 | 上海·普陀区 | 5年及以上 |
| 3 | JAVA开发工程师（密码方向） | 1.5-2.5万·15薪 | 上海·浦东新区 | 3年及以上 |

### Fallback extraction (if sensorsdata attr is ever absent)

```python
rows = js("""(function(){
  return Array.from(document.querySelectorAll('.joblist-item')).map(function(card){
    var name = card.querySelector('.joblist-item-jobname');
    var info = card.querySelector('.joblist-item-jobinfo'); // "salary\\narea\\n..." newline-joined
    var a = card.querySelector('a');
    return {title:name?name.innerText.trim():null,
            info:info?info.innerText.trim().split('\\n'):null,
            href:a?a.href:null};
  });
})()""")
```

## Search URL reference (verified)

- Base: `https://we.51job.com/pc/search`
- `keyword=` — search term (URL-encode Chinese).
- `searchType=2` — job posting search.
- `jobArea=` — city code. Verified: **上海 = `020000`**. (Homepage links show other cities use similar 6-digit codes.) The page title confirms the city, e.g. `【Java,上海招聘，求职】`.
- Filters (experience 工作年限, salary 月薪范围, 学历, 行业, etc.) — **must be applied by clicking the option label in the page, not via URL params.** `?workYear=05` was tested and had NO effect on results.

## Gotchas

- **Detail pages are CAPTCHA-walled.** `jobs.51job.com/.../<id>.html` returns a "🐴 Verification" interstitial (body ~214 chars, no job fields) via the cloud browser. So do NOT plan to open individual postings — get everything you need (title, salary, area, experience, degree, companyId) from the search-list `sensorsdata`. Verified 2026-07-06.
- **Local `http_get` is blocked by Aliyun WAF** on this host — both the detail page and the JSON API return an `aliyun_waf_aa`/`renderData` JS-challenge page instead of data. Local IP path is unusable for 51job; stay on the cloud browser + `js()`.
- **The JSON search API `we.51job.com/api/job/search-pc` responds 200 with valid envelope** when fetched from inside the cloud page (`js()` + `fetch`), but returned `items:[]`/`totalCount:0` with hand-built params — it needs the SPA's exact param/signature set (api_key, timestamp, requestId, pageCode) that I couldn't reproduce. Don't rely on calling it directly; scrape the rendered cards instead. (Same URL via local `http_get` = WAF challenge, above.)
- **Filter click is not a strict AND filter.** After clicking `5-10年`, the list is re-ranked toward 5-10yr postings but still contains cards labeled `3年及以上` / `5年及以上` / `2年及以上` (51job treats a posting's min-experience range as overlapping the filter band). If you need strictly 5-10yr, post-filter on the `year` field yourself. The top results do lead with genuine `5-10年` matches.
- **Cloud exit IP is regional (shows Nanjing on homepage).** The homepage recommends 南京 jobs, but explicit `jobArea=020000` correctly scopes search results to Shanghai — always pass `jobArea` rather than trusting geo defaults.
- SPA keeps the URL static after in-page filter clicks (no query-string reflect), so you can't bookmark a filtered state — always re-navigate to the base search URL then re-click filters in the same `js()` session (page state persists across `bh-lex` calls within a session).
- Cards render lazily; `wait(4)` after `wait_for_load()` before first extract, and `wait(3)` after a filter click.
