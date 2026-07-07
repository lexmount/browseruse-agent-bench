# suning.com scraping

Field-tested on 2026-07-04 (via Lexmount cloud Chrome, HK exit IP). Suning (苏宁易购) product search: results live at `search.suning.com`, all task fields (title/price/capacity/energy-grade/review-count) are readable off the results DOM with **no login** — the product detail page and most review APIs are login/cluster-gated, so scrape the list page, do not open detail pages.

## Do this first (免登录, one page, all fields)

Search URL pattern: `https://search.suning.com/<URL-encoded-keyword>/`. Results render into `.item-bg` cards. Each card carries everything you need; extract in one `js()` call:

```python
import urllib.parse
kw = urllib.parse.quote("海尔冰箱")
new_tab(f"https://search.suning.com/{kw}/"); wait_for_load(); wait(3)
# scroll once to trigger the lazy-loaded second half of the page
js("window.scrollTo(0, document.body.scrollHeight)"); wait(2)

data = js(r"""
(function(){
  function detailUrl(it){
    var a=it.querySelector('a[href*="product.suning.com"]'); if(!a) return '';
    var hr=a.getAttribute('href');
    if(hr.indexOf('//product')===0) return 'https:'+hr.split('#')[0];      // organic item
    if(hr.indexOf('product.suning.com')===0) return hr.split('#')[0];
    var m=hr.match(/clickUrl=([^&]+)/);                                     // ad item: real url hides in clickUrl=
    return m?decodeURIComponent(m[1]).split('#')[0]:'';
  }
  function evNum(s){ var m=s.match(/([\d.]+)\s*(万)?\+?评价/); if(!m) return 0;
    var n=parseFloat(m[1]); if(m[2]) n*=10000; return n; }
  var rows=[];
  document.querySelectorAll('.item-bg').forEach(function(it){
    var cfg=((it.querySelector('.info-config')||{}).innerText||'').trim();  // e.g. "541升 |1级 |变频(省电)"
    var capM=cfg.match(/(\d+)\s*升/);  var cap=capM?parseInt(capM[1]):0;    // capacity (升/L)
    var enM =cfg.match(/(\d)\s*级/);   var en =enM?enM[1]+'级':'';           // energy grade (能效等级)
    var price=parseFloat(((it.querySelector('.def-price')||{}).innerText||'').replace(/[^\d.]/g,''))||0;
    var ev=((it.querySelector('.info-evaluate')||{}).innerText||'').trim().split('\n')[0]; // "1400+评价"
    rows.push({
      title:((it.querySelector('.title-selling-point')||{}).innerText||'').trim(),
      price:price, capacity:cap, energy:en,
      reviewText:ev, reviewNum:evNum(ev),
      url:detailUrl(it)
    });
  });
  return JSON.stringify(rows);
})()
""")
import json
rows = json.loads(data)
# task filters: capacity >= 400L, price 3000-5000, rank by review count (proxy for 评分)
cand = [r for r in rows if r["capacity"]>=400 and 3000<=r["price"]<=5000]
cand.sort(key=lambda r: r["reviewNum"], reverse=True)
print(json.dumps(cand[:5], ensure_ascii=False, indent=1))
```

### Verified selectors (all read off `.item-bg` cards, no login)
- `.title-selling-point` → full product title / 型号 (contains the BCD-xxxx model string). **实测可用**
- `.def-price` (or `.price-box`) → price, text like `¥2515.15到手价`; strip non-digits. **实测可用**
- `.info-config` → one line packing 容量+能效+制冷方式, e.g. `416升1级风冷(无霜)` or `541升 |1级 |变频`. Regex `(\d+)升` → capacity, `(\d)级` → energy grade. **实测可用**
- `.info-evaluate` → review count, text like `50+评价` / `1400+评价` / `1.2万+评价`. **实测可用**
- product URL: read the `a[href*="product.suning.com"]` href. Organic items give `//product.suning.com/<vendor>/<code>.html` directly; ad items wrap it in a `th.suning.com/calCpcClicks?...&clickUrl=<encoded>` tracker — pull the real URL from the `clickUrl` param. **实测可用**

