Field-tested on 2026-07-04
mgtv.com (芒果TV) — locate a variety show, list its seasons/episodes, and extract guests + playcount + duration via免登录 JSON APIs called from inside the cloud page (fetch runs on the cloud IP; http_get is blocked/403).

## Do this first (fastest verified path)
All mgtv JSON APIs are open (no login) but **enforce a `Referer: https://www.mgtv.com/` header and only respond from the cloud IP** — so call them with `js()` `fetch(...)`, NOT `http_get` (http_get runs on the local IP → search API returns 403). Flow:

1. **Search** `mobileso.bz.mgtv.com/msite/search/v2` → gives每个季/系列 a `media` row with title, 嘉宾 (guest list), type/year, and a URL whose first path segment is the **collection_id** (e.g. `/b/446003/...` → cid 446003).
2. **Episode list** `pcweb.api.mgtv.com/variety/showlist?collection_id=<cid>` → episodes with `playcnt` (播放量), `time` (时长), `t1`/`t3` (期数/标题), `t2` date; `tab_m` lists available months; `related.url` often points to a **newer season** (`/b/<newerCid>/`).
3. **Episode detail** `pcweb.api.mgtv.com/video/info?cid=<cid>&vid=<vid>` → `data.info.detail`: `story` (简介), `releaseTime` (首播/播出时间), `kind`, `updateInfo`, plus `time` (时长).

### Verified runnable code
```python
BU_NAME=wfmgtvcom BU_CDP_WS='<CONNECT_URL>' ./browser-harness <<'PY'
# open any mgtv page once so fetch() runs on the cloud origin
new_tab("https://www.mgtv.com/"); wait_for_load(); wait(2)

r = js(r"""
(async () => {
  const H = {headers:{'Referer':'https://www.mgtv.com/'}};
  const j = u => fetch(u, H).then(r=>r.json());
  const kw = encodeURIComponent('乘风破浪');

  // 1) SEARCH -> season media rows (title + 嘉宾 + collection_id from url)
  const s = await j('https://mobileso.bz.mgtv.com/msite/search/v2?q='+kw+'&pn=1&pc=10&_support=10000000');
  const seasons = [];
  for (const c of (s.data.contents||[])) if (c.type==='media' && Array.isArray(c.data))
    for (const d of c.data) {
      const cid = (d.url||'').split('/')[2];           // "/b/446003/xxx.html" -> "446003"
      seasons.push({title:(d.title||'').replace(/<\/?B>/g,''), guests:d.desc, cid, url:d.url});
    }

  // 2) SHOWLIST for the chosen season's cid (here: first hit)
  const cid = seasons[0].cid;
  const sl = await j('https://pcweb.api.mgtv.com/variety/showlist?allowedRC=1&collection_id='+cid+'&page=1&_support=10000000');
  const newerSeason = sl.data.related && sl.data.related.url;   // e.g. /b/859271/ = a later season
  const eps = (sl.data.list||[]).map(e=>({
    ep:e.t1, full:e.t3, date:e.t2, playcnt:e.playcnt, time:e.time,   // time = 节目时长 "67:26"
    vid:e.video_id, url:e.url
  }));

  // 3) DETAIL for one episode (简介 / 播出时间)
  const vid = eps[0].vid;
  const vi = await j('https://pcweb.api.mgtv.com/video/info?allowedRC=1&cid='+cid+'&vid='+vid+'&_support=10000000');
  const det = vi.data.info.detail;   // {story, releaseTime, kind, updateInfo, area, ...}

  return {seasons: seasons.slice(0,4), months: sl.data.tab_m, newerSeason,
          episodes: eps.slice(0,5), detail:{story:det.story, releaseTime:det.releaseTime, kind:det.kind, updateInfo:det.updateInfo}};
})()
""")
import json; print(json.dumps(r, ensure_ascii=False, indent=1))
PY
```

