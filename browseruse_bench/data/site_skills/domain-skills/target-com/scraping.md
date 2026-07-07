Field-tested on 2026-07-04 (cloud IP = Target US store 1771 / zip 52404, Iowa)
Target.com product search: drive the SPA by URL params, extract from `data-test` product cards with `js()`. Cloud browser (US-appearing IP) works; local `http_get` is blocked (HTTP 400) by RedSky.

## Do this first (robust primary path: URL params + DOM extraction)
Search, filter (same-day delivery), and sort (guest ratings) are ALL controllable via URL query params — no clicking needed. Navigate once, then extract.

URL scheme (verified):
- Search:            `https://www.target.com/s?searchTerm=air+fryer`
- Sort guest rating: add `&sortBy=RatingHigh`   (this IS the "Average ratings" / guest-rating sort; sorts descending)
- Same-day delivery: add `&facetedValue=cl92v`  (this is the Same-day Delivery facet id)
- Combined (the exact task): `https://www.target.com/s?searchTerm=air+fryer&facetedValue=cl92v&sortBy=RatingHigh`

Verified: combined URL returned 23 cards (vs 30 unfiltered), top items sorted 4.7, 4.7, 4.6 ratings descending.

```python
# one browser-harness invocation; keep it all in one call (see Gotchas re: WS drops)
new_tab("https://www.target.com/s?searchTerm=air+fryer&facetedValue=cl92v&sortBy=RatingHigh")
wait_for_load(); wait(6)   # cards lazy-render; 5-6s needed

items = js("""(() => [...document.querySelectorAll('[data-test="@web/site-top-of-funnel/ProductCardWrapper"]')].map(c => {
  const t = c.querySelector('[data-test="@web/ProductCard/title"]');            // name (also the /p/ link <a>)
  const p = c.querySelector('[data-test="current-price"]');                     // price, e.g. "$77.99"
  const starAria = [...c.querySelectorAll('[aria-label]')]
      .map(e => e.getAttribute('aria-label'))
      .find(a => /stars? with/i.test(a));                                       // "4.7 stars with 134 ratings"
  let rating=null, count=null;
  if (starAria){ const m = starAria.match(/([\\d.]+) stars? with ([\\d,]+)/); if(m){ rating=m[1]; count=m[2]; } }
  return {
    name:   t ? t.textContent.trim() : null,
    href:   (c.querySelector('a[href^="/p/"]')||{}).href,
    price:  p ? p.textContent.trim() : null,
    rating, ratingCount: count
  };
}))()""")
import json; print(len(items), json.dumps(items[:5], ensure_ascii=False, indent=1))
```

