Field-tested on 2026-07-04 — crunchyroll.com anime series: get seasons + per-season episode counts via the internal content/v2 JSON API (no login needed, just an anonymous token).

## Do this first (fastest, fully verified)

Crunchyroll has a clean internal JSON API. You do NOT need to log in. Mint an anonymous bearer token with the public web client (`cr_web:` = Basic `Y3Jfd2ViOg==`), then hit the search + seasons endpoints. All three calls succeed from the Lexmount cloud IP.

IMPORTANT: run these via `js()` (in-page fetch on the cloud IP), NOT `http_get`. The token endpoint sets/uses cookies and the country is derived from the browser's egress IP; in-page fetch keeps everything consistent. http_get from the local machine may return a different region and is untested here.

```python
# One shot: search a title -> pick the series -> get seasons + per-season episode counts
r = js("""(async () => {
  // 1) anonymous token (public cr_web client, no login)
  const tr = await fetch('https://www.crunchyroll.com/auth/v1/token', {
    method:'POST',
    headers:{'Content-Type':'application/x-www-form-urlencoded','Authorization':'Basic Y3Jfd2ViOg=='},
    body:'grant_type=client_id', credentials:'include'
  });
  const tok = (await tr.json()).access_token;
  const H = {'Authorization':'Bearer '+tok};

  // 2) search (type=series). data[] is grouped; flatten .items
  const q = encodeURIComponent('Attack on Titan');
  const sjRaw = await (await fetch(
    'https://www.crunchyroll.com/content/v2/discover/search?q='+q+'&n=6&type=series&locale=en-US',{headers:H})).json();
  let hits=[]; (sjRaw.data||[]).forEach(g=>(g.items||[]).forEach(it=>hits.push({id:it.id,title:it.title,type:it.type})));
  const series = hits.find(h=>h.type==='series') || hits[0];   // first result is the right one for AoT / Demon Slayer

  // 3) seasons (region-filtered) + per-season episode counts
  const sj = await (await fetch(
    'https://www.crunchyroll.com/content/v2/cms/series/'+series.id+'/seasons?locale=en-US',{headers:H})).json();
  const seasons = (sj.data||[]).map(s=>({title:s.title, season_number:s.season_number, episodes:s.number_of_episodes}));

  // 4) series meta = catalog-wide season/episode totals (may exceed what's licensed in this region)
  const meta = (await (await fetch(
    'https://www.crunchyroll.com/content/v2/cms/series/'+series.id+'?locale=en-US',{headers:H})).json()).data[0];

  return {
    series_id: series.id, series_title: series.title,
    accessible_season_count: sj.total,                                  // seasons visible in this region
    accessible_total_episodes: seasons.reduce((a,s)=>a+(s.episodes||0),0),
    seasons,
    catalog_season_count: meta.season_count,                            // full-catalog totals
    catalog_episode_count: meta.episode_count
  };
})()""")
print(r)
```

Just change the `q=` value to search a different title (e.g. `'Demon Slayer'`). No other change needed.

## Key endpoints (all verified working, status 200)

- Token: `POST https://www.crunchyroll.com/auth/v1/token`
  headers `Authorization: Basic Y3Jfd2ViOg==` + `Content-Type: application/x-www-form-urlencoded`, body `grant_type=client_id`. Returns `{access_token}`. Anonymous, no account.
- Search: `GET https://www.crunchyroll.com/content/v2/discover/search?q=<url-enc>&n=6&type=series&locale=en-US`
  Response `data[]` is grouped by type; each group has `.items[]` with `{id,title,type}`. For "Attack on Titan" and "Demon Slayer" the correct series is the FIRST item.
- Seasons: `GET https://www.crunchyroll.com/content/v2/cms/series/<SERIES_ID>/seasons?locale=en-US`
  `total` = season count (region-filtered); each `data[i]` has `title`, `season_number`, `number_of_episodes`. Sum `number_of_episodes` for total episodes.
- Series meta: `GET https://www.crunchyroll.com/content/v2/cms/series/<SERIES_ID>?locale=en-US`
  `data[0].season_count`, `data[0].episode_count` = catalog-wide totals.
- Known series IDs (stable, verified): Attack on Titan = `GR751KNZY`; Demon Slayer: Kimetsu no Yaiba = `GY5P48XEY`.

All bearer calls only need `{'Authorization':'Bearer '+tok}` — no other headers.

## Gotchas

- REGIONAL LICENSING is the big one. The Lexmount cloud IP resolved to country HK, and the `/seasons` endpoint only returns seasons LICENSED in that region. Measured results (HK, 2026-07-04):
  - Attack on Titan (`GR751KNZY`): `/seasons` -> 1 season, 27 episodes. But the on-page synopsis says "four seasons," and the catalog is larger. So the accessible count != the globally-known count.
  - Demon Slayer (`GY5P48XEY`): `/seasons` -> 1 season ("Hashira Training Arc", season_number 7), 8 episodes; while series meta says `season_count:2, episode_count:16`.
  Report BOTH numbers and label them: "accessible in this region (via /seasons)" vs "catalog total (via series meta)". Do not silently present the region number as the absolute answer — it will look wrong to a user who knows AoT has 4 seasons. If the task needs the true global count you would need a US/JP egress IP; not available on this HK session.
- `meta.season_count` / `meta.episode_count` can be LARGER than the summed `/seasons` episodes (16 vs 8 for Demon Slayer) because meta counts the full catalog while `/seasons` is region-gated. They can also disagree in the other direction if a season is partly available. Treat `/seasons` (summed) as ground truth for what's actually watchable in-region.
- The token needs a token: the browser stores NO bearer token in localStorage/sessionStorage (it's held in a JS closure). Do not scrape storage — mint your own anonymous token as above. A raw call to any `content/v2/...` endpoint without `Authorization: Bearer` returns `401 {"code":"content.error.invalid_auth_token"}`.
- DOM fallback is painful and NOT recommended: the series page (`/series/<ID>/<slug>`) renders episodes (83 `a[href*="/watch/"]` cards were present for AoT) but the season <select> is a custom non-native dropdown that is easy to mis-click (clicking near it opened the global nav menu instead). The JSON-LD `TVSeries` block on the page does not include a season/episode count. Use the API; only fall back to DOM if the API changes.
- Search grouping: `discover/search` returns `data[]` as TYPE GROUPS (music, series, episodes...), each with `.items[]`. You must flatten `.items` and filter `type==='series'` — the top-level `data[]` entries are not the results themselves.
- The human-facing search page `https://www.crunchyroll.com/search?q=...` also works and its result links have the pattern `/series/<ID>/<slug>` (first `a[href*="/series/"]` was Attack on Titan), so you can extract the SERIES_ID from a rendered search page if you ever need to avoid the search API. But the API path is cleaner.
