Field-tested on 2026-07-04

top.baidu.com is Baidu's hot-search rankings site (百度热搜). There is a clean, no-login JSON API that returns the full ranked list — no scraping needed. Both the JSON API (local http_get) and DOM extraction (cloud browser) were verified to return identical top-10 titles.

## Do this first — JSON API via local http_get (no login, no cloud IP needed)

The board API works from the local machine IP (China mainland). Use the default (or `platform=pc`) variant: it returns a flat `content[]` array with rich fields, which is the easiest to parse.

```python
import json
r = http_get("https://top.baidu.com/api/board?tab=realtime")   # default == pc layout
d = json.loads(r)
items = d["data"]["cards"][0]["content"]        # flat list, ~50 entries, already rank-ordered
top10 = [it["word"] for it in items[:10]]
for i, t in enumerate(top10, 1):
    print(i, t)
```

Verified output (2026-07-04): 受贿超22亿元 杨有林被判死刑 / 广西六蓝水库 / 多部门部署开展防汛工作 / ...

Each item also carries: `desc`, `hotScore`, `hotChange`, `hotTag` (e.g. 热/新/沸), `url` (m.baidu.com search link), `img`, `index`.

### Other tabs (same API, change `tab=`)
- `tab=realtime` — 实时热点 (default hot search)
- `tab=novel` — 小说榜, `tab=movie` — 电影榜, `tab=teleplay` — 电视剧, `tab=car` — 汽车, `tab=game` — 游戏

## Platform variants (important structural difference)

`http_get` (returns a **str**, call `json.loads` on it — it is NOT a requests object, no `.get()/.status`):

- `?tab=realtime` or `?platform=pc&tab=realtime` → `data.cards[0].content[]` is a **flat** array of ~50 items with rich fields. **Excludes the pinned top item** (置顶, e.g. a 习近平 headline), so item[0] is the highest *ranked* entry.
- `?platform=wise&tab=realtime` → nested one level deeper: `data.cards[0].content[0].content[]` (51 items), and **includes** the pinned `isTop:true` item as element 0. Fields are leaner (`word`, `url`, `index`, `hotTag`).

Pick pc/default for richness; pick wise if you specifically need the pinned headline included.

## Fallback — cloud browser DOM extraction

If the API is ever blocked, load the board page in the cloud browser and read the title spans. Verified to match the API output exactly.

```python
new_tab("https://top.baidu.com/board?tab=realtime"); wait_for_load(); wait(3)
titles = js("""
(() => Array.from(document.querySelectorAll('.c-single-text-ellipsis'))
        .slice(0,10).map(e => e.textContent.trim()))()
""")
print(titles)
```

`.c-single-text-ellipsis` = the clean title text nodes (no rank/hot-tag suffix). Alternative selector `.title_dIF3B` also works but appends the hot-tag ("热"/"新") to the text — prefer `.c-single-text-ellipsis`. Note the hashed class suffix (`_dIF3B`) may change across deploys; `.c-single-text-ellipsis` is a stable Baidu utility class and is the safer bet.

## Gotchas

- `http_get` returns a **plain string**, not a response object. Do `json.loads(r)` — do NOT call `r.get(...)` / `r.status` (raises AttributeError).
- Do NOT assume `cards[0].content[]` items have a `word` key on the wise variant — there the real list is one level deeper at `content[0].content[]`. The pc/default variant IS flat. Wrong nesting → `KeyError: 'word'`.
- No SSR JSON is embedded in the HTML (`"word"` not found in page source) — the browser fetches the same `/api/board` endpoint client-side, so the API is the source of truth.
- No login, no captcha, no cookie/token required for the board API. Worked first try from the local (mainland) IP; the cloud (HK) browser also loads the page fine. No anti-bot encountered.
- Cloud region note: this is a China site and behaves the same from local mainland IP and the HK cloud browser — no geo-skew observed. Prefer local http_get (fastest, one call).
