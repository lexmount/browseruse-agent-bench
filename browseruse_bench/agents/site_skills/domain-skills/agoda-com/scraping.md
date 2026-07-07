Field-tested on 2026-07-04 (re-verified 2026-07-06) — Agoda hotel search: results render as DOM cards (no usable JSON API); drive it with a direct `/search` URL + `hotelStarRating` filter and extract per-card via `data-selenium` attributes.

## Do this first (verified working path)

Agoda has NO免登录 JSON endpoint you can hit — hotel/price data is loaded via XHR/GraphQL and rendered straight into the DOM (no `__NEXT_DATA__`, no inline JSON blob containing hotel data). The reliable path is: open the `/search` results URL through the cloud browser and read the rendered cards with `js()`.

Search URL pattern (all params confirmed working):
```
https://www.agoda.com/search?city=<CITY_ID>&checkIn=YYYY-MM-DD&los=<NIGHTS>&rooms=1&adults=2&sort=Ranking&hotelStarRating=<N>
```
- `city=9395` → **Bangkok** (verified). City IDs are Agoda-internal; get one by searching the site UI once, or reuse known ones.
- `checkIn=2026-08-01` → check-in date. `los=1` → length of stay = 1 night (checkout = check-in + los).
- `hotelStarRating=5` → **5-star filter** (verified: filter panel then shows "5-Star rating" active, every card shows "5 stars out of 5").
- `sort=Ranking` → Agoda's default ranking order. Card #0 is the top-ranked result.
- `adults=2&rooms=1` → default occupancy.

### Full runnable extraction (Bangkok, 2026-08-01, 1 night, 5-star, top result)
```python
new_tab("https://www.agoda.com/search?city=9395&checkIn=2026-08-01&los=1&rooms=1&adults=2&sort=Ranking&hotelStarRating=5")
wait_for_load(20); wait(6)          # cards lazy-render; give it time
js("window.scrollTo(0,400)"); wait(2)   # nudge scroll to force first cards to paint

import json
info = js("""(function(){
  var cards = document.querySelectorAll('[data-selenium="hotel-item"]');
  var res = [];
  for (var i=0;i<Math.min(cards.length,5);i++){
    var c = cards[i];
    res.push({
      rank: i+1,
      hotelid: c.getAttribute('data-hotelid'),
      name: (c.querySelector('[data-selenium="hotel-name"]')||{}).innerText,
      price: (c.querySelector('[data-selenium="display-price"]')||{}).innerText  // number only, no currency symbol
    });
  }
  return {count: cards.length, currency: (document.body.innerText.match(/\\b(USD|HKD|THB)\\b/)||[])[0], cards: res};
})()""")
print(json.dumps(info, ensure_ascii=False))
```

Verified output (2026-07-04): top 5-star result = **The Peninsula Bangkok**, `display-price` = **1,934**, currency = **HKD**. (#2 The Standard Bangkok Mahanakhon 1,156; #3 Grande Centre Point Surawong 734.)

Re-verified (2026-07-06, same URL/selectors, checkIn=2026-08-01): path worked unchanged — 11 cards, currency HKD, `data-selenium` selectors all resolved. Top 5-star result rotated to **Siam Kempinski Hotel Bangkok**, `display-price` = **3,097** HKD (5-star confirmed via card text "5 stars out of 5"). (#2 Akara Hotel Bangkok 457; #3 Eastin Grand Hotel Phayathai 1,078.) The specific top hotel/price changes with dates & inventory — trust the method, not the frozen example.

## Verified selectors (per card)
Each result card is `[data-selenium="hotel-item"]` (also carries `data-hotelid` and matches `li[data-hotelid]`). Inside a card:
- `[data-selenium="hotel-name"]` → hotel name. ✅
- `[data-selenium="display-price"]` → price number, **no currency symbol** (e.g. `1,934`). ✅
- Star rating is NOT in a clean numeric attr — the card's full text contains `"5 stars out of 5"`; simplest is to trust the `hotelStarRating` URL filter rather than parse stars per card.

11 cards render into the DOM per page even though the page reports "49 properties found" (pagination: `Page 1 of 5`). The top few are enough for "rank #1".

## Gotchas
- **Currency = HKD, not THB/USD.** The cloud出口IP is Hong Kong, so Agoda serves Hong Kong locale: all prices are in **HKD**, and `display-price` gives the number only. `&currencyCode=USD` in the URL does **NOT** override it (tested — still HKD). The header currency selector is client-side only. If you need THB/USD you'd have to change currency in the UI + cookie; for "record the price" tasks, report the HKD value and note the currency explicitly. Footer confirms "Agoda International (Hong Kong) Limited".
- **`display-price` is the headline/original price.** For The Peninsula the card also shows a lower cashback price (HKD 1,908) and a "-13%" discount off HKD 2,193. `display-price` returns `1,934` (the main quoted per-night price before taxes/fees). If a task needs the exact "pay" number, read the card's full `innerText` and pick the intended line — the semantics of strikethrough vs. applied price vary per card.
- **Lazy render / whitespace body.** Right after load, `document.body.innerText` is mostly `\xa0` placeholders. Always `wait(6)` + a small `scrollTo` before extracting, or cards' inner text is empty.
- **No JSON API.** No `__NEXT_DATA__`, no inline hotel-data script, no discovered免登录 REST/GraphQL endpoint returning prices. DOM extraction is the only verified route. (`http_get` from the local China IP was not needed and not relied on here; the cloud-browser DOM path worked cleanly and is the recommended one.)
- **No anti-bot block hit.** Homepage and `/search` both loaded on the cloud (HK) IP with no CAPTCHA/403 during this session. No login required to see results and prices.
- **Sort label wording.** The visible UI text may read "Sort by: Best match" while `sort=Ranking` is in the URL; the ordering is Agoda's ranking and card #0 is the intended top result. Some top cards are marked "Promoted" (paid placement) — Agoda states commission affects order — so "rank #1" is the ranking-sorted top card as the site presents it.
