Field-tested on 2026-07-04 (re-verified 2026-07-06) — xueqiu.com (雪球): resolve a stock keyword → symbol, then pull its discussion timeline as clean JSON. No login needed; run everything inside the Lexmount cloud browser via `js(fetch(..., {credentials:'include'}))` so the anonymous xq cookie rides along.

## Do this first (keyword → symbol → discussion posts)

The whole task is two免登录 JSON endpoints. Both must be called from the cloud browser with `credentials:'include'` — the first `new_tab("https://xueqiu.com")` sets the anonymous cookie that these APIs require. Do NOT use local `http_get` for them (it returns HTTP 400 without cookies).

```python
# 1) Warm up the session so the anonymous cookie is set
new_tab("https://xueqiu.com"); wait_for_load(); wait(2)

# 2) Keyword -> symbol.  q is the plain Chinese name; URL-encode it.
import urllib.parse
kw = urllib.parse.quote("宁波银行")
r = js(f"""(async function(){{
  var res = await fetch('https://xueqiu.com/query/v1/suggest_stock.json?q={kw}', {{credentials:'include'}});
  return await res.text();
}})()""")
print(r)
# -> data[0].code == "SZ002142"  (Shenzhen A-share; SH=Shanghai, prefix by market)

# 3) symbol -> latest discussion posts (sort=time). count<=? works up to ~20; page for more.
sym = "SZ002142"
r = js(f"""(async function(){{
  var res = await fetch('https://xueqiu.com/query/v1/symbol/search/status.json?count=20&comment=0&symbol={sym}&hl=0&source=all&sort=time&page=1&q=&type=11', {{credentials:'include'}});
  var j = await res.json();
  var posts = (j.list||[]).map(function(p){{
    return {{
      user: p.user && p.user.screen_name,
      created: p.created_at,                       // epoch ms
      text: (p.text||'').replace(/<[^>]+>/g,'').trim(),  // strip HTML tags
      reply: p.reply_count, retweet: p.retweet_count,
      like: p.like_count, fav: p.fav_count,
      target: p.target                              // "/uid/statusId" -> full URL below
    }};
  }});
  return JSON.stringify({{count:j.count, maxPage:j.maxPage, posts:posts}});
}})()""")
print(r)
# Full post URL = "https://xueqiu.com" + p.target
```

For the benchmark task ("总结最近投资者的主要观点"): call step 3 with `sort=time`, read the `text` fields of the first 20–40 posts (page through `page=1,2,...`), and summarize. That's the whole flow — no HTML scraping needed.

## Verified endpoints (all HTTP 200 from cloud browser, 2026-07-04)

- **Search suggest (keyword→symbol):** `GET https://xueqiu.com/query/v1/suggest_stock.json?q=<urlencoded name>` → `data[].code` is the symbol, `query` is the matched name.
- **Discussion timeline:** `GET https://xueqiu.com/query/v1/symbol/search/status.json?count=20&symbol=<SYM>&source=all&sort=time&page=<n>&type=11`
  - `sort=time` = newest first (what you want for "最近观点"); `sort=alpha`/`reply`/other = different ordering. Verified: `count:1000`, `maxPage:200`, `page=2` returns fresh posts.
  - Minimal working query needs at least `symbol`, `source=all`, `sort`, `page`, `type=11`. `comment=0&hl=0&q=` are harmless extras.
  - Post fields: `user.screen_name`, `created_at` (epoch ms), `text` (HTML — strip `<[^>]+>`), `reply_count`/`retweet_count`/`like_count`/`fav_count`, `target` (path `/uid/statusId`).
- **Quote (price/detail):** `GET https://stock.xueqiu.com/v5/stock/quote.json?symbol=<SYM>&extend=detail` → `data.quote.*` (current price, high52w, etc.). Also免登录 from cloud browser.

## Browser-fallback path (if an API changes)

Search page renders server-side and exposes the stock link:
```python
new_tab("https://xueqiu.com/k?q=" + urllib.parse.quote("宁波银行")); wait_for_load(); wait(2)
links = js("""JSON.stringify([...new Set([].slice.call(document.querySelectorAll('a'))
  .map(a=>a.href).filter(h=>/\\/S\\/[A-Z0-9]+/.test(h)))])""")
# -> ["https://xueqiu.com/S/SZ002142"]  ; the /S/<SYM> path is the stock detail+discussion page
```

## Gotchas

- **Local `http_get` does NOT work for these APIs** — `stock.xueqiu.com/v5/stock/quote.json` returns HTTP 400 from the local IP without the xq cookie. Everything must go through the cloud browser `js(fetch(...,{credentials:'include'}))` after a homepage `new_tab` warms the cookie. This is the opposite of the "http_get as backup" pattern — here the cloud browser is the ONLY path that carries the anonymous session.
- Cloud IP (Hong Kong) is fine for xueqiu — no 403/geo-block observed; it's a mainland finance site that serves the same content.
- `text` field is HTML; always strip tags. Many posts start with `$宁波银行(SZ002142)$` cashtag markup — keep or drop as needed.
- `created_at` is epoch **milliseconds**, not seconds.
- Symbol prefix encodes the market: `SZ`=深圳, `SH`=上海, `HK`=港股, plain ticker for US. Always resolve via suggest_stock.json rather than guessing.
- `count` in the response is a capped total (1000) / `maxPage` 200 — not the true post count; just page until you have enough recent posts.