## Field map (all verified live)
- **嘉宾 (guest list)**: `search/v2` → `media` row `desc[1]` = `"嘉宾: 那英 宁静 ..."` (space-separated). This is the most reliable guest source. `pcweb.api.mgtv.com/star/list?video_id=<vid>` exists but returned `[]` for these shows — do not rely on it.
- **播放量 (playcount)**: `showlist` `list[].playcnt` (already humanized, e.g. `"2.9亿"`, `"1390.7万"`).
- **节目时长 (duration)**: `showlist` `list[].time` = `"MM:SS"` / `"MMM:SS"` (e.g. `"67:26"`, `"219:56"`); also in `video/info` `data.info.time`.
- **期数/标题**: `showlist` `t1` (short, e.g. `"第12期：X-SISTER荣耀诞生"`) and `t3` (full看点); `updateInfo` in detail = `"更新至2026-07-03期"`.
- **播出时间 (air date)**: per-episode `showlist` `t2` = `"2026-07-03"`; season 首播 = `video/info` `detail.releaseTime` = `"2026-04-02"`.
- **简介 (description)**: `video/info` `data.info.detail.story`.
- **latest season / 最新一季**: the top search hit is NOT always the newest. `showlist.data.related.url` links to a later season (verified: 乘风破浪第三季 cid 446003 → `related` → 乘风2026 cid 859271, whose newest episode was 2026-07-03). To get truly latest, search, then follow `related.url` collection_id and re-run `showlist`.

### Verified live values (2026-07-04)
- 乘风2026 (cid **859271**, seriesId 105454): latest ep `加更版：曾沛慈谈唐艺昕` 2026-07-03, playcnt `1390.7万`, time `67:26`, releaseTime 2026-04-02.
- 乘风破浪 第三季 (cid **446003**, 2022): guests `那英 宁静 张蔷 许茹芸 黄奕 …`; ep `第12期：X-SISTER荣耀诞生` playcnt `2.9亿` time `219:56`.
- 披荆斩棘的哥哥 (cid **367750**, 2021): guests `黄贯中 林志炫 陈小春 谢天华 …`. (Also 舞台纯享版 cid 385819, 黑胶全开麦 cid 385821 — pick the main `type: media` row without a suffix in its title.)

## Gotchas
- **http_get is 403 / blocked.** The search API (`mobileso.bz...`) returns HTTP 403 `{"msg":"Forbidden"}` when called from the local IP (http_get) or without the Referer. Always call from `js()` fetch on a loaded mgtv page **with** `Referer: https://www.mgtv.com/`. `pcweb.api.mgtv.com` is more lenient but still safest via the cloud page.
- **观众评分 (viewer rating) does NOT exist for variety shows.** Verified: the play page DOM has no rating element, `video/info` JSON contains no `score`/`rating`/`评分`/`豆瓣` field, and no rating/star-score endpoint responded. The `star/list` endpoint is a cast list (returned `[]` here), not a score. If a task demands a viewer rating for a 综艺, report it as unavailable rather than inventing one. (Movies/dramas may differ — untested.)
- **collection_id vs video_id**: the URL `/b/<collection_id>/<video_id>.html`. `showlist` is keyed by `collection_id`; `video/info` needs both `cid` (=collection_id) and `vid` (=video_id).
- Search `title` fields are wrapped in `<B>…</B>` highlight tags — strip with `.replace(/<\/?B>/g,'')`.
- Guest suffix-season trap: for shows with spinoffs (纯享版 / 黑胶全开麦), search returns multiple `media` rows; the canonical show is the one whose title has no descriptive suffix.
- `showlist` `tab_m` gives available months; add `&month=YYYYMM` to page older episodes. Default (no month) returns the most recent batch — good enough for "最新一期".
- The cloud page must be a real mgtv page before fetch() (open `https://www.mgtv.com/` first) so the request origin is mgtv; a blank tab origin can trip CORS/Referer checks.
