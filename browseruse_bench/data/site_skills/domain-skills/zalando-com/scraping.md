Field-tested on 2026-07-04 — Zalando fashion catalog: search + faceted filter/sort on the country site (zalando.de), product tiles extracted from DOM or the embedded GraphQL hydration cache.

## Orientation (read once)
- `zalando.com` is ONLY a country picker (no products). It lists 27 country sites: `zalando.de`, `.co.uk`, `.fr`, `.it`, `.es`, `.nl`, etc. For EUR tasks use **zalando.de**.
- From the Lexmount cloud IP (Hong Kong), zalando.de auto-redirects to **`en.zalando.de`** (English UI) and to `en.` subdomain generally. Prices show in **EUR**. Category slugs are English there.
- **`http_get` (local China IP) TIMES OUT on zalando.de** — Zalando blocks/drops it. All extraction MUST go through the cloud browser (`new_tab` + `js`). This is verified, not assumed.

## Do this first — search + filters + sort via one URL
Zalando encodes color/size as a PATH segment and price/sort as query params. You do NOT need to click the UI. Build the URL directly:

Pattern: `https://en.zalando.de/women/_<color>_size-<SIZE>/?q=<query>&price_from=<n>&price_to=<n>&order=activation_date`

`order=activation_date` = "Newest". `p=<N>` = page (84 tiles/page). The site rewrites `?color=black&size=M` into the `/_black_size-M/` path segment automatically — either form works on input.

```python
# TASK: women's winter coats, size M, black, €50-150, sort newest
url=("https://en.zalando.de/women/_black_size-M/"
     "?q=women%27s+winter+coats&price_from=50&price_to=150&order=activation_date")
new_tab(url); wait_for_load(); wait(5)
print(page_info())  # title confirms facets, e.g. "Black Women's Winter Coats Size M ... ZALANDO"
```
Verified result: title = "Black Women's Winter Coats Size M online | Women | ZALANDO", 535 total items, 84 tiles rendered on page 1, all black, all €50-150, "New" badge on top items confirms newest-first sort.

## Extract listings — DOM tiles (complete: all 84/page)
Each `<article>` tile innerText is: `[badges] BRAND \n NAME - Winter coat - COLOR \n €PRICE ...`. Class names are obfuscated; parse by structure. VERIFIED to return 84 clean rows.

```python
r = js(r"""(() => {
  const BADGE=/^(New|Deal|Sustainable|Sponsored|Bestseller|\d+% EXTRA|-?\d+%|heart_outlined|From|Originally:|Last lowest price:|Regular price:|up to)$/i;
  const out=[];
  for(const art of document.querySelectorAll('article')){
    const link=art.querySelector('a[href*=".html"]'); if(!link) continue;
    const lines=art.innerText.split('\n').map(s=>s.trim()).filter(Boolean);
    const di=lines.findIndex(l=>l.includes(' - '));          // "NAME - Winter coat - color"
    let brand=null; for(let i=di-1;i>=0;i--){ if(!BADGE.test(lines[i])){brand=lines[i];break;} }
    const desc=di>=0?lines[di]:null;
    const price=(art.innerText.match(/€\s?[\d.,]+/)||[])[0]||null;  // first € = current price
    out.push({brand, name:desc?desc.split(' - ')[0]:null,
              color:desc?desc.split(' - ').pop():null, price,
              url:link.href.split('?')[0]});
  }
  return {n:out.length, items:out};
})()""")
print(r['n']); print(r['items'][:5])
```
Sample verified output: `{brand:"Marikoo", name:"STEPPMANTEL SAHNEKATZII XIV-1", color:"black", price:"€129.95", url:".../marikoo-winter-coat-black-m5m21u04a-q11.html"}`

## Extract listings — GraphQL hydration cache (clean numeric prices, but PARTIAL ~24)
An inline script `#re-concurrent-data-hydrate` holds a per-product GraphQL cache with clean fields. It only covers the first-hydrated ~24 tiles (not all 84), so use it as a supplement when you need numeric price / sku / silhouette, not as the sole source.

```python
r = js(r"""(() => {
  const s=document.querySelector('#re-concurrent-data-hydrate');
  const t=s.textContent; const obj=JSON.parse(t.slice(t.indexOf('(')+1,t.length-1));
  const cache=obj.graphqlCache||{}; const items=[];
  for(const v of Object.values(cache)){
    const p=v&&v.data&&v.data.product; if(!p||!p.sku) continue;
    items.push({sku:p.sku, brand:p.brand&&p.brand.name, name:p.name, silhouette:p.silhouette,
      price:p.displayPrice&&p.displayPrice.trackingCurrentAmount,        // numeric, e.g. 129.95
      currency:p.displayPrice&&p.displayPrice.original&&p.displayPrice.original.currency}); // "EUR"
  }
  return {n:items.length, items};
})()""")
print(r['n'], r['items'][:3])
```
Verified: `{sku:"M5M21U04A-Q11", brand:"Marikoo", name:"...- Winter coat - black", silhouette:"COAT", price:129.95, currency:"EUR"}`.

## Pagination
84 tiles per page. Add `&p=2`, `&p=3`… to the same URL for more (535 total in the sample task = ~7 pages). Pagination hrefs are also present as `a[href*="&p="]`.

## Search box (if you must type instead of building a URL)
Input is `#header-search-input` (name=`q`) on the home/any page. Submitting via Enter navigates to `en.zalando.de/catalogue/` then redirects to `en.zalando.de/women/?q=<query>` (auto-scoped to a target group). Direct URL `…/catalogue/?q=<query>` also redirects to the scoped results page. Building the URL directly (above) is more reliable than the JS-driven search box, which can drop the typed value on Enter.

## Gotchas
- **zalando.com has no products** — always jump to a country site (`en.zalando.de` from cloud IP).
- **Local `http_get` is dead here** (China IP → connection times out on zalando.de). Cloud `new_tab`+`js` is the only working path. Do not waste a retry on http_get.
- **Cloud IP = Hong Kong → English `en.` subdomain + EUR.** If a task expects the German UI or a non-EUR currency, note the geo skew; results/currency reflect the cloud egress region.
- **Obfuscated class names** (`z5x6ht`, `JT3_zV`, …) — never select by class; parse `<article>` innerText / `a[href*=".html"]` structure as above.
- **First `€` value in a tile = current selling price.** Discounted tiles also list "Originally:", "Last lowest price:", "Regular price:" — filter those out (BADGE regex handles it). "From €X" appears when the price varies by size.
- **Hydration cache is partial (~24 of 84).** For a complete page use the DOM extractor; the GraphQL cache is a clean-price supplement only.
- Color/size go in the URL PATH (`/_black_size-M/`); price/sort stay as query params (`price_from`, `price_to`, `order=activation_date`). Both `?color=&size=` query form and the path form are accepted on input and normalize to the path form.
