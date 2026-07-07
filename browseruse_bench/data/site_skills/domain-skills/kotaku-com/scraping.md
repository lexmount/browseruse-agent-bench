Field-tested on 2026-07-05 — kotaku.com (G/O Media / WordPress gaming news) is behind a site-wide Cloudflare "Just a moment..." non-interactive challenge that BLOCKS both available IP paths; no免登录 extraction path was reachable this session.

## Status: BLOCKED (no verified extraction path)

Every kotaku.com path — homepage, `/search?q=`, `/wp-json/`, `/rss`, `/feed`, `/sitemap.xml`, even `/robots.txt` — returns Cloudflare's managed challenge. The challenge is the JS-only *non-interactive* variant: it never renders a Turnstile checkbox iframe to click, and it silently loops (cookie `cf_chl_rc_ni` keeps incrementing) instead of granting a `cf_clearance`. It cannot be solved by waiting or clicking.

## What was tried (all failed, both IP paths)

Task target: "Search 'indie game development' on Kotaku, find most recent feature article, record title/author/date." None of the below reached any article data.

1. Cloud browser (Lexmount, HK datacenter IP) — `new_tab` / `goto_url`:
   - `https://kotaku.com/` → title stays `🐴 Just a moment...`, body 258 chars, waited up to ~50s across reloads. Never clears.
   - `https://kotaku.com/search?q=indie+game+development` → same challenge.
   - `/rss`, `/feed`, `/sitemap.xml`, `/robots.txt` → all challenge.
   - In-page same-origin `fetch('/wp-json/wp/v2/posts?search=...')` → HTTP 403, `content-type: text/html`, body is the CF challenge HTML (no clearance cookie set, so even JSON endpoints are gated).
   - DOM check: `document.querySelectorAll('iframe')` is empty and there is no `[id*=turnstile]` widget — nothing to click. `document.cookie` = only `cf_chl_rc_ni=2` (retry counter), never `cf_clearance`.

2. Local `http_get` (China-mainland local machine IP, does NOT go through the cloud browser):
   - `https://kotaku.com/` → HTTP 403 Forbidden.
   - `/wp-json/`, `/wp-json/wp/v2/posts?search=...` → 403.
   - `/rss`, `/sitemap.xml`, `/robots.txt` → 403.
   - Tried desktop Chrome UA (Win + Mac) — no difference.

## Gotchas / notes for future attempts

- Kotaku is a **WordPress** site, so the intended免登录 API would be the WP REST route `GET /wp-json/wp/v2/posts?search=<query>&per_page=N&orderby=date` (returns JSON with `title.rendered`, `date`, and an `author` id you resolve via `/wp-json/wp/v2/users/<id>`). This is the right path to retry **if the CF challenge ever lets a request through** — but this session it was 403/challenged on every call from both IPs.
- The block is **site-wide** (robots.txt itself is challenged), which points to Cloudflare "Under Attack" / bot-fight mode rather than a per-path WAF rule. Waiting does not help; the non-interactive challenge just retries.
- To get past this you would need an IP that Cloudflare trusts for this zone (e.g. a residential/clean egress) or a browser session that has already earned a `cf_clearance` cookie. Neither the HK cloud egress nor the CN local IP qualified today.
- Do NOT report a title/author/date for the task — none was obtainable; any value would be fabricated.
