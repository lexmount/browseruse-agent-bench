Field-tested on 2026-07-04 (airbnb.com). Search stays by building a `/s/<Place>/homes` URL with dates+guests+room_type in the query string, then extract listing cards from the rendered page with `js()` — there is NO usable no-auth JSON API and `http_get` cannot see the results.

## Do this first (the whole flow)

Airbnb search results are entirely URL-driven. Construct the URL, open it in the CLOUD browser (`new_tab`), wait, then extract cards with `js()`. No login, no search-box typing, no clicking needed.

URL pattern (verified):
```
https://www.airbnb.com/s/<PLACE>/homes?adults=<N>&checkin=YYYY-MM-DD&checkout=YYYY-MM-DD&room_types[]=<TYPE>
```
- `<PLACE>`: city with spaces/commas → `-` and `--`. `Tokyo, Japan` → `Tokyo--Japan`; `Brooklyn, New York` → `Brooklyn--New-York`. (Both verified returning correct-region results.)
- `adults=` guest count. Add `&children=`, `&infants=` if needed.
- `checkin`/`checkout` are `YYYY-MM-DD`. Nights = checkout − checkin.
- `room_types[]` filter (URL-encoded), verified values:
  - Entire home → `room_types[]=Entire%20home%2Fapt`
  - Private room → `room_types[]=Private%20room`
  - (Shared room → `Shared%20room`; Hotel room → `Hotel%20room` — same scheme, untested here.)
  - Omit the param for no room-type filter.

## Run it (paste as-is, edit the URL)

```python
new_tab("https://www.airbnb.com/s/Tokyo--Japan/homes?adults=2&checkin=2026-08-10&checkout=2026-08-13&room_types[]=Entire%20home%2Fapt")
wait_for_load(20); wait(4)   # cards render client-side; the wait(4) matters

listings = js(r"""(()=>{
  function price(row){
    // charged total = last currency token BEFORE 'for N nights'
    // (discounted cards show original then discounted; 'Pay $0 today' sits AFTER 'for N nights' and must be excluded)
    const before=row.split(/for \d+ nights?/)[0];
    const toks=before.match(/[$€£¥][\d,.]+\s*[A-Z]{0,3}/g)||[];
    return toks.length?toks[toks.length-1].trim():null;
  }
  return [...document.querySelectorAll('[data-testid="card-container"]')].map(c=>{
    const id=c.querySelector('a[href*="/rooms/"]')?.getAttribute('href').match(/rooms\/(\d+)/)?.[1]||null;
    const rm=c.innerText.match(/([0-9]\.[0-9]{1,2})\s*\((\d+)\)/);   // "4.95 (104)"
    const row=c.querySelector('[data-testid="price-availability-row"]')?.innerText||'';
    const nightsM=row.match(/for (\d+) nights?/);
    return {
      id,
      type:  c.querySelector('[data-testid="listing-card-title"]')?.innerText||null,  // "Apartment in Shinjuku" / "Room in Brooklyn"
      name:  c.querySelector('[data-testid="listing-card-name"]')?.innerText||null,   // host's headline
      totalPrice: price(row),          // e.g. "$1,444 HKD" (total for the stay, NOT per night)
      nights: nightsM?+nightsM[1]:null,
      rating: rm?rm[1]:'New',
      reviews: rm?+rm[2]:0,
      url: id?('https://www.airbnb.com/rooms/'+id):null
    };
  });
})()""")
import json; print(json.dumps(listings, indent=1, ensure_ascii=False))
```

Verified output: 18 cards per page, and all 18 had non-null `id`, `totalPrice`, and `url` on both the Tokyo and Brooklyn tasks. `rating`='New' for listings with no reviews yet.

## Field notes on the fields

- **Card selector**: `[data-testid="card-container"]` — 18 per result page, verified on both tasks. `[itemprop="itemListElement"]` also returns 18 (equivalent).
- **id / url**: only reliable id source is the `/rooms/<digits>` href inside the card. Detail page = `https://www.airbnb.com/rooms/<id>`.
- **type** (`listing-card-title`): property type + area, e.g. "Apartment in Tokyo Prefecture", "Room in Brooklyn". The private-room filter is confirmed working because titles become "Room in ..." rather than "Apartment in ...".
- **name** (`listing-card-name`): the host-written headline.
- **rating**: parsed from card innerText as `X.XX (NN)`. Present on all 18 Tokyo cards. Use 'New' fallback for no-review listings.
- **totalPrice**: this is the TOTAL for the whole stay ("for 3 nights"), already includes any discount, currency depends on the browser's geo IP (see gotcha). Divide by `nights` for per-night.

## Gotchas (all observed live)

- **`http_get` cannot get listings — browser path is mandatory.** `http_get` on the `/s/.../homes` URL returns ~889 KB of HTML from the LOCAL IP, but it contains `data-deferred-state` only — ZERO `/rooms/` links, no `card-container`. Results are hydrated client-side. There is no discovered no-auth JSON endpoint. Always use `new_tab` + `js()` (cloud IP). (The string "captcha" appears in that HTML but it is a false alarm — it's the config key `disable_google_recaptcha`, not a block.)
- **Price parsing is the tricky part.** The price row can read `$original | $discounted | Show price breakdown | for 3 nights | Pay $0 today | Free cancellation`. Taking the *last* currency token overall wrongly grabs `$0` (from "Pay $0 today"). The verified rule: split on `for N nights`, take the LAST currency token in the part BEFORE it → that is the charged discounted total. This gave correct prices for all 18/18 cards.
- **Currency is geo-dependent.** The Lexmount cloud browser geolocated to Hong Kong, so prices came back in HKD ("$1,444 HKD"). Do NOT assume USD. Read the currency token as-is, or append `&currency=USD` to the URL if a fixed currency is required (untested but standard Airbnb param).
- **`wait(4)` after `wait_for_load` is needed** — cards populate a beat after load; extracting too early yields 0 cards.
- **Only ~18 results per page.** For more, paginate via the `&cursor=` param (present on next-page links) or scroll; not needed for "record the listings" tasks.
- **Skip cookie/region popups?** None blocked extraction in this session (cloud context was fresh with `--create-context`); cards were reachable directly. If a modal ever covers results, dismiss before extracting.
