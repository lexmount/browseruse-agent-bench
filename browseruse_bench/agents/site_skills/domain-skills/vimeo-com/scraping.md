Field-tested on 2026-07-04 (verified again 2026-07-05) via Lexmount cloud Chrome (Hong Kong exit IP) + browser-harness.
Vimeo video search: results are server-rendered into the page HTML; there is NO usable JSON search API. Filter + sort are driven entirely by URL query params, so the whole task collapses to loading one URL and reading the DOM.

## Do this first (the whole task in 2 navigations)

Vimeo puts Cloudflare "Just a moment..." on the FIRST hit to /search from a fresh session. Load the homepage once to bank the cf clearance cookie, THEN load the fully-parameterized search URL. All three task constraints (query + Staff Picks + most-popular) live in the URL — no UI clicking required.

```python
# 1. bank Cloudflare clearance (search 404s/challenges without this on a cold session)
goto_url("https://vimeo.com/"); wait_for_load(); wait(3)

# 2. load search with filter+sort baked into the URL, then extract
url = "https://vimeo.com/search?type=clip&q=documentary+filmmaking&sort=popularity_desc&collection=staffpick"
goto_url(url); wait_for_load(); wait(5)

# 3. extract result cards (title / creator / plays), deduped, ranked
EX = r"""
(()=>{
  const seen=new Set(), out=[];
  document.querySelectorAll('a[href]').forEach(a=>{
    const m=a.href.match(/^https?:\/\/vimeo\.com\/(\d+)(?:$|\?|#)/);
    if(!m || seen.has(m[1])) return;
    let card=a;
    for(let i=0;i<8 && card.parentElement;i++){card=card.parentElement; if(/\bviews\b/i.test(card.innerText||'')) break;}
    const lines=(card.innerText||'').split('\n').map(s=>s.trim()).filter(Boolean);
    const vi=lines.findIndex(l=>/\bviews\b/i.test(l)); if(vi<1) return;   // vi-2=title, vi-1=creator, vi=views line
    seen.add(m[1]);
    out.push({rank:out.length+1, id:m[1], url:'https://vimeo.com/'+m[1],
              title:lines[vi-2], creator:lines[vi-1],
              plays:(lines[vi].match(/([\d.,KM]+)\s*views/i)||[])[1]});
  });
  return out;
})()
"""
import json
print(json.dumps(js(EX), ensure_ascii=False, indent=1))

# total match count for the current filter set:
print("total:", js(r"""(()=>{const m=document.body.innerText.match(/Videos\s*\n\s*([\d,]+)/);return m?m[1]:null;})()"""))
```

Verified output (top of the Staff-Picks / most-popular list, "total" = 13 videos):
rank1 LEGO Turing Machine — Andre Theelen — 497K
rank2 FATA MORGANA — Amelie Wen — 238K
rank3 Minka — Birdling Films — 108K
rank4 Before Prison — Reveal Films — 88.5K
rank5 Baby Brother — Kamau Bilal — 53.5K
(descending by plays confirms the sort took effect; 13 total vs 6,825 unfiltered confirms the Staff Picks filter took effect.)

## URL parameter reference (all discovered by driving the real UI and reading the resulting location.href)

Base: `https://vimeo.com/search?q=<query>` (spaces as `+` or `%20`; the SPA canonicalizes to `+`).

| Goal | Param | Notes |
|------|-------|-------|
| Video results only | `type=clip` | Search also has People/Channels/Groups tabs; `type=clip` = the Videos tab. |
| Sort: Most popular | `sort=popularity_desc` | THE working value. `sort=popularity` (no `_desc`) is silently IGNORED — do not use it. |
| Sort: others | `sort=` | Menu labels seen: Relevance (default, omit param), Recently uploaded, Most popular, Title A–Z, Title Z–A, Longest, Shortest. Only `popularity_desc` was param-verified. |
| Filter: Staff Picks | `collection=staffpick` | Maps to the "Vimeo collections → Staff Picks" filter. Cuts 6,825 → 13 results for this query. |

Other filters exist in the "Filters" side panel (Categories: Documentary/Narrative/… , Price: Free/Paid, License: CC*, Resolution: 4K, Duration: Short/Medium/Long, Date uploaded) — each sets its own query param the same way; drive the panel and read location.href if you need one not listed above.

## How the sort/filter UI works (only needed if you must discover a NEW param)

Controls are Chakra UI. The "Relevance" sort button and "Filters" button open a `[data-testid="side-panel"]`. Clicking an option updates `location.href` live (no Apply button needed for sort/Staff-Picks). Two gotchas made this hard to inspect:
- The menu/panel CLOSES the instant a `js()` (CDP Runtime.evaluate) runs — you cannot open-then-read in separate calls. Read it inside the SAME js() call that clicks, OR install a `MutationObserver` into `window.__cap` first, then `click_at_xy`, then read `window.__cap`.
- Set a desktop viewport before clicking, the default cloud viewport is tiny (780x493) and controls overlap: `cdp("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=1, mobile=False)`.
Locate a control by text, e.g. the sort button: `[...document.querySelectorAll('button.chakra-menu__menu-button')].find(b=>/Relevance/.test(b.textContent))`; option nodes live under `[data-testid="side-panel"]`.

## Gotchas

- **Both direct IP paths for raw HTTP are blocked — you MUST use the cloud browser (new_tab/goto_url + js).**
  - `http_get` (runs from the LOCAL China-mainland IP): Vimeo resets the TLS handshake — `ConnectionResetError [Errno 54] Connection reset by peer`. Unusable.
  - Cloud Hong Kong IP via plain fetch of raw endpoints: fine for the SPA, but there is no JSON search API to hit anyway.
- **Cloudflare re-challenges intermittently.** Symptoms: `document.title` becomes `"🐴 Just a moment..."` and `document.body.innerText` collapses to ~114 chars ("Verify to continue"). When you see this: re-load `https://vimeo.com/` (homepage passes cf automatically), wait ~3s, then re-load the search URL and extract IMMEDIATELY. A cleared page shows title `"🐴 Vimeo"` or `"Vimeo"`.
- **No client-side search API.** The page fires only analytics (`fresnel-events.vimeocdn.com`, datadog RUM) — results come pre-rendered in the initial HTML. Guessed endpoints like `/api/felix/search` return 404. Extract from the DOM, not from XHR.
- **Path-style sort URLs 404.** `https://vimeo.com/search/sort:popularity?q=...` gives a "VimeUhOh" 404 page. Sort/filter are query params only (`?sort=...&collection=...`).
- **Dedupe by numeric id.** The DOM carries each visible card plus preload/hidden anchors to the same `/vimeo.com/<id>`; the extractor's `seen` set handles it. (Repeated *titles* with DIFFERENT ids, e.g. two "LEGO Turing Machine", are genuinely distinct uploads in Vimeo's data — not a dedupe bug.)
- **`plays` is the displayed "views" string** (`497K`, `1,443`, `16.4K`) — Vimeo labels the play count as "views" in search cards. Parse `K`/`M` suffixes if you need a number.
- **Result field order inside a card (innerText lines):** `[likes] [comments] [duration] Title / Creator / "<plays> views • <date>"`. The extractor anchors on the "views" line and reads title=vi-2, creator=vi-1.
