Field-tested on 2026-07-04 (re-verified 2026-07-06) — finance.ifeng.com (凤凰网财经): grab the channel top headline + open any article, extracting title / publish-time / source from an embedded JSON blob. **Both the homepage and article pages are servable via plain `http_get` from the local (China) IP — no cloud browser, no login, no reverse-proxy needed.**

## Do this first (offline path — fastest, verified)

`http_get` runs from the local China IP and returns the full HTML **as a string** (not a dict). Every article page embeds a `var allData = {...}` object whose `docData` sub-object holds the clean fields. The homepage lists article links as `https://finance.ifeng.com/c/{base62Id}` in DOM order (top headline first).

```python
import re, json

def _docdata(html):
    """Brace-match the docData object out of the page HTML."""
    i = html.find('"docData":')
    if i < 0: return None
    j = html.find('{', i); depth = 0; k = j
    while k < len(html):
        c = html[k]
        if c == '{': depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0: break
        k += 1
    return json.loads(html[j:k+1])

def article(cid):
    d = _docdata(http_get("https://finance.ifeng.com/c/" + cid))
    return {
        "title":  d["title"],
        "time":   d["newsTime"],      # "2026-07-06 07:57:44", Beijing time
        "source": d.get("source") or "凤凰网财经",  # empty => ifeng original
        "author": d.get("author"),
        "editor": d.get("editorName"),
    }

# --- Task: channel top headline ---
home = http_get("https://finance.ifeng.com/")            # returns str, ~140 KB
links = re.findall(
    r'href="(https://finance\.ifeng\.com/c/([A-Za-z0-9]+))"[^>]*>([^<]{8,60})', home)
top_id = links[0][1]          # first /c/ link == the visual top headline
print(article(top_id))
# -> {'title':'原油价格暴跌后，欧佩克+最新消息','time':'2026-07-06 07:57:44',
#     'source':'凤凰网财经','author':'','editor':'秦沛洁'}
```

Verified live: homepage first link `8uX19tvXhRq` == the largest-font top headline on the rendered page. Reprint example `8uXKnlbnyZx` -> `source="中国证券报"`.

## Cloud-browser path (fallback if local IP ever gets blocked)

Same JSON is on `window.allData` after render — no regex needed:

```python
new_tab("https://finance.ifeng.com/c/8uX19tvXhRq"); wait_for_load(); wait(2)
res = js(r"""(function(){var d=window.allData.docData;
  return {title:d.title,time:d.newsTime,source:d.source,author:d.author,editor:d.editorName};})()""")
print(res)
```

To pick the top headline visually in the cloud browser, sort `/c/` links by computed `font-size` then `top` (the headline block uses the largest font, ~17.7px; sub-heads ~14px).

## Field reference (allData.docData)

| field | meaning |
|-------|---------|
| `title` | article headline |
| `newsTime` / `createTime` | publish time `YYYY-MM-DD HH:MM:SS` (Beijing) |
| `source` | reprint outlet name (e.g. `中国证券报`); **empty for ifeng originals** |
| `sourceUrl` | original URL if reprinted |
| `author` | byline (often empty) |
| `editorName` | responsible editor (usually populated) |
| `base62Id` | the `{id}` in the `/c/{id}` URL |

## Gotchas

- **`http_get` returns a `str`, not a dict.** Do NOT call `.get()` / `.get('text')` on it — it IS the HTML. (Cost me one failed call.)
- **`source` is empty for ifeng-original articles.** Treat empty as "凤凰网财经" (self-published). It only holds a name when the piece is reprinted (中国证券报, etc.).
- **Don't `json.loads` the whole `allData`** — it's huge and nested with mixed content (nav, ads, embedded video items). The naive text search for `"newsTime":` / `"source":` in the raw HTML can hit an **embedded video item's** fields instead of the main article. Always brace-match `"docData":` specifically (helper above) — that's the main-article object.
- Article URL pattern is stable: `https://finance.ifeng.com/c/{base62Id}`. Some feature pieces use `/c/special/{id}` — those also carry `allData`/`docData`.
- No anti-bot / captcha / login seen on either path. Cloud (HK) exit IP also loads fine; no HK geo-block on ifeng. No dedicated免登录 JSON API found — the embedded `docData` blob IS the structured source of truth and is the recommended extraction target.
