Field-tested on 2026-07-06 (re-verified 2026-07-06) — skyscanner.com flight search & results extraction. Site is behind PerimeterX (PX) bot protection; the Lexmount Hong Kong cloud IP gets hard-blocked, so read the Gotchas FIRST. Re-verification reproduced every finding exactly: first fresh-session homepage load renders real content; direct /transport/flights/ URL nav trips the PX press-and-hold captcha instantly; the HK egress IP is flagged after 1-2 requests so even a second homepage load returns the captcha; local http_get HTML → 708-byte SPA stub, JSON APIs → HTTP 403.

# Skyscanner — Scraping & Data Extraction

## Bottom line (read before trying)

**Both extraction paths are blocked by PerimeterX at the time of testing:**

- **Cloud browser (Lexmount, Hong Kong egress IP)** → every page (even the
  homepage `/`) redirects to a PX press-and-hold captcha:
  `https://www.skyscanner.com.hk/sttc/px/captcha-v2/index.html?...`.
  In-browser `XHR`/`fetch` to any API returns `403 {"reason":"blocked"}`.
- **Local `http_get` (mainland-China IP)** → HTML pages return only a **708-byte
  SPA stub** (`<div id="root"></div>`, needs JS). Every JSON API returns
  **HTTP 403**.

The very first homepage load on a brand-new isolated cloud session rendered
real content **once**, then PX flagged the egress IP within ~1–2 requests and
every subsequent page (including fresh sessions) got the captcha. Treat the
cloud IP as burned for this host.

So this file documents the **URL patterns and DOM/field-extraction recipe** that
work *when you have an un-flagged session/IP*, plus what was actually verified.

---

## Do this first (when not PX-blocked)

Skyscanner is a client-rendered SPA — there is **no useful server-side HTML and
no open JSON API without a valid PX cookie**. You must drive a real browser.

### URL pattern for a results page (verified format, triggers captcha on cloud IP)
```
https://www.skyscanner.com/transport/flights/{ORIGIN}/{DEST}/{YYMMDD}/?adults=1&cabinclass=economy
# one-way: omit the return-date segment (single /{YYMMDD}/)
# round-trip: /transport/flights/{ORIGIN}/{DEST}/{OUT}/{BACK}/
# {ORIGIN}/{DEST} = lowercase IATA (pek, pvg) or Skyscanner place slug
# {YYMMDD} e.g. next Monday 2026-07-13 -> 260713
```
The cloud browser gets **geo-redirected to `www.skyscanner.com.hk`** (Hong Kong
egress → HKD currency, `en-GB`, `market:::HK`). If you must force a market,
navigate directly to the `.com.hk`/localized host so URLs stay stable.

### Preferred flow (survives PX better than direct URL nav)
Direct navigation to a `/transport/flights/...` URL trips PX **instantly**.
Going through the homepage search box (human-like) is the only flow that ever
rendered content. On the homepage the search widget exposes an `origin` and
`destination` combobox and Depart/Return date pickers (labels: "From",
"To", "Depart", "Return", "Travellers and bags"). Fill origin, pick the
autosuggest option, fill destination, pick a Depart date, submit.

---

## Field extraction (recipe — run in `js()` on a loaded results page)

Results are React-rendered flight cards. When a results page renders, extract
lowest-price flights with this DOM sweep. **Selectors below are the documented
shape to try; they were NOT confirmable live because PX blocked every results
page from the test IP.** Verify/adjust `[class*=...]` prefixes against the live
DOM on first use.

```python
js(r"""(function(){
  // Flight result cards. Skyscanner ships hashed classnames; anchor on
  // stable substrings + role/aria. Try these in order, keep the first that hits.
  var cards = [...document.querySelectorAll('[class*="FlightsResults_dayViewItem"], [class*="TicketStub"], [data-testid*="itinerary" ]')];
  var out = cards.slice(0,20).map(function(c){
    var t = c.innerText || '';
    // price: first $/HK$/￥ number in the card text
    var pm = t.match(/(?:HK\$|US\$|\$|￥|EUR|£)\s?[\d,]+/);
    // times: HH:MM tokens
    var times = (t.match(/\b\d{1,2}:\d{2}\b/g) || []);
    // airline: alt/aria on the carrier logo img
    var img = c.querySelector('img[alt]');
    return {
      airline: img ? img.getAttribute('alt') : null,
      depart: times[0] || null,
      arrive: times[1] || null,
      price: pm ? pm[0] : null,
      raw: t.replace(/\s+/g,' ').slice(0,160)
    };
  }).filter(function(r){return r.price;});
  // lowest price:
  out.sort(function(a,b){
    var pa=+(a.price||'').replace(/[^\d.]/g,''), pb=+(b.price||'').replace(/[^\d.]/g,'');
    return pa-pb;
  });
  return JSON.stringify({count:out.length, cheapest:out[0]||null, all:out}, null, 1);
})()""")
```

Notes on the fields the task needs (airline / time / price of the cheapest
one-way flight): airline comes from the carrier logo `img[alt]`; the two
`HH:MM` tokens are depart/arrive; price is the currency token (**HKD** on the
Hong Kong cloud IP — record the currency, it is not USD). Sort ascending on the
numeric price for "lowest price flight".

---

## Gotchas (all field-observed on 2026-07-06)

- **PerimeterX press-and-hold captcha (`PXrf8vapwA`).** Detected by the
  `px-captcha` element and `client.px-cloud.net` / `js.px-cloud.net` scripts,
  and the redirect to `/sttc/px/captcha-v2/index.html`. Cookies `_px3`,
  `_pxvid`, `_pxhd`, `pxcts` are the PX fingerprint set.
- **CDP press-and-hold does NOT solve it.** Holding the mouse button on the
  captcha box (center ~x=382,y=483 of the 468×102 element) via
  `cdp("Input.dispatchMouseEvent", type="mousePressed"...)` + repeated
  `mouseMoved` for ~11s + `mouseReleased` was **detected as a bot** and the page
  stayed on the captcha. Synthetic mouse events lack the movement/pressure
  entropy PX checks.
- **The Lexmount cloud egress IP is Hong Kong and gets IP-reputation-flagged
  fast.** First fresh-session homepage load rendered real content; within ~1–2
  requests every page (and every *new* isolated session) got the captcha.
  Closing/reopening the session did NOT help — it is IP-level, not
  cookie/session-level.
- **`http_get` (mainland-China local IP) is also PX-blocked:** HTML → 708-byte
  empty SPA stub; APIs (`/g/autosuggest-search/...`, `/g/geo/v3/markets`,
  `/sttc/genesis/config/...`) → **HTTP 403**, even with a realistic
  `User-Agent` + `Referer` + `x-skyscanner-market/locale` headers.
- **In-browser `XHR`/`fetch` from the cloud session is blocked too:** the
  autosuggest endpoint returned `403 {"reason":"blocked","redirect_to":".../captcha-v2/..."}`
  — so there is **no open JSON API** reachable without first clearing PX.
- **Geo/currency skew:** the cloud IP resolves to `www.skyscanner.com.hk`,
  `ssculture=locale:::en-GB&market:::HK&currency:::HKD`. Prices render in **HKD**
  and the market is HK — always record the currency you actually saw; do not
  assume USD.

## What to try if you need this host to work
1. Use an **un-flagged residential/other-region IP** (the whole blocker is IP
   reputation + PX). A clean IP + the homepage-search-box flow is the only
   realistic path.
2. If a session ever renders results, extract immediately in the SAME call
   (PX re-challenges aggressively on subsequent navigations).
3. Do not bother with `http_get` for this host — it never returns data.
