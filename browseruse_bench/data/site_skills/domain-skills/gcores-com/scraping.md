Field-tested on 2026-07-04 (re-verified 2026-07-06)

gcores.com (机核 GCORES) is an Ember SPA backed by a public, no-auth JSON:API at `https://www.gcores.com/gapi/v1/`. Skip the browser search UI entirely — hit the API.

## Do this first: search articles via the JSON:API (no login, works from local IP)

The site search box is a client-side SPA that does NOT read the `?q=` URL param and pops a NetEase captcha/login modal — do not drive it. Use the API instead.

Search endpoint (verified 200):
```
GET https://www.gcores.com/gapi/v1/search?query=<URL-encoded-keyword>&type=articles&include=user&page[limit]=30
Header: Accept: application/vnd.api+json
```
- `type` can be `articles` (also `radios`/播客, `videos`, `games`, etc.).
- `include=user` embeds the author under `included[]` as `type:"users"`, joined via `data[].relationships.user.data.id`; the author name is `attributes.nickname`.
- **Gotcha (verified): the `sort=-published-at` param is silently ignored by the search endpoint** — results come back relevance-ordered, not by date. To get the *latest* article you MUST sort client-side by the `published-at` attribute.

Both transport paths are verified working for this host (no geo-block, no anti-bot on the API):
- Local IP: `http_get(url, headers={"Accept":"application/vnd.api+json"})` — returned full JSON, status 200.
- Cloud browser: `js("(async()=>{ ... fetch ... })()")` — returned full JSON, status 200.

### Verified snippet — search + pick newest article + author (run in cloud js(), or adapt to http_get)
```python
res = js(r"""
(async()=>{
  const kw = encodeURIComponent('塞尔达传说');
  const u = 'https://www.gcores.com/gapi/v1/search?query='+kw+'&type=articles&include=user&page%5Blimit%5D=30';
  const r = await fetch(u, {headers:{'Accept':'application/vnd.api+json'}});
  const j = await r.json();
  const users = {};
  (j.included||[]).forEach(x=>{ if(x.type==='users') users[x.id]=x.attributes.nickname; });
  let arr = (j.data||[]).map(d=>{
    const a = d.attributes;
    const uid = (((d.relationships||{}).user||{}).data||{}).id;
    return {id:d.id, title:a.title, published:a['published-at'], author:users[uid]||uid};
  });
  arr.sort((a,b)=> new Date(b.published) - new Date(a.published));  // newest first — REQUIRED
  return {latest: arr[0], count: arr.length};
})()
""")
print(res)
# -> latest: {"id":"215667","title":"《塞尔达传说：时之笛》重制版正式公布：6月任天堂直面会消息汇总",
#             "published":"2026-06-09T22:55:57.000+08:00","author":"YT17"}
```

Local-IP equivalent (backup, verified same JSON):
```python
r = http_get("https://www.gcores.com/gapi/v1/search?query=%E5%A1%9E%E5%B0%94%E8%BE%BE%E4%BC%A0%E8%AF%B4&type=articles&include=user&page%5Blimit%5D=30",
             headers={"Accept":"application/vnd.api+json"})
import json; j = json.loads(r["body"] if isinstance(r,dict) else r)
# then same users-map + sort-by-published-at logic as above
```

## Article detail by id (verified 200)
```
GET https://www.gcores.com/gapi/v1/articles/<id>?include=user
Header: Accept: application/vnd.api+json
```
Fields: `data.attributes.title`, `data.attributes.published-at`, author via `included[] type:"users" .attributes.nickname`.
`data.attributes.content` is a JSON string of Draft.js blocks (`{"blocks":[{"text":...}]}`) — parse and join `blocks[].text` for the body if needed.

## Also verified
- Newest articles site-wide (no keyword): `GET /gapi/v1/articles?page[limit]=3&sort=-published-at` → 200 (here `sort` DOES work, unlike the search endpoint).

## Gotchas
- **Search box UI is a trap**: `/search?q=...` renders "请输入搜索关键词" and does not run the query; typing + Enter triggers a NetEase captcha (`ir-sdk.dun.163.com`) and a login/register modal. Never rely on it — use `/gapi/v1/search`.
- **`sort=-published-at` is ignored on `/gapi/v1/search`** — always sort client-side by `published-at`. (It IS respected on `/gapi/v1/articles`.)
- Author lives in `included[]` (type `users`, field `nickname`), keyed by `relationships.user.data.id` — not inline in the article attributes.
- No anti-bot on the JSON:API; both local `http_get` and cloud `fetch` returned identical 200 JSON with no captcha. No geo-issue observed from the HK cloud egress for this host.
