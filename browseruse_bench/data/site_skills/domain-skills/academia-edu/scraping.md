Field-tested on 2026-07-04

academia.edu — find an author profile and rank their papers by view count. The site is fully Cloudflare-protected; only the Lexmount cloud browser (new_tab/js) works. Local http_get is hard-blocked.

## Do this first (the whole task in 3 navigations)

Everything below runs inside browser-harness on the cloud session. Do NOT use http_get for academia.edu — it 403s from the local IP.

### Step 1 — People search (get the profile URL)
Append `&tab=2` to the search URL to land directly on the PEOPLE tab (no clicking needed). Cloudflare shows "Just a moment..." on first hit; wait it out (~30-45s), then re-read.

```python
goto_url("https://www.academia.edu/search?q=Geoffrey+Hinton&tab=2")
wait_for_load(); wait(4)
# Clear Cloudflare interstitial if present
for _ in range(6):
    if "Just a moment" not in (js("document.title") or ""):
        break
    wait(8)
# People profile links are anchors to <sub>.academia.edu/<Name>
import json
people = json.loads(js("""
JSON.stringify(Array.from(document.querySelectorAll('a[href]'))
 .filter(a=>/\\.academia\\.edu\\/[A-Za-z][^\\/]*$/.test(a.href) && /Hinton/i.test(a.textContent))
 .map(a=>a.href))
"""))
print(people)
# Also read the innerText to see "N Papers | N Followers" per person to pick the real author
print(js("document.body.innerText"))
```
The result page prints each hit as e.g. `Geoffrey Hinton\n59 Papers | 709 Followers`. Pick the profile with the most papers/followers. For Hinton that is `https://utoronto.academia.edu/GeoffreyHinton` (59 Papers, ~710 Followers, University of Toronto). The other two "Geoffrey Hinton" hits have 0 Papers | 0 Followers.

### Step 2 — Load the profile, lazy-load ALL papers
The profile lives on a department subdomain (utoronto.academia.edu). It throws its OWN Cloudflare challenge (separate from www) — expect another ~40-50s wait. Papers are lazy-loaded: only ~20 render initially, you must scroll to force all of them in.

```python
goto_url("https://utoronto.academia.edu/GeoffreyHinton")
wait_for_load(); wait(5)
for _ in range(8):                          # clear Cloudflare
    if "Just a moment" not in (js("document.title") or ""):
        break
    wait(8)
# Scroll until the work-card count stops growing
prev = 0
for i in range(15):
    scroll(0, 0, 3000); wait(1.5)
    n = js("document.querySelectorAll('.wp-workCard').length")
    if n == prev and i > 2:
        break
    prev = n
print("workcards", js("document.querySelectorAll('.wp-workCard').length"))  # 59 for Hinton
```

### Step 3 — Extract (title, views) and rank
Each paper is a `div.wp-workCard`. The title is its first `academia.edu/` anchor; the view count is a leaf node matching `"N Views"` inside the same card.

```python
import json
rows = json.loads(js("""
(function(){
  let cards = Array.from(document.querySelectorAll('.wp-workCard'));
  let out = [];
  cards.forEach(c=>{
    let a = c.querySelector('a[href*="academia.edu/"]');
    let title = a ? a.textContent.trim() : '';
    let vn = Array.from(c.querySelectorAll('*'))
      .find(e=>e.children.length===0 && /^[\\d,]+\\s+Views?$/.test(e.textContent.trim()));
    let v = vn ? parseInt(vn.textContent.replace(/[^0-9]/g,'')) : 0;
    out.push({title:title.replace(/\\s+/g,' ').slice(0,90), views:v, href:a?a.getAttribute('href'):''});
  });
  out.sort((x,y)=>y.views-x.views);
  return JSON.stringify(out.slice(0,10));
})()
"""))
for r in rows[:3]:
    print(r["views"], "|", r["title"])
```

Verified output (Hinton, 2026-07-04), top 3 by views:
1. How neural networks learn from experience — 2921 Views
2. Boltzmann machines: Constraint satisfaction networks that learn — 1816 Views
3. Adaptive mixtures of local experts — 1015 Views

(Profile header also shows aggregate "Public Views 12,615  Top 3%" — that is a lifetime profile total, NOT a per-paper figure; do not confuse it with the per-paper "N Views".)

## What's verified vs. not
- VERIFIED working selectors: `.wp-workCard` (one per paper), `.wp-workCard a[href*="academia.edu/"]` (title link), leaf node regex `^[\d,]+\s+Views?$` (per-paper views). Search-tab switch via URL `&tab=2`.
- VERIFIED: cloud browser (Hong Kong exit IP) passes academia.edu Cloudflare after a wait, on both www and the department subdomain.

## Gotchas
- **Local http_get is dead for academia.edu.** Both `www.academia.edu/search` and `/` return HTTP 403 from the local (China) IP — Cloudflare blocks it outright. There is NO working http_get path and NO discovered public JSON API. Everything must go through the cloud browser's new_tab/js.
- **Cloudflare "Just a moment..." on first navigation to each host.** It auto-solves in the cloud browser but is slow: search page cleared in ~40s, the utoronto subdomain profile in ~45-50s. Always poll `document.title` for "Just a moment" and wait in a loop before reading content. `page_info()['title']` shows the same title.
- **Default search tab is PAPERS, not PEOPLE.** A plain `?q=X` shows paper-title matches; the author profiles live under the PEOPLE tab. The tab is a React `<li class="Tabs-...">` with NO anchor/href — you cannot get its link. Two ways in: (a) append `&tab=2` to the search URL (works on fresh load, recommended), or (b) `js("(function(){Array.from(document.querySelectorAll('li[class*=Tabs]')).find(e=>/People/i.test(e.textContent)).click();})()")` then wait.
- **Profiles sit on department subdomains** (e.g. `utoronto.academia.edu`, `independent.academia.edu`), each its own Cloudflare origin — budget a fresh challenge wait per subdomain.
- **Papers are lazy-loaded.** ~20 of 59 cards render on load; you MUST scroll (loop, watch `.wp-workCard` count) or you will rank an incomplete set and miss the true top papers. The initial view (before scrolling) does not contain the #1 paper.
- **View counts require no login** — the "N Views" per paper and the ranking are visible to anonymous users. "Upgrade to view results" / "Log In" prompts appear only for full-text paper search, not for the profile's paper list or view counts.
- **Title-to-views association**: bind them within each `.wp-workCard` (as above). Do NOT collect all "Views" nodes and all title links separately and zip them — some cards lack a clean title anchor and the lists desync.
