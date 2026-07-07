Field-tested on 2026-07-04

GameSpot (gamespot.com) — get a game's GameSpot review score, reviewer, and publish date. Cloudflare-protected; MUST use the cloud browser (new_tab/js), never http_get.

## Do this first (the whole flow, ~4 steps)

1. Derive the game slug from the title: lowercase, drop `:` `'` `.`, spaces → `-`.
   `The Legend of Zelda: Tears of the Kingdom` → `the-legend-of-zelda-tears-of-the-kingdom`
   `Hogwarts Legacy` → `hogwarts-legacy`
2. Open `https://www.gamespot.com/games/<slug>/reviews/` — this listing page names the GameSpot review link.
3. Grab the review URL, open it, run the EXTRACT function below.

The first navigation of a fresh session hits a Cloudflare "Just a moment..." interstitial. It clears on the SECOND navigation (cf cookie gets set). So after any goto_url, check `document.title` for "moment" and re-navigate to the same URL once. After that, all pages in the session load clean.

```python
# One-time Cloudflare warm-up + navigate helper
def gs_goto(url):
    goto_url(url); wait(6)
    if "moment" in js("document.title").lower():
        goto_url(url); wait(6)   # 2nd nav passes Cloudflare

def gs_slug(title):
    import re
    return re.sub(r'[:\'.]', '', title).lower().strip().replace(' ', '-')

# 1) find the review URL from the game's reviews listing
game = "Hogwarts Legacy"
gs_goto(f"https://www.gamespot.com/games/{gs_slug(game)}/reviews/")
review_url = js(r"""(function(){
  var a=Array.from(document.querySelectorAll('a[href*="/reviews/"]'))
        .map(x=>x.getAttribute('href'))
        .find(h=>/\/reviews\/[^\/]+\/1900-\d+\//.test(h));
  return a||null;})()""")
print("review_url:", review_url)

# 2) open the review article and extract fields
gs_goto(review_url)
EXTRACT = r"""(function(){
  var score=null;
  var w=document.querySelector('.review-verdict__score-wrapper, .rating-section');
  if(w){var m=w.innerText.match(/\b(10|[0-9](?:\.\d)?)\b/); if(m)score=m[1];}
  if(score===null){var rt=document.querySelector('[class*="has-rating-"]');
    if(rt){var mm=rt.className.match(/has-rating-(\d+)/); if(mm)score=mm[1];}}
  var a=document.querySelector('a[href*="/profile/"]');
  var reviewer=a?a.innerText.trim():null;
  var date=null;
  document.querySelectorAll('script[type="application/ld+json"]').forEach(function(s){
    try{(JSON.parse(s.textContent)['@graph']||[]).forEach(function(n){
      if(n.datePublished&&!date)date=n.datePublished;});}catch(e){}
  });
  return JSON.stringify({title:document.title.replace(/^\W*\s*/,''),
    score:score, reviewer:reviewer, datePublished:date});
})()"""
print(js(EXTRACT))
```

## Verified results (real calls on 2026-07-04)

- Zelda: Tears of the Kingdom — `/reviews/the-legend-of-zelda-tears-of-the-kingdom-review/1900-6418063/`
  → score **10**, reviewer **Steve Watts**, datePublished **2023-05-11T12:00:00+00:00**
- Hogwarts Legacy — `/reviews/hogwarts-legacy-review-sleight-of-hand/1900-6418032/`
  → score **6**, reviewer **Ivan Ho**, datePublished **2023-02-17T20:43:00+00:00**

## Field extraction cheatsheet (all DOM-verified)

- **Score**: read numeric text from `.review-verdict__score-wrapper` (contains e.g. `"6\nFAIR"`) or `.rating-section`. GameSpot scores are integers 1–10. Do NOT rely on the `has-rating-N` class — it is present on the game reviews-LISTING page but MISSING on the review ARTICLE page (there the element is just `class="rating animate-border"` with the number as text). The EXTRACT above handles both.
- **Reviewer**: `a[href*="/profile/"]` innerText (e.g. "Steve Watts", profile href `/profile/sporkyreeve/`).
- **Date**: from JSON-LD `script[type="application/ld+json"]` → `@graph[].datePublished` (ISO 8601). This is the most reliable date source. `meta[property="article:published_time"]` is NOT present.
- **Title**: `document.title` (strip a leading emoji the session tab prefix may add).

## Gotchas

- **Cloudflare on every fresh session.** First navigation returns `document.title` = "🐴 Just a moment..." / body "Performing security verification". It passes on the 2nd navigation to the same URL — the `gs_goto` helper handles this. After warm-up the whole session stays clear.
- **http_get is useless here** — returns HTTP 403 from the local machine IP (Cloudflare blocks it). No public JSON API found. Everything must go through the cloud browser (new_tab/goto_url + js).
- **Site search does NOT work for scraping.** `https://www.gamespot.com/search/?q=...` redirects to `/games/search/?q=...` and renders results client-side via XHR — `a[href]` queries return zero game/review links even after long waits. Skip search entirely; use the `/games/<slug>/reviews/` listing path instead, which is a static server-rendered page that always contains the review link.
- **Review slug ≠ `<game>-review`.** The article slug can carry a subtitle, e.g. `hogwarts-legacy-review-sleight-of-hand`. Never guess the review URL from the game slug — always discover it via the reviews-listing page and match the regex `/reviews/[^/]+/1900-\d+/`.
- **Score element differs listing-vs-article** (see cheatsheet). The EXTRACT function reads the text wrapper first, class second, so it works on the article page where the class is absent.
- **WebSocket to the CDP endpoint can drop mid-run.** If the harness daemon dies with "WebSocket connection closed", kill it (`pkill -f browser-harness`) and reconnect; if the connect_url itself is dead, create a fresh Lexmount session (the browser container may have been recycled).
