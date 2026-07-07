Field-tested on 2026-07-04 — you.163.com (网易严选). Search is a clean免登录 JSON API; product rating/reviews/material/size come from the detail page's server-embedded FTL data (no separate API needed).

## Do this first (fastest verified path)

Two免登录 data sources, both work via **in-page `fetch()` on the cloud browser** (`js(...)`), credentials:'include'. The cloud出口 is 香港 but you.163.com does NOT block it — Yanxuan works fine from the cloud IP. `http_get` (local CN IP) also works but the browser fetch path is what was tested.

1. **Search list → id, name, price(retailPrice), sales(sellVolume), primarySkuId.**
   `GET https://you.163.com/xhr/search/search.json?keyword=<urlenc>&page=<n>`
   Items live at `data.directly.searcherResult.result[]` (10/page). Total pages at `.pagination.totalPage`.
   NOTE: the sibling `.../search/searchList.json` and `item/detail.json` endpoints return `{"code":"403"}` or fail — do NOT use them.

2. **Detail page HTML → goodRate(好评率), reviewCount(评价数), material(材质), size(尺寸).**
   `GET https://you.163.com/item/detail?id=<itemId>` returns HTML with an embedded JS blob `var JSON_DATA_FROMFTL = {...}`. It is **NOT strict JSON** (single-quoted string values), so extract with regex, not JSON.parse on the whole blob. `attrList` sub-array IS valid JSON and parseable on its own.

Yanxuan has NO 5-star numeric "评分". The task's "评分4.8+" maps to **好评率 (positive-review %)**: 4.8/5 ≈ **96%+**. Field key: `commentGoodRates`.

## Runnable code (verified end-to-end: 搜"毛巾" → price<50 & 好评率≥96% → top by sales)

Run inside `js(...)` on the cloud browser. Keep detail fetches to ≤5 per `js()` call — the browser-harness IPC times out (~30s) if you chain too many sequential awaits in one call. Split into multiple calls if needed.

```python
# STEP 1 — search: gather + dedup, filter price<50, sort by sales desc. Use ≤4 pages (top sellers are early).
cand = js(r"""(async()=>{
  const kw=encodeURIComponent('毛巾');const seen=new Set();let all=[];
  for(let p=1;p<=4;p++){
    const j=await (await fetch(`https://you.163.com/xhr/search/search.json?keyword=${kw}&page=${p}`,{credentials:'include'})).json();
    const res=(((j.data||{}).directly||{}).searcherResult||{}).result||[];
    for(const x of res){if(!seen.has(x.id)){seen.add(x.id);all.push({id:x.id,name:x.name,price:x.retailPrice,sell:x.sellVolume});}}
  }
  return JSON.stringify(all.filter(x=>x.price<50).sort((a,b)=>b.sell-a.sell).slice(0,5));
})()""")

# STEP 2 — enrich each candidate from its detail HTML (≤5 ids per call).
# Paste the ids from step 1 into the `cand` array below.
r = js(r"""(async()=>{
  const cand=[{id:4066443,name:'x',price:12.9,sell:3796}]; // <- fill from step 1
  const out=[];
  for(const c of cand){
    const html=await (await fetch(`https://you.163.com/item/detail?id=${c.id}`,{credentials:'include'})).text();
    const gr=(html.match(/commentGoodRates['"]?\s*:\s*['"]([^'"]*)['"]/)||[])[1]||'';      // 好评率 e.g. "99.9%"
    const cc=(html.match(/"commentCount"\s*:\s*(\d+)/)||[])[1]||'';                          // 评价数 aggregate
    let material='';const ar=(html.match(/"attrList"\s*:\s*(\[[\s\S]*?\])/)||[])[1];
    if(ar){try{const a=JSON.parse(ar);const m=a.find(x=>x.attrName==='材质');material=m?m.attrValue:'';}catch(e){}}  // 材质
    const size=(html.match(/"skuSpecValueList"\s*:\s*\[\s*\{[^}]*?"value"\s*:\s*"([^"]+)"/)||[])[1]||''; // first sku spec, usually carries 尺寸 e.g. "…32*70cm"
    out.push({...c,goodRate:gr,reviewCount:cc,material,size});
  }
  // rating 4.8+  ==  好评率 >= 96%
  return JSON.stringify(out.filter(x=>parseFloat(x.goodRate||0)>=96).slice(0,3),null,1);
})()""")
```

Verified live output (毛巾, 2026-07-04), fields all real:
- `Pro星选一次性压缩毛巾洗脸巾` — ¥12.9, sell 3796, 好评率 99.9%, 评价 4363, 材质 棉, 尺寸 24cm*30cm
- `7A抗菌新疆长绒棉亲肤毛巾` — ¥46, sell 6151, 好评率 100%, 评价 506, 材质 纯棉
- `净洁出行一次性灭菌毛巾` — ¥9.9, sell 8300, 好评率 100%, 评价 580, 尺寸 30*60cm

## Field map (verified)
- search item (`searcherResult.result[]`): `id`, `name`, `retailPrice`(现价), `counterPrice`(划线价), `sellVolume`(销量), `primarySkuId`. NO rating/reviewCount/material here.
- detail FTL (`JSON_DATA_FROMFTL`): `"commentCount":<int>`(评价数), `commentGoodRates:'<pct>'`(好评率), `attrList`(含 attrName=材质/工艺/风格/适用季节), `skuSpecList[].skuSpecValueList[].value`(规格串，尺寸常嵌在里面如 "…32*70cm").
- Comment list API (works, gives per-review text + star, NOT a summary rate):
  `GET https://you.163.com/xhr/comment/listByItemByTag.json?itemId=<id>&tag=&page=1&size=1&order=1`
  → `data.commentList[].{content,star,skuInfo}`, `data.pagination.total` (per-tag comment count, differs from FTL aggregate `commentCount`).

## Gotchas
- **好评率 is sparse on low-review items.** `commentGoodRates` is EMPTY (`''`) in the raw-fetched HTML for items with few reviews (e.g. reviewCount 8 → '', 175 → ''; 506 → '100%', 4363 → '99.9%', 308415 → '99.9%'). Popular items (hundreds+ reviews) reliably have it. If good-rate is required and empty, treat that item as not passing a 4.8+ filter (Yanxuan itself doesn't surface a rate for it). This is a real threshold behavior, not a scrape bug.
- **`JSON_DATA_FROMFTL` is not valid JSON** — single-quoted values (`commentGoodRates: '99.9%'`) break `JSON.parse` on the whole blob. Regex-extract individual fields; only the `attrList` sub-array is clean JSON.
- **Dead endpoints (return `{"code":"403"}` or fail-to-fetch, do NOT waste calls on them):** `xhr/item/detail.json`, `xhr/comment/detail.json`, `xhr/comment/getCommentTagListByItemId.json`, `xhr/search/searchList.json`. The two that WORK are `xhr/search/search.json` and `xhr/comment/listByItemByTag.json`.
- **Search results duplicate across pages** — dedup by `id` before ranking (same item recurs on later pages).
- **IPC timeout:** browser-harness `js()` times out (~30s) if one call chains too many sequential `await fetch`. Keep to ≤4 search pages or ≤5 detail fetches per `js()` call; split into multiple calls otherwise. 8 pages / 12 details in one call reliably timed out.
- **No 5-star score exists** on Yanxuan; only 好评率%. Map "评分X" tasks to 好评率 (X/5*100 ≥ threshold).
- **No login required** for search or detail. `credentials:'include'` is harmless but not necessary.
- Cloud出口是香港但 you.163.com 不封香港IP，价格显示人民币，无地区化偏差（与 Google/优酷 等不同）。
