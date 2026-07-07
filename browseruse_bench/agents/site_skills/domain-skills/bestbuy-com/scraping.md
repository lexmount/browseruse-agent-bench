Field-tested on 2026-07-04

bestbuy.com — US electronics retailer. Search + faceted filtering + sort all drive off ONE URL (`/site/searchpage.jsp`); build the URL directly and extract product cards from the rendered DOM. No login needed.

## Cloud-IP gotcha (READ FIRST)
The Lexmount cloud egress is Hong Kong, so a bare `new_tab("https://www.bestbuy.com")` lands on the **international country-selector splash** (title contains "Best Buy International: Select your Country"), NOT the store. Bypass it by appending `?intl=nosplash` (or clicking "United States"). Once you hit any `/site/...` URL with `intl=nosplash`, a cookie is set and later navigations stay on the US store even without the param. Prices/inventory shown are the real US store (USD), unaffected by the HK IP.

## Do this first — search + filter + sort in ONE navigation
Everything (query, brand facet, screen-size facet, sort) is expressible in the URL. Build it and `goto_url`. Verified working:

```python
import urllib.parse as u
# Facet syntax: KEY=Label~Value, multiple facets joined by '^', whole thing goes in qp=
qp = ('brand_facet=Brand~Samsung'
      '^brand_facet=Brand~LG'
      '^parent_tvscreensizeplus_facet=TV Screen Size~55" - 64"'
      '^parent_tvscreensizeplus_facet=TV Screen Size~65" - 74"')
url = ('https://www.bestbuy.com/site/searchpage.jsp?id=pcat17071'
       '&st=' + u.quote('4K TV') +
       '&sp=Customer-Rating' +            # sort; see options below
       '&qp=' + u.quote(qp) +
       '&intl=nosplash')
goto_url(url); wait_for_load(); wait(6)
```

### Verified facet keys / values (TV category)
- Brand: `brand_facet=Brand~Samsung`, `brand_facet=Brand~LG` (value = brand name as shown).
- Screen size: `parent_tvscreensizeplus_facet=TV Screen Size~55" - 64"` and `...~65" - 74"`.
  Available buckets (exact label text): `33" - 44"`, `45" - 54"`, `55" - 64"`, `65" - 74"`, `75" - 84"`, `85" - 94"`, `95" or More`. (Also `32" and Under`.)
  WARNING: there is a decoy key `currentscreensize_facet=Screen Size~...` — it stays in the URL but does NOT actually filter. Use `parent_tvscreensizeplus_facet` / `TV Screen Size` for TVs.
- Category (plain `<a>` links on page): `category_facet=All Flat-Screen TVs~abcat0101001`, etc.
- Multiple values of the SAME facet = OR (both brands shown). Different facets = AND.
- Separator between facets is `^` (encodes to `%5E`).

### Sort param `sp=` (verified options — Best Buy has NO explicit "review count" sort)
- `Best-Match` (default, omit `sp`)
- `Customer-Rating`  ← closest to "sort by reviews": rating high→low, ties broken by review count desc. Use this for review-ranked tasks.
- `Price-Low-To-High`, `Price-High-To-Low`

## Extract product cards (verified extractor)
Cards are `.product-list-item`. Content is lazy-rendered per viewport, so SCROLL first, then extract. Title/rating/reviews/price come from `innerText`; the stable product id comes from the `/product/.../<PRODUCTID>` anchor (the new UI uses alphanumeric product ids like `JJ8VPZR3P3`, not always a numeric `/sku/`).

```python
# 1) force lazy render
for y in range(0, 9000, 900):
    js(f"window.scrollTo(0,{y})"); wait(0.6)
js("window.scrollTo(0,0)"); wait(2)

# 2) extract
import json
data = js(r"""
(function(){
  var items=document.querySelectorAll('.product-list-item'); var out=[];
  items.forEach(function(el){
    var txt=el.innerText;
    var a=el.querySelector('a[href*="/product/"]'); var pid=null;
    if(a){var m=a.getAttribute('href').match(/\/product\/[^\/]+\/([A-Z0-9]+)/); if(m)pid=m[1];}
    var title=null;
    txt.split('\n').forEach(function(s){s=s.trim(); if(!title && s.length>18 && /- \d+"|Class/i.test(s)) title=s;});
    var rm=txt.match(/Rating ([\d.]+) out of 5 stars with ([\d,]+) reviews/);
    var pm=txt.match(/\$[\d,]+\.\d{2}/);   // first $ price = current/deal price
    if(title) out.push({title:title, productId:pid,
      rating: rm?parseFloat(rm[1]):null,
      reviews: rm?parseInt(rm[2].replace(/,/g,'')):null,
      price: pm?pm[0]:null});
  });
  return out;
})()
""")
print(json.dumps(data, ensure_ascii=False, indent=1))
```

Sample verified output (4K TV, Samsung+LG, 55-64/65-74, Customer-Rating), top rows:
`LG 65" C3 OLED — JJ8VPZR3P3 — 4.8★ — 2444 reviews — $1,727.61`
`LG 65" C4 OLED — JJ8VPZQF6G — 4.8★ — 1563 reviews — $1,296.42`
`Samsung 65" S90D OLED — J3ZYG2HRF7 — 4.8★ — 1545 reviews — $1,043.00`
All returned rows were correctly Samsung/LG and within 55–65", proving the facet keys filter for real.

## Gotchas
- **Splash page**: without `intl=nosplash`, `page_info().title` starts with "Best Buy International: Select your Country". Always include the param on the first hit.
- **Lazy rendering**: right after navigation only ~1–2 cards have full content/anchors; `.product-list-item` count (~25) is correct but most are skeletons. Scroll the whole page (loop above) before extracting or you get 1 row. Even after scrolling expect to reliably get ~18 of ~25 (a few cards flip back to skeleton when out of viewport). Re-scroll if you need every last one.
- **Class names churn**: `.sku-title`/`h2` selectors from the old UI are GONE — extract title from `innerText` line matching `- NN"` / `Class`. `.product-list-item` and the `/product/` anchor were stable.
- **Facet checkbox clicking is unreliable via `label.click()`**: it toggles the box but the SPA doesn't always commit it to the URL/results. Building the URL directly (above) is far more reliable than clicking facets.
- **No `sp=review count`**: Best Buy only sorts by Best Match / Customer Rating / Price. Use `Customer-Rating` for review-ranked tasks (it secondary-sorts by review volume).
- **No clean免登录 JSON search API observed** from the page; results are rendered into the React app. DOM extraction is the path. (Product ids like `JJ8VPZR3P3` can build a canonical URL `https://www.bestbuy.com/product/x/<PRODUCTID>` for the detail page.)
- **http_get from local (China) IP** was not needed here — the cloud browser reaches the US store fine once past the splash. If the cloud path ever gets challenged, local `http_get` is the fallback, but note it will NOT carry the `intl=nosplash` cookie.
