Field-tested on 2026-07-04

image.baidu.com (百度图片) — search images by keyword and extract per-image title / source site / real source-page URL / real image URL. Use the免登录 `acjson` JSON API via an **in-page fetch from the cloud browser** (carries session cookies + same-origin referer). Do NOT DOM-scrape: the results page is a React waterfall with hashed classnames and no stable selectors.

## Do this first (best path)

1. Open any image.baidu.com page once (homepage or a search URL) so the cloud session gets Baidu cookies.
2. In-page `fetch` the `acjson` API with `Referer: https://image.baidu.com/`.
3. Read `data[]`; for each item with a `thumbURL`, pull fields — and use `replaceUrl[0]` for the DECODED source-page and image URLs (avoids Baidu's objURL cipher).

```python
import urllib.parse, json

# 1) any image.baidu.com page for cookies (a search URL also works)
new_tab("https://image.baidu.com/"); wait_for_load(15); wait(2)

kw = "布偶猫美容"
# pn = offset, rn = count (30 per page is safe). Bump pn by rn for more.
api = ("https://image.baidu.com/search/acjson?tn=resultjson_com&ipn=rj&word="
       + urllib.parse.quote(kw) + "&pn=0&rn=30")

# 2) in-page fetch = cloud IP + same-origin cookies/referer (this is what makes it work)
raw = js("(async()=>{const r=await fetch(%r,{headers:{'Referer':'https://image.baidu.com/'}});return await r.text();})()" % api)
data = json.loads(raw)

# 3) extract
rows = []
for it in data.get("data", []):
    if not it.get("thumbURL"):   # trailing entries in data[] are empty {} — skip them
        continue
    ru = (it.get("replaceUrl") or [{}])[0]
    rows.append({
        "title":      it.get("fromPageTitleEnc") or it.get("fromPageTitle"),  # image/source title
        "source":     it.get("fromURLHost"),          # source site domain, e.g. www.douyin.com
        "sourcePage": ru.get("FromURL") or it.get("fromURL"),  # DECODED real source page URL
        "imageURL":   ru.get("ObjURL") or it.get("middleURL"), # DECODED real full image URL
        "thumb":      it.get("thumbURL"),             # baidu-hosted thumbnail (always usable)
        "wh":         (it.get("width"), it.get("height")),
    })
for r in rows[:6]:
    print(json.dumps(r, ensure_ascii=False))
```

Verified output (kw="布偶猫美容", 2026-07-04) — first entries:
- title "盛世美颜布偶猫.#布偶猫 … - 抖音", source www.douyin.com, sourcePage http://www.douyin.com/note/7481228376317955355
- title "布偶猫:温柔的陪伴者", source mbd.baidu.com, sourcePage http://mbd.baidu.com/newspage/data/dtlandingsuper?nid=dt_4035618226679018794

## Field reference (acjson item)

- `fromPageTitleEnc` / `fromPageTitle` — the image title / source page title (Chinese). May contain `<strong>` highlight tags on some keywords; strip with a regex if present.
- `fromURLHost` — source website domain (the "来源").
- `replaceUrl[0].FromURL` — **decoded** real source page URL. Prefer this.
- `replaceUrl[0].ObjURL` — **decoded** real full-size image URL. Prefer this.
- `fromURL` / `objURL` (top-level) — same links but Baidu-CIPHER-encoded (`ippr_z2C$q...`). Only fall back to these if `replaceUrl` is missing; they need custom decoding, so avoid.
- `middleURL` / `hoverURL` / `thumbURL` — baidu.com-hosted preview images (JPEG, always reachable, never encoded).
- `di` — image id; `width`/`height` — dimensions.
- `data[]` returns rn results but has a few trailing empty `{}` objects — filter on `thumbURL` truthiness.

## Pagination

Same API, bump `pn` by `rn`: `pn=30&rn=30` for the next page, etc. `word` must be URL-encoded UTF-8 (`urllib.parse.quote`).

## Gotchas

- **Local `http_get` is BLOCKED.** Calling the acjson API via `http_get` (runs from local China IP, no browser cookies) returns `{"antiFlag":1,"message":"Forbid spider access"}` (len ~82). Anti-spider triggers on missing session cookies. You MUST use the in-page `js(fetch(...))` path from a loaded image.baidu.com tab.
- **Referer is required.** The fetch must send `Referer: https://image.baidu.com/`. Same-origin fetch from the page also supplies the cookies automatically — that combination is what passes the anti-spider check.
- **No DOM scraping.** The rendered results page (`/search/index?tn=baiduimage&word=...`) is a React app: image cells use hashed classnames like `img-cell-w6C5O`, `main-img-box-Eu2gv`; there is no `.imgitem`/`window.imgData` (the old 2019-era selectors are gone). `document.body.innerText` only yields UI chrome ("变清晰/下载/…"), not titles+sources. The JSON API is the only reliable extraction path.
- Cookies persist across tabs in the same cloud session, so you can open the homepage once and then fetch many keywords/pages without reopening.
- `sourcePage` URLs are often `http://` (not https) and many point to douyin.com or mbd.baidu.com (Baidu's own aggregator) rather than an original blog — that's just what Baidu indexed, report as-is.
