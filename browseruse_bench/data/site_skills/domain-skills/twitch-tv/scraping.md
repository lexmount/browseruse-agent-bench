Field-tested on 2026-07-04 (verified again 2026-07-05) — Twitch stream/channel data is best pulled from the public GraphQL endpoint `https://gql.twitch.tv/gql`; no login needed, DOM scraping is a fragile last resort.

## Do this first — public GraphQL (no auth, works from the cloud/HK IP)

Twitch's web client uses a **public, hardcoded Client-ID** that anyone can send:
`kimne78kx3ncx6brgo4mv6wki5h1ko`. With just that header you can run **raw GraphQL queries** (no persisted-query hash needed). Endpoint returns HTTP 200 and JSON. Verified from the Lexmount cloud browser (Hong Kong exit IP) — Twitch does NOT block HK for the API, so run these via `js(...)` fetch inside the cloud browser. (You must have a Twitch page open in the tab, or CORS blocks it — `new_tab("https://www.twitch.tv/")` first.)

Fields available per stream node: `title`, `viewersCount`, `broadcaster { login displayName }`, `game { name }`.

### Task pattern: "search 'X' streams, filter live 1000+ viewers, top 3 with title/viewers/game"

There are TWO different data sources depending on what "search X" means — pick by what actually has data:

**(A) Keyword search of streams** — the literal `/search?term=X` results, operation `searchStreams`.
This matches channel names / titles containing the word. Small result set (capped ~11 for "speedrun", no real pagination), often low viewers. Use when the task literally says "search".

```python
# run inside cloud browser after new_tab("https://www.twitch.tv/")
res = js(r'''
(async () => {
  const H={"Client-ID":"kimne78kx3ncx6brgo4mv6wki5h1ko","Content-Type":"application/json"};
  const query=`query($q:String!){ searchStreams(userQuery:$q, first:100){
    edges{ node{ title viewersCount broadcaster{login displayName} game{name} } } } }`;
  const r=await fetch("https://gql.twitch.tv/gql",{method:"POST",headers:H,
    body:JSON.stringify({query,variables:{q:"speedrun"}})});
  const j=await r.json();
  const nodes=(j.data.searchStreams.edges||[]).map(e=>e.node);
  const live1000=nodes.filter(n=>n.viewersCount>=1000).sort((a,b)=>b.viewersCount-a.viewersCount);
  return {total:nodes.length, over1000:live1000.length,
    top3: live1000.slice(0,3).map(n=>({title:n.title, viewers:n.viewersCount,
      streamer:n.broadcaster.displayName, game:n.game?n.game.name:null}))};
})()
''')
```

**(B) Category / game directory** — operation `game(slug){ streams(options:{sort:VIEWER_COUNT}) }`.
This is the high-viewer, sorted-by-default source. Use when "X" is a game/category name. `slug` = lowercase, spaces→hyphens (e.g. "Just Chatting" → `just-chatting`). Filtering 1000+ and taking top-3 is trivial because it's already sorted.

```python
res = js(r'''
(async () => {
  const H={"Client-ID":"kimne78kx3ncx6brgo4mv6wki5h1ko","Content-Type":"application/json"};
  const query=`query($slug:String!){ game(slug:$slug){ displayName
    streams(first:30, options:{sort:VIEWER_COUNT}){
      edges{ node{ title viewersCount broadcaster{displayName} game{name} } } } } }`;
  const r=await fetch("https://gql.twitch.tv/gql",{method:"POST",headers:H,
    body:JSON.stringify({query,variables:{slug:"just-chatting"}})});
  const j=await r.json();
  if(!j.data||!j.data.game) return {err:JSON.stringify(j).slice(0,300)};
  const nodes=(j.data.game.streams.edges||[]).map(e=>e.node);
  const over=nodes.filter(n=>n.viewersCount>=1000);   // already sorted desc
  return {category:j.data.game.displayName, total:nodes.length, over1000:over.length,
    top3: over.slice(0,3).map(n=>({title:n.title, viewers:n.viewersCount,
      streamer:n.broadcaster.displayName, game:n.game.name}))};
})()
''')
```

Verified output shape (Just Chatting, 2026-07-05): top3 = stableronaldo/31650, HasanAbi/26005, jasontheween/18135 — all with title+viewers+streamer+game. This proves the sort+filter+top3 pipeline end to end.

## For the specific "speedrun" task

- **searchStreams("speedrun")**: real result on 2026-07-04/05 was only **11 streams, max ~80 viewers, ZERO at 1000+**. So the literal keyword search yields no 1000+ live channels — this is a genuine data state, not a bug. Report "0 channels ≥1000 viewers" and list the actual top-3 by viewers (LD_speedruns 80, SpeedrunHypeTV 22, SpeedRaiseR 14).
- **`game(slug:"speedrun")` category**: exists (`displayName:"Speedrun"`) but had **0 live streams** at test time — do not rely on it for this task.
- If a task means the broader speedrunning scene, the biggest live speedruns show up under specific game categories (e.g. slug for the game being run), not under a single "speedrun" bucket.

## Gotchas

- **Client-ID is required**; without the `Client-ID` header you get an auth error. The public web one above is stable and unauthenticated.
- **Persisted queries fail**: sending `{extensions:{persistedQuery:{sha256Hash:...}}}` returns `PersistedQueryNotFound` (hashes rotate). Always send a **raw `query` string** instead — that path is not gated.
- **CORS**: the `fetch` must originate from a twitch.tv page context. Open `new_tab("https://www.twitch.tv/")` (or any twitch page) before calling `js(...)` fetch, or the browser blocks the cross-origin POST.
- **HK cloud IP is fine** for gql.twitch.tv (HTTP 200). Not tested from local `http_get` — the API is CORS/origin-sensitive so prefer the cloud-browser `js` fetch. If you must use local `http_get`, Twitch may treat the raw non-browser request differently; the cloud `js` path is the verified one.
- **`streams(tags:["speedrun"])` does NOT filter** by freeform tag string — it silently returns the global top streams (Jynxzi, HasanAbi, etc.) unrelated to speedrun. Freeform-tag filtering needs tag IDs, not names; avoid this operation for keyword tasks. Use `searchStreams` (keyword) or `game(slug)` (category) instead.
- **DOM scraping is unreliable**: Twitch obfuscates class names and virtualizes cards. `a[data-a-target="preview-card-image-link"]` and `a[data-test-selector="TitleAndChannel"]` returned 0 matches on the search page after scrolling. The `/search?term=X` page DOM also mixes a "People searching for X also watch" recommendation carousel (large streams like Feinberg 2.9K) with the real "Live channels tagged X" results — easy to grab the wrong section. Use the GraphQL API, not the DOM.
- **`viewersCount`** is an integer already (no "K"/"M" parsing needed) — unlike the DOM which shows "2.9K".
