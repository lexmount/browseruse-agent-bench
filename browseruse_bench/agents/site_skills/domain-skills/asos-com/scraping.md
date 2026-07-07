Field-tested on 2026-07-04 (asos.com). ASOS has a clean, login-free product-search JSON API; use it via `js()` fetch inside the cloud browser — never via local http_get (China IP is 403-blocked).

## Do this first — the JSON API (best path, verified)

Endpoint (returns full product list + prices + facets, no auth, no cookies):
```
https://www.asos.com/api/product/search/v2/?q=<query>&store=COM&lang=en-GB&currency=GBP&country=GB&limit=72&offset=0
```
Optional refinements (all verified working, append as query params):
- `&size=117`         → UK 8. (Size facet: numeric UK sizes map to ids, e.g. UK 8 = `117`, UK 6-8 = `17097`, UK 8-10 = `18167`.)
- `&pricerange=0-40`  → price band in £ (see gotcha: it leaks, always re-filter client-side).
- `&sort=freshness` | `priceasc` | `pricedesc`  → verified. **There is NO explicit "best selling" sort; the DEFAULT (omit `sort`) IS the Recommended/best-selling order** the UI shows. So for "sort by best selling" just send no `sort` param.
- Colour facet `base_colour` (White=5, Multi=17, Black=4, Blue=3, Green=2 …) if a colour is asked.

**"Floral print" is NOT a facet** — ASOS dresses have no print/pattern facet. Put it in the text query: `q=floral summer dresses`. Colour field on each product often reads "…FLORAL" / "…PATTERN", useful to confirm.

Run this inside the cloud browser (must be on an asos.com tab first so fetch is same-origin):
```python
new_tab("https://www.asos.com/search/?q=summer+dresses"); wait_for_load(20); wait(2)
r = js("""
(async function(){
  var url = "https://www.asos.com/api/product/search/v2/?q=floral%20summer%20dresses"
          + "&store=COM&lang=en-GB&currency=GBP&country=GB&limit=72&offset=0"
          + "&size=117&pricerange=0-40";   // UK8, under £40. No sort = best-selling.
  var j = await (await fetch(url,{headers:{'accept':'application/json'}})).json();
  var items = j.products
    .filter(function(p){ return p.price.current.value <= 40; })   // MUST re-filter, see gotcha
    .map(function(p){ return {
      id:p.id, name:p.name, brand:p.brandName, colour:p.colour,
      price:p.price.current.value, priceText:p.price.current.text,
      url:"https://www.asos.com/prd/"+p.id
    };});
  return {totalMatches:j.itemCount, kept:items.length, items:items};
})()
""")
import json; print(json.dumps(r, indent=1, ensure_ascii=False))
```
Verified output shape: `{"searchTerm","itemCount","products":[{"id","name","brandName","colour","price":{"current":{"value":28.0,"text":"£28.00"}},"colourWayId",...}], "facets":[...]}`. `price.current.value` is a number (£), `price.current.text` is "£28.00". Product URL: `https://www.asos.com/prd/<id>` (301-redirects to the full slug; the numeric id is the stable key).

Pagination: increase `&offset=` by `limit` (72). `itemCount` is the total (e.g. floral summer dresses + UK8 + £0-40 ≈ 554). Keep each fetch to ONE page per `js()` call — an 8-page loop in a single call times out the ~30s js await window.

## DOM fallback (verified, use only if API shape changes)

Search UI URL pattern: `https://www.asos.com/search/?q=summer+dresses&size=117&pricerange=0-40` (same refinement params as the API). Tiles are `<li id="product-<id>">` with an `<a href=".../prd/<id>">`; name+price live in the anchor's `aria-label`.
```python
goto_url("https://www.asos.com/search/?q=floral+summer+dresses&size=117&pricerange=0-40"); wait_for_load(20); wait(3)
r = js("""
(function(){
  return Array.from(document.querySelectorAll('li[id^="product-"] a[href*="/prd/"]')).map(function(a){
    var lbl = a.getAttribute('aria-label')||'';
    // two label shapes: "<name>, Price £X"  OR  "<name>, Original price £X current price £Y, Discount: -n%"
    var cur = lbl.match(/current price\\s*(£[\\d.,]+)/i) || lbl.match(/Price\\s*(£[\\d.,]+)/i);
    var name = lbl.split(/,\\s*(?:Original price|Price)/)[0];
    return {id:(a.href.match(/prd\\/(\\d+)/)||[])[1], name:name, priceText:cur?cur[1]:null, url:a.href.split('#')[0]};
  });
})()
""")
```
DOM caveat: the UI lazy-loads only ~72 tiles per scroll and the `aria-label` price format varies between full-price ("…, Price £X") and discounted ("…, Original price £X current price £Y, Discount") items — the regex above handles both. The API path avoids all of this.

## Gotchas (all observed live)

- **`pricerange` is a SOFT filter — it leaks.** At `limit=20` results were strictly ≤£40, but at `limit=72/100/200` the API padded the tail with items up to £199. ALWAYS re-filter `p.price.current.value <= 40` client-side (done in the code above). Same leak appears in the DOM UI (a £46 item showed on a `pricerange=0-40` page).
- **Local `http_get` is 403 (Forbidden) from the China IP** — ASOS/Akamai geo-blocks it. The API is reachable ONLY from the cloud browser via `js()` fetch (Hong Kong exit IP passes). Do not attempt http_get for asos.com.
- **Cloud exit IP is Hong Kong but store=COM/currency=GBP/country=GB in the URL forces the UK catalogue and £ prices** — verified prices come back in GBP regardless of exit IP, so no HK/region skew as long as you keep those params.
- **No "best selling" sort key exists.** The task's "sort by best selling" = ASOS default order (omit `sort`). `sort=freshness|priceasc|pricedesc` are the other verified values.
- **Size "8" means UK 8 = facet id `117`** (2600+ matches). Don't confuse with S/M/L alpha sizes (S=278, M=277, L=276) — the size facet mixes numeric UK, alpha, and bra sizes.
- Multi-fetch loops inside one `js()` call time out (~30s). One page (one fetch) per call; paginate across separate calls.
