Field-tested on 2026-07-04 (re-verified 2026-07-06)

V2EX (www.v2ex.com) node topic listings: extract the page-1 topic list (titles + reply counts) for any node via **local http_get** on the node HTML page. The cloud browser (Hong Kong exit IP) does NOT work here — see Gotchas.

## Do this first — parse the node HTML page via local http_get

`http_get` runs from the local mainland-China IP, which V2EX serves normally. This is the ONLY route verified to work. Do NOT use the cloud browser (`new_tab`/`js`) for v2ex — it hangs (see Gotchas).

Node page-1 URL pattern: `https://www.v2ex.com/go/<node_name>` (e.g. `tech`, `qna`, `apple`, `programmer`).
Page 1 = exactly 20 topics, ordered by last activity (last reply time), same as what a logged-out user sees.

Each topic row carries its reply count **inline in the topic-link href** as `#reply<N>`, and this exactly matches the visible reply badge (`count_livid` / `count_orange`). Parse the href — it is the most reliable field.

```python
import re, html
r = http_get("https://www.v2ex.com/go/tech", headers={"User-Agent": "Mozilla/5.0"})
body = r if isinstance(r, str) else r.get("body", "")   # http_get may return str OR dict

# Each row: <a href="/t/1182036#reply86" class="topic-link" id="topic-link-1182036">TITLE</a>
rows = re.findall(r'<a href="/t/(\d+)#reply(\d+)" class="topic-link"[^>]*>([^<]+)</a>', body)
# rows = [(topic_id, reply_count_str, title_html), ...]  -> 20 rows on page 1

topics = [(int(rc), html.unescape(t), f"https://www.v2ex.com/t/{tid}") for tid, rc, t in rows]

# Task: most-replied post on the tech node's first page
most = max(topics, key=lambda x: x[0])
print("replies:", most[0], "| title:", most[1], "| url:", most[2])
```

Verified output on 2026-07-04 for `/go/tech`: `86 | 🖖Manus 被 Meta 数十亿美元收购❗❗❗ | https://www.v2ex.com/t/1182036`
(20 topics parsed; the `#reply<N>` counts matched the visible badges exactly — multiset match confirmed.)

To read a topic's own page use the same http_get on `https://www.v2ex.com/t/<id>`.

## JSON API — exists and免登录, but WRONG for "page 1 most-replied"

`https://www.v2ex.com/api/topics/show.json?node_name=<node>` returns JSON with no auth. Verified reachable via local http_get, returns fields:
`id, title, url, replies, content, content_rendered, created, last_touched, last_reply_by, member, node, ...`

```python
import json
r = http_get("https://www.v2ex.com/api/topics/show.json?node_name=tech", headers={"User-Agent":"Mozilla/5.0"})
data = json.loads(r if isinstance(r, str) else r.get("body",""))
# data[i]["replies"], data[i]["title"], data[i]["url"]
```

BUT: this API returned only **10 topics** and a DIFFERENT set than the web page-1 (its max reply count was 12, while the real page-1 max was 86). It does not reflect the node's "first page" listing. **Use the HTML page (above) for any "current first page" task; the API will silently give a wrong answer.** The API is fine only if you just want some recent topics with their absolute reply totals and don't care about page-1 ordering/completeness.

## Gotchas

- **Cloud browser (Lexmount HK exit IP) is blocked/broken for v2ex — do not use it.** `Page.navigate` to v2ex "succeeds" (tab title becomes the 🐴 emoji) but then every `Runtime.evaluate` / `js()` and `DOM.getOuterHTML` on that tab **times out at the IPC layer** — the renderer hangs (page title stays empty). `1+1` on an about:blank tab in the same session works fine, so the session is healthy; only v2ex tabs hang. An in-page `fetch()` to v2ex from the cloud HK IP returns `TypeError: Failed to fetch`. Net: the entire cloud route is dead for this host. Fall back to local `http_get`, which works cleanly.
- **`http_get` return type is inconsistent** — it returned a plain `str` (the body) in this session, but the helper docs imply a dict. Always guard: `body = r if isinstance(r, str) else r.get("body","")`.
- **Reply count lives in the href, not just the badge.** `href="/t/<id>#reply<N>"` — `N` is authoritative and present even when you don't want to parse the separate `count_livid`/`count_orange` span. Topics with 0 replies have `#reply0` in href but NO count badge (14 badges vs 20 rows), so counting badges undercounts rows.
- **Page 1 = 20 topics.** For more, page 2+ is `https://www.v2ex.com/go/<node>?p=2` (not verified here, but the `?p=` param is V2EX-standard).
- No login was needed for the tech node listing or the JSON API — both are public.