## Rating (评分) — best available免登录 signal is review COUNT, not a numeric score
The results DOM exposes **review count** (`.info-evaluate`) but **no numeric star rating**. Use review count to rank "评分最高/最受欢迎". If a true numeric rating is required, there is a JSONP review API (below) but it is unreliable per-SKU — see Gotchas.

Review-satisfy API (navigate via `new_tab`, it is JSONP `satisfy({...})`):
```
https://review.suning.com/ajax/review_satisfy/general-<20digitCode>-<shopCode>-----satisfy.htm
```
- `<20digitCode>` = the product code from the detail URL, left-zero-padded to 20 digits (e.g. `12442250733` → `000000000012442250733`).
- `<shopCode>` = vendor from the URL (`0000000000` for Suning self-operated).
- Returns JSON with `qualityStar` (0–5 rating), `goodRate` (好评率), `totalCount`, and star-bucket counts.

```python
code="12442250733"; shop="0000000000"
c20=code.zfill(20)
new_tab(f"https://review.suning.com/ajax/review_satisfy/general-{c20}-{shop}-----satisfy.htm")
wait_for_load(); wait(1)
raw = js("document.body.innerText")   # satisfy({...})
import json,re
obj = json.loads(re.search(r'satisfy\((.*)\)', raw).group(1))
rc = obj["reviewCounts"][0]
print(rc["qualityStar"], rc["goodRate"], rc["totalCount"])
```

## Gotchas (verified failures / quirks)
- **Detail page requires login.** Opening `https://product.suning.com/<vendor>/<code>.html` in the cloud browser 302-redirects to `passport.suning.com/ids/login` (title becomes 用户登录). So do NOT rely on the detail page for any field — everything the task needs is on the search list page. **实测：详情页跳登录。**
- **`review_satisfy` API often returns `totalCount:0` / `qualityStar:5.0` for a SKU even when the list card shows "1400+评价".** Suning aggregates reviews at the parent-cluster code, and the search SKU code frequently is not that cluster code, so the SKU-level satisfy call reports empty. Treat `qualityStar` as unreliable unless `totalCount>0`. The list-page `.info-evaluate` count is the trustworthy免登录 popularity signal. **实测：SKU 级 satisfy 常返回 0。**
- `review_count` / `cluster_review_count` endpoint variants return Suning's "商品暂时无法显示" error page — only `review_satisfy` responds. The wrong `commodityCode` format also returns `{"returnMsg":"商品类型传参有问题"}`. **实测：只有 review_satisfy 通。**
- **CORS blocks in-page `fetch()`** of `review.suning.com` from the search/product page context (`Failed to fetch`). Use `new_tab(apiUrl)` + `js("document.body.innerText")` to read JSONP instead — navigation bypasses CORS. **实测。**
- **Pagination is not URL-driven.** `?cp=1` and `/&cp=1` both return page-1 content unchanged. The page ships ~40 `.item-bg` cards; the second half is lazy-loaded on scroll (initial DOM has ~81 partial nodes → 40 full cards after scroll). To get more candidates, `window.scrollTo(0, document.body.scrollHeight)` and re-extract, or refine the keyword (e.g. add 容量/型号 terms) rather than paging. **实测：cp 参数无效。**
- **Cloud exit IP is Hong Kong → regionalized prices.** Displayed 到手价 skew low vs. mainland; on the test run only 1 of 40 海尔冰箱 cards fell in the 3000–5000 band with 400L+. Widen the price band or note the region bias when a mainland-priced answer is expected. `new_tab`/`js` on `search.suning.com` and `review.suning.com` are NOT blocked from the HK IP (both loaded fine). **实测：港区定价偏低。**
- `http_get` (local mainland IP) was not needed here since the cloud path is unblocked; if the HK IP ever gets throttled, the same `search.suning.com/<kw>/` HTML is fetchable locally and the `.item-bg` selectors apply to the raw HTML too.
```
```
