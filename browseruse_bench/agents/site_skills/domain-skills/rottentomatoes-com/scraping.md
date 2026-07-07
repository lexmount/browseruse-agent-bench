# rottentomatoes.com — scraping

Field-tested on 2026-07-04. Get a movie's Tomatometer (critics) score, Audience Score, and review counts from Rotten Tomatoes by hitting the server-rendered HTML directly.

## Do this first (fastest — no browser needed)

Rotten Tomatoes is NOT IP-blocking: `http_get` from the local China IP returns the full server-rendered HTML with all scores baked in (both the `media-scorecard` web-component slots and a JSON-LD `aggregateRating`). No JS execution, no login, no cookies required. Two plain `http_get` calls cover the whole "search → open movie → read scores" task.

### Step 1 — Search, pick the top movie result

Search URL pattern: `https://www.rottentomatoes.com/search?search=<url-encoded query>`

The first/best match is the `<search-page-media-row>` carrying `data-qa="info-name"` on its title link. Each row also exposes `tomatometer-score` as an attribute, so you can even read the critics score straight from the search page.

```python
import re, urllib.parse
q = "Dune: Part Two"
body = http_get("https://www.rottentomatoes.com/search?search=" + urllib.parse.quote(q))
# Top result's movie URL (verified: returns /m/dune_part_two for "Dune: Part Two"):
m = re.search(r'href="(https://www\.rottentomatoes\.com/m/[^"]+)"[^>]*data-qa="info-name"', body)
movie_url = m.group(1)
print(movie_url)
# Bonus: tomatometer score is also on the row attribute:
row = re.search(r'<search-page-media-row[^>]*tomatometer-score="(\d+)"[^>]*>.*?'
                r'href="https://www\.rottentomatoes\.com/m/', body, re.S)
print("search-row tomatometer:", row.group(1) if row else None)  # -> 92
```

### Step 2 — Open the movie page, read all scores

The movie page HTML contains a `<media-scorecard>` element whose child `rt-text`/`rt-link` slots hold everything. Slot names use HYPHENS (`critics-score`, NOT `criticsScore`).

```python
import re
body = http_get(movie_url)  # e.g. https://www.rottentomatoes.com/m/dune_part_two

def slot_text(html, slot, tag="rt-text"):
    m = re.search(rf'<{tag}[^>]*slot="{slot}"[^>]*>(.*?)</{tag}>', html, re.S)
    return re.sub(r'\s+', ' ', m.group(1)).strip() if m else None

critics_score   = slot_text(body, "critics-score")                     # "92%"
audience_score  = slot_text(body, "audience-score")                    # "95%"
critics_reviews = slot_text(body, "critics-reviews", tag="rt-link")    # "466 Reviews"
audience_reviews= slot_text(body, "audience-reviews", tag="rt-link")   # "5,000+ Verified Ratings"
print(critics_score, audience_score, critics_reviews, audience_reviews)
```

### JSON-LD fallback (Tomatometer only)

The page embeds `<script type="application/ld+json">` with an `aggregateRating` block. It carries the critics Tomatometer only (name="Tomatometer") — NOT the audience score. Good as a robust backup for the critics number + review count:

```python
import re, json
body = http_get(movie_url)
ld = re.search(r'<script type="application/ld\+json">(.*?)</script>', body, re.S).group(1)
d = json.loads(ld)
ar = d["aggregateRating"]
print(d["name"], ar["ratingValue"], ar["ratingCount"])  # Dune: Part Two 92 466
```

## Verified result (Dune: Part Two, /m/dune_part_two, 2026-07-04)

| Field | Value | Source (all verified) |
|---|---|---|
| Tomatometer (critics) | 92% | `rt-text[slot="critics-score"]` and JSON-LD ratingValue |
| Critics review count | 466 Reviews | `rt-link[slot="critics-reviews"]` and JSON-LD ratingCount |
| Audience Score | 95% | `rt-text[slot="audience-score"]` |
| Audience ratings | 5,000+ Verified Ratings | `rt-link[slot="audience-reviews"]` |

## If you must use the cloud browser (new_tab + js)

Also verified working (Lexmount HK cloud IP is NOT blocked by RT). Same DOM — query the live `media-scorecard`:

```python
new_tab("https://www.rottentomatoes.com/m/dune_part_two"); wait_for_load(); wait(3)
r = js("""(() => {
  const sc = document.querySelector('media-scorecard');
  const t = (sel) => { const e=sc.querySelector(sel); return e? e.textContent.trim():null; };
  return {
    critics_score:   t('rt-text[slot="critics-score"]'),
    audience_score:  t('rt-text[slot="audience-score"]'),
    critics_reviews: t('rt-link[slot="critics-reviews"]'),
    audience_reviews:t('rt-link[slot="audience-reviews"]'),
  };
})()""")
print(r)  # {'critics_score':'92%','audience_score':'95%','critics_reviews':'466 Reviews','audience_reviews':'5,000+ Verified Ratings'}
```

## Gotchas

- **Prefer `http_get` (local IP) — it's the cheapest path and works fully.** RT does NOT block the China local IP nor the HK cloud IP. No need for the browser unless something changes. (Verified both paths on 2026-07-04.)
- **Slot names are hyphenated:** `slot="critics-score"`, `slot="audience-score"`, `slot="critics-reviews"`, `slot="audience-reviews"`. camelCase (`criticsScore`) returns nothing — a natural first-guess trap.
- **JSON-LD `aggregateRating` = Tomatometer only.** Its name is "Tomatometer"; it gives ratingValue/ratingCount for CRITICS. There is NO audience score in JSON-LD — read audience from the scorecard slots.
- **Audience "review count" is phrased as ratings**, e.g. "5,000+ Verified Ratings" (not "N Reviews"), and is bucketed/rounded for popular films. The critics side is an exact count ("466 Reviews").
- **`http_get` returns a raw string** (the HTML body), not a dict — don't call `.get()` on it. Regex/parse the string directly.
- **Search top-match selector:** the intended top result is the `<search-page-media-row>` link with `data-qa="info-name"`. Don't just grab the first `/m/` link on the page — the header/carousel contains unrelated promo movie links (e.g. `/m/minions_and_monsters`) that appear before the actual search results in source order.
- Movie URL slugs are stable and human-readable (`/m/dune_part_two`); if you already know the slug you can skip search entirely.
