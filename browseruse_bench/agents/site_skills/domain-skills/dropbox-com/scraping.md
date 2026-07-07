Field-tested on 2026-07-05 (dropbox.com) — pricing/plan comparison and help-center articles (file recovery, version history) are all plain server-rendered text; extract with `js("document.body.innerText")` over the cloud browser. `http_get` is IP-blocked.

## Do this first

Everything below runs through the cloud browser (`new_tab` + `js`). **`http_get` fails on dropbox.com** — the local IP gets `ConnectionReset` on the TLS handshake (verified). Always go `new_tab(url); wait_for_load(); wait(2-3); js(...)`.

Two verified entry points:
- **Pricing / plan comparison** → `https://www.dropbox.com/plans` — the full compare-plans table is in `document.body.innerText`.
- **Help articles (file recovery, version history, etc.)** → `https://help.dropbox.com/<category>/<slug>` — article body is in `document.body.innerText`.

### Task 1 — pricing: capacity / price / features across plans

```python
new_tab("https://www.dropbox.com/plans"); wait_for_load(); wait(3)
text = js("document.body.innerText")
print(text)   # contains every plan card + the full "Compare plans" feature table
```

Verified content in the innerText (2026-07-05, monthly billing default):
- **Plus** — `$9.99 / month`, 1 person, 2 TB, 30 days to restore deleted files, transfer up to 50 GB.
- **Standard** — `$15 / user / month`, starts at 3 TB team, 180 days to restore, transfer up to 100 GB.
- **Advanced** — `$24 / user / month`, starts at 15 TB team, 1 year to restore, transfer up to 100 GB, E2E encryption.
- **Basic** — Free, 2 GB.  **Enterprise** — "Contact us for pricing".

The page also renders a full `Compare plans` table in innerText: per-feature rows (Storage, Account recovery and version history, Restore deleted files, One-way transfer size, eSignature, admin/security features) with `-` marking absence. That single innerText dump answers "compare storage / price / key features across plans" without clicking anything.

Grab just the prices with a regex if you only need numbers:
```python
prices = js(r"""(()=>{const m=document.body.innerText.match(/\$[0-9.]+ ?\/ ?(?:user ?\/ ?)?month/g);return m?[...new Set(m)]:[]})()""")
# -> ['$9.99 / month', '$15 / user / month', '$24 / user / month']
```

### Task 2 — help center: file recovery & version history

Direct, verified article URLs (both extract cleanly):
```python
# File recovery
new_tab("https://help.dropbox.com/delete-restore/recover-deleted-files-folders"); wait_for_load(); wait(2)
print(js("document.body.innerText"))
# Key facts in body: deleted files kept 30 days (longer on Professional/team plans);
# restore via "Deleted files" in left sidebar -> select -> Restore; needs "Can edit" access.

# Version history
new_tab("https://help.dropbox.com/delete-restore/version-history-overview"); wait_for_load(); wait(2)
print(js("document.body.innerText"))
# Key facts: Basic/Plus/Family = 30 days; Professional/Essentials/Business/Standard = 180 days;
# Business Plus/Advanced/Enterprise = 365 days. View via file "…" -> Activity -> Version history.
```

### Finding any help article (search)

The help search box submits to `https://help.dropbox.com/search-results?q=<url-encoded>`. Results are JS-rendered — **wait ~4s** before reading, then pull article links:
```python
new_tab("https://help.dropbox.com/search-results?q=recover%20deleted%20files"); wait_for_load(); wait(4)
items = js(r"""(()=>{const a=[...document.querySelectorAll('a[href]')]
  .map(x=>({t:x.innerText.trim(),h:x.href}))
  .filter(x=>/help\.dropbox\.com\/[a-z-]+\/[a-z0-9-]+$/.test(x.h)&&x.t.length>4);
  const s=new Set(),o=[];for(const x of a){if(!s.has(x.h)){s.add(x.h);o.push(x);}}return o.slice(0,10);})()""")
# Verified: returns ~23 article {title, href} objects after the 4s wait.
```
Article slugs follow `help.dropbox.com/<category>/<slug>` (categories seen: delete-restore, sync, plans, security, billing, account-settings, share, storage-space, installs).

## Gotchas

- **`http_get` is dead on dropbox.com** — local-IP TLS handshake is reset (`[Errno 54] Connection reset by peer`), verified on `/plans`. Never use it here; always use the cloud browser (`new_tab`/`js`).
- **`www.dropbox.com` vs `help.dropbox.com`** — help articles live on the `help.` subdomain. `www.dropbox.com/features/...` deep links 404 (e.g. `/features/cloud-storage/recover-and-restore-files` returned an in-page 404 with title `Dropbox - 404`). Check `page_info()["title"]` for `404` / `Not found` before trusting a page.
- **Guessing help slugs is unreliable** — `sync/older-versions`, `sync/version-history-overview`, `sync/recover-older-versions` all 404; the real ones are under `delete-restore/`. Prefer the search-results step above over guessing, or use the two confirmed URLs.
- **Search results render async** — the search-results page returns only the category sidebar if you read too early (~2s). Wait ~4s. A `wait(6)` on it once caused a harness timeout (exit 144) — the page is heavy; keep the wait at ~4s and read once.
- **Yearly billing toggle** — the `/plans` page has "Billed monthly / Billed yearly" controls. Clicking the yearly label registered but the per-month `$` figures I regex'd didn't change in the same innerText snapshot (yearly may render as an annual total elsewhere in the DOM). The **monthly** figures ($9.99 / $15 / $24) are the reliable default; if you need annual pricing, read the full innerText after toggling rather than reusing the monthly-price regex.
- **Extraction method** — plain `document.body.innerText` beats CSS-selector scraping here: class names are hashed/obfuscated (`[class*="plan"]` found nothing useful), but the rendered text carries every plan name, price, storage number, and feature row including `-` for absent features.
