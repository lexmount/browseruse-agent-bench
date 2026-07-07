# Ctrip (携程 / `ctrip.com`) — Train / 高铁票 Search

Field-tested 2026-07-06. Beijing→Shanghai high-speed rail, PC + m-site.

---

## TL;DR

**Do NOT use the `trains.ctrip.com` PC SPA — its list page is dead weight from a
cloud/overseas IP.** The list route renders only a decorative splash animation
(`.tower/.star/.wave` divs, `body.innerText.length === 52`) and never fires the
train-list XHR. Driving the homepage form gets you the same dead searchlist tab.

**Working path: fetch the m-site SSR page over a mainland IP (`http_get`) and
parse `__NEXT_DATA__`.** Full structured train data (54 trains for BJ→SH) is
embedded in the initial HTML as JSON — train numbers, times, durations, and
full seat-price breakdown including 二等座. No browser, no login, no XHR.

- Prices, times, durations all visible without login.
- `http_get` (local mainland IP) works; `new_tab`/`js` fetch (HK cloud IP) gets
  a 14 KB anti-bot block page for every train API/data path.

---

## Working endpoint (the one to use)

```
https://m.ctrip.com/webapp/train/list
    ?dStation=<出发站/城市, URL-encoded>   # 北京  (also accepts a specific 北京南)
    &aStation=<到达站/城市, URL-encoded>   # 上海
    &dDate=YYYY-MM-DD                       # 2026-07-07
```

Case matters: `dStation`/`aStation`/`dDate` (capital S / capital D). The lowercase
variant `dstation=...&astation=...&ddate=...` returns **HTTP 404**.

Fetch it with `http_get` (mainland IP). Returns ~430 KB of SSR HTML with a
`<script id="__NEXT_DATA__">` JSON blob.

```python
import re, json
u = ("https://m.ctrip.com/webapp/train/list"
     "?dStation=%E5%8C%97%E4%BA%AC&aStation=%E4%B8%8A%E6%B5%B7&dDate=2026-07-07")
body = http_get(u)                     # str, not dict; raises HTTPError on 404
m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', body, re.S)
data = json.loads(m.group(1))
trains = data["props"]["pageProps"]["initialState"]["trainSearchInfo"]["trainInfoList"]
```

`http_get` returns the **body string directly** (not a dict — do not call
`.get()` on it). On non-200 it raises `urllib.error.HTTPError`.

---

## Train record shape (`trainInfoList[i]`)

Relevant fields:

| field | meaning | example |
|-------|---------|---------|
| `trainNumber` | 车次号 | `"G9"` |
| `departureTime` | 出发 HH:MM | `"10:00"` |
| `arrivalTime` | 到达 HH:MM | `"14:35"` |
| `runTime` | 耗时（分钟，int — sort on this） | `275` |
| `duration` | 耗时中文 | `"4时35分"` |
| `departureStationName` / `arrivalStationName` | 具体站 | `"北京南"` / `"上海虹桥"` |
| `startPrice` | 最低票价（起价） | `661` |
| `seatItemInfoList` | 各座席价格数组 | see below |
| `takeDays` | 跨天数 | `0` |

`takeDays` matters for overnight trains — arrival `HH:MM` can be next day.

### 二等座 price

Pull from `seatItemInfoList`; each seat has `seatName` + `seatPrice` (== `price`
== `showSeatPrice`):

```python
def second_class_price(t):
    for s in t.get("seatItemInfoList") or []:
        if "二等" in s.get("seatName", ""):
            return s.get("seatPrice")
    return None
```

Other `seatName`s seen: `一等座`, `商务座`, `无座`. `forecastSeatPriceList` inside
a seat holds student/discount forecast prices — ignore for face value.

---

## Filter + sort recipe (dep-window + shortest duration)

```python
def dep_min(t):
    h, m = t["departureTime"].split(":"); return int(h)*60 + int(m)

win = [t for t in trains if 8*60 <= dep_min(t) < 12*60]   # 08:00–12:00
win.sort(key=lambda t: t["runTime"])                       # shortest first
for t in win[:3]:
    print(t["trainNumber"], t["departureTime"], "->", t["arrivalTime"],
          t["duration"], "二等座", second_class_price(t))
```

Verified result (北京→上海, 2026-07-07, dep 08:00–12:00, top 3 by runTime):
```
G9  10:00 -> 14:35  4时35分  二等座 ¥661
G11 10:03 -> 14:39  4时36分  二等座 ¥661
G7  09:00 -> 13:37  4时37分  二等座 ¥661
```

---

## Traps

- **`trains.ctrip.com/trainbooking/searchlist?...` is a trap on cloud/overseas
  IPs.** The tab loads (no login redirect) but `body.innerText` is 52 chars of
  copyright and the DOM is a `.tower/.star1/.wave` splash animation. Detect by
  `!!document.querySelector('.tower,.star1')`. Do not wait for it — it never
  fills.
- **Browser `fetch` of any train data/API path from the HK cloud IP returns a
  generic 14209-byte HTML block page** (`<title>携程旅行网</title>`, `robots
  noindex,nofollow`). Guessing `/pages/booking/getTrainList`,
  `/tochtrains/TrainSearch/SearchLeftTrainListForPC`, etc. all hit this wall.
  The cloud IP is soft-blocked for trains data — use `http_get` (mainland IP).
- **Param case is load-bearing.** `dStation`/`aStation`/`dDate` work; the
  lowercase `dstation`/`astation`/`ddate` variant 404s.
- **`http_get` returns a `str`**, and raises `HTTPError` on 4xx — wrap in
  try/except if you probe multiple URLs.
- **City vs station.** `dStation=北京` returns trains from all Beijing stations
  (result rows carry `departureStationName` like `北京南`). Pass a city name to
  cover all stations; pass a specific station to narrow.
- **`runTime` (int minutes) is the field to sort on**, not `duration` (a
  Chinese string). `startPrice` == cheapest seat, usually the 二等座 face value
  but confirm via `seatItemInfoList`.

---

## Homepage form (fallback, if you must drive the UI)

`trains.ctrip.com/` homepage form works and holds values, but its 搜索 button
just opens the dead searchlist tab (see trap above), so it does not get you
data. Documented only so you don't chase it:

- Inputs: `.focus-departStation`, `.focus-arriveStation`, `.focus-departDate`
  (value `YYYY-MM-DD`), `.focus-returnDate`.
- Set values via the React-safe native-setter trick (dispatch `input`+`change`).
- "只搜高铁动车" toggle and 搜索 button are plain elements; coordinate-click.
- Net result is still the splash page. Prefer the `http_get` m-site path.

---

## Login

Not required for search/list/prices via the m-site `http_get` path. Booking
(下单/支付) would need login, but reading schedules, durations, and fares does
not.