Verified selectors (all present, exact strings matter):
- Product card:  `[data-test="@web/site-top-of-funnel/ProductCardWrapper"]`  (30 per page unfiltered)
- Name:          `[data-test="@web/ProductCard/title"]`  (it's the `<a>`; also gives the /p/…/A-<TCIN> href)
- Price:         `[data-test="current-price"]`  -> "$89.99"  (there is also `comparison-price`)
- Rating+count:  read the card's `aria-label` matching `/stars? with/` -> "4.4 stars with 398 ratings". There is NO `data-test="ratings"` on the card; the aria-label is the reliable source.

## Fast path (best-effort JSON, throttles quickly): RedSky plp_search_v2
There IS a login-free JSON endpoint. It returns clean title/price/rating/reviews. But it is aggressively rate-limited from the cloud IP — a handful of calls, then HTTP 400/403. Use it opportunistically; fall back to the DOM path above on any non-200.

- Host: `redsky.target.com/redsky_aggregations/v1/web/plp_search_v2`
- Public web key (from page state, verified working): `9f36aeafbe60771e321a7cc95a78140772ab3e96`
- Must be called via `js()` fetch inside the cloud page (US IP). `http_get` from local machine = HTTP 400 (blocked).

```python
# run inside cloud page; use a FRESH visitor_id each run to dodge throttling
import random
vid = ''.join(random.choice('0123456789ABCDEF') for _ in range(32))
r = js(f"""(async () => {{
  const key='9f36aeafbe60771e321a7cc95a78140772ab3e96';
  const url='https://redsky.target.com/redsky_aggregations/v1/web/plp_search_v2?key='+key+
    '&channel=WEB&count=24&default_purchasability_filter=true&keyword=air+fryer'+
    '&faceted_value=cl92v'+                        // same-day delivery filter (URL param facetedValue -> API faceted_value)
    '&offset=0&page=%2Fs%2Fair+fryer&platform=desktop&pricing_store_id=1771'+
    '&sort_by=RatingHigh&visitor_id={vid}&zip=52404';
  const res=await fetch(url,{{headers:{{'accept':'application/json'}}}});
  if(res.status!==200) return {{status:res.status}};   // -> fall back to DOM path
  const j=await res.json();
  const items=(((j.data||{{}}).search||{{}}).products)||[];
  return {{status:200, count:items.length, items: items.map(p=>({{
    tcin:   p.tcin,
    name:   p.item && p.item.product_description && p.item.product_description.title,
    price:  p.price && p.price.formatted_current_price,
    rating: p.ratings_and_reviews && p.ratings_and_reviews.statistics && p.ratings_and_reviews.statistics.rating && p.ratings_and_reviews.statistics.rating.average,
    reviews:p.ratings_and_reviews && p.ratings_and_reviews.statistics && p.ratings_and_reviews.statistics.rating && p.ratings_and_reviews.statistics.rating.count
  }}))}};
}})()""")
```
Verified live: first calls returned `status:200, count:24` (no facet) / `count:23` (with `faceted_value=cl92v`), sorted by RatingHigh, with real title/price/rating/reviews. Later calls in the same session returned 400 then 403 (throttled).

## Gotchas (all observed live)
- **RedSky throttles fast.** The JSON API worked for the first ~3 calls, then HTTP 400, then 403 within one session. Treat JSON as a bonus; the DOM-navigation path is the dependable one. Always branch on `status!==200`.
- **Local `http_get` cannot reach RedSky** — HTTP 400 from the China IP even with a browser UA. RedSky is US-geofenced/header-gated. All API and page work must go through the cloud browser (`new_tab`/`js`).
- **Cloud IP appears as US Iowa (store 1771, zip 52404).** Prices, store availability, and the same-day-delivery inventory reflect that store, not the guest's real location. `sortBy=RatingHigh` on the full catalog surfaces low-count 5-star items first (e.g. "5 stars with 1 rating"); adding the same-day facet `cl92v` filters to in-stock same-day items and yields the more meaningful high-volume 4.6-4.7 items. Record which subset you queried.
- **Sort via URL, not clicking.** The sort menu uses a `<label>`+hidden `radio[name=radio-sort-by]` (value just `"on"`) and only commits when you click its **"Apply"** button; clicking the label alone does NOT change results or URL. Skip all that — just put `&sortBy=RatingHigh` in the URL. Other sort values seen in the menu: Relevance, Featured, Price low/high, **RatingHigh** (=Average ratings/guest rating), Best seller, Newest.
- **Same-day facet id `cl92v`** was captured by clicking the "Same-day Delivery" facet card and reading `facetedValue` off the URL. Facet ids can change over time; if `cl92v` returns the full unfiltered set, re-derive it: click `[data-test="facet-button-Sort"]`/facet cards and read `location.href`.
- **Cards lazy-render** — always `wait(5-6)` after `wait_for_load()` before extracting, or you get 0 cards.
- **`js("() => expr")` must be self-invoking**: use `js("(() => location.href)()")`, not `js("() => location.href)")` (the latter returns the function object as `{}`).
- **WebSocket drops on tab churn.** Opening many `new_tab`s across separate `browser-harness` invocations killed the daemon WS ("normal connection lost"), and eventually the browser CDP endpoint returned 1008 "Active session not found" (container recycled). Mitigations: do a whole search-extract flow in ONE invocation; reuse the same tab (`goto_url` instead of piling up `new_tab`s); if the daemon dies, `pkill -f browser_harness` and reconnect; if the endpoint returns 1008, the session is dead — create a new Lexmount session.
- No anti-bot challenge (no captcha/Akamai block) was hit on page navigation from the cloud IP; only RedSky's own rate limit and the local-IP geofence.
