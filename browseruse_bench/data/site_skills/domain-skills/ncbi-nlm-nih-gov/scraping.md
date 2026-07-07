# NCBI PubMed Central (PMC) — Scraping & Data Extraction

Field-tested on 2026-07-06 (re-verified 2026-07-06). `www.ncbi.nlm.nih.gov` / `pmc.ncbi.nlm.nih.gov` — PubMed Central full-text archive. **Never use the cloud browser for PMC search; use `http_get` + the NCBI E-utilities REST API (`db=pmc`).** No login, no API key needed (a free key raises the rate limit from 3 to 10 req/s).

Task this skill covers: *Search "CRISPR gene editing" on PubMed Central, filter review articles, list top results' title / authors / year.*

## Do this first

**ESearch (`db=pmc`) → ESummary. Two `http_get` calls, JSON responses, no XML parsing.** This is the whole task. Verified 2026-07-06.

```python
import json
# helpers (http_get, new_tab, js, ...) are pre-imported in the bh-lex runtime — do NOT `from helpers import`

# Step 1 — search PMC, filter to Review articles, relevance sort
es = json.loads(http_get(
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    "?db=pmc"
    "&term=CRISPR+gene+editing+AND+review[pt]"   # review[pt] == "Review" article-type filter
    "&retmax=5&retmode=json&sort=relevance"))
r = es['esearchresult']
ids = r['idlist']
print("total review hits:", r['count'])          # '116645' (string) on 2026-07-06
print("top uids:", ids)                            # PMC internal uids (numeric, no 'PMC' prefix)

# Step 2 — one ESummary call for all uids: title, authors, year, journal, PMCID/PMID/DOI
su = json.loads(http_get(
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
    f"?db=pmc&id={','.join(ids)}&retmode=json"))
res = su['result']
for uid in res['uids']:
    a = res[uid]
    aid = {x['idtype']: x['value'] for x in a.get('articleids', [])}
    year = (a.get('pubdate') or a.get('epubdate') or '')[:4]
    print("---")
    print("title  :", a['title'])
    print("authors:", ", ".join(x['name'] for x in a.get('authors', [])))  # 'Last II' form
    print("year   :", year, "| journal:", a.get('source'))
    print("pmcid  :", aid.get('pmcid'), "| pmid:", aid.get('pmid'), "| doi:", aid.get('doi'))
```

Confirmed top result (2026-07-06):
```
title  : Applications of Clustered Regularly Interspaced Short Palindromic Repeats (CRISPR) as a Genetic Scalpel for the Treatment of Cancer: A Translational Narrative Review.
authors: Mondal R, Brahmbhatt N, Sandhu SK, Shah H, Vashi M
year   : 2023 | journal: Cureus
pmcid  : PMC10767422 | pmid: 38186450 | doi: 10.7759/cureus.50031
```

## Field reference (ESummary `db=pmc`, per-article dict)

| Field | Meaning |
|---|---|
| `title` | Full article title (trailing period included) |
| `authors` | List of `{'name': 'Last II'}` — abbreviated names, in order |
| `pubdate` | e.g. `'2023 Dec'` or `'2021 Jul 20'` — slice `[:4]` for the year |
| `epubdate` | e-pub-ahead-of-print date; use as year fallback when `pubdate` is empty |
| `source` | Abbreviated journal name (e.g. `'Cureus'`) |
| `fulljournalname` | Full journal name |
| `articleids` | List of `{idtype,value}`; map to a dict to get `pmcid`, `pmid`, `doi`, `pii` |

The `id` you pass to ESummary is the **numeric PMC uid** returned by ESearch (no `PMC` prefix). The human `PMCID` string (`PMC10767422`) is inside `articleids`.

## Search syntax (append to `term=`, URL-encode spaces as `+`)

```
review[pt]                    Review article-type filter  (== "review"[Publication Type]) — 116645 hits with CRISPR
CRISPR gene editing           free text; auto-expands via MeSH (see querytranslation in response)
"gene editing"[MeSH Terms]    MeSH controlled vocabulary
2024[pdat]                    publication year
Nature[Journal]               journal name
```
Booleans `AND`/`OR`/`NOT` (uppercase). Sort: `&sort=relevance` (default) or `&sort=pub+date` (newest first).
Pagination: `&retstart=0&retmax=20`. Total count is `esearchresult.count` (a **string**).

## Article full text / abstract (optional, when ESummary isn't enough)

- HTML page: `https://pmc.ncbi.nlm.nih.gov/articles/PMC10767422/` — reachable via **local** `http_get` (163 KB HTML, `<title>` = article title). Verified 2026-07-06.
- Structured XML (abstract, MeSH, body): `efetch.fcgi?db=pmc&id=<numeric-uid>&retmode=xml`. Verified returns full JATS XML.

## Gotchas

- **The cloud browser (HK exit IP) is HARD-BLOCKED by NCBI.** `new_tab("https://www.ncbi.nlm.nih.gov/pmc/?term=...")` redirects to `misuse.ncbi.nlm.nih.gov/error/abuse.shtml` → "Access Denied / temporarily blocked due to possible misuse". Do NOT scrape the PMC web UI through the browser. E-utilities + local `http_get` is the only reliable path. Verified failing 2026-07-06.
- **`review[Filter]` does NOT exist for `db=pmc`** — it returns `count:0` with `phrasesnotfound:["review"]`. Use `review[pt]` (publication type). `systematic[sb]` also does not exist for PMC.
- **ESummary `authors` are abbreviated** (`'Mondal R'`), not full names. For full given names use EFetch XML — but note EFetch JATS XML also embeds reference-list authors under the same `<surname>/<given-names>` tags, so a naive regex over the whole doc grabs cited-reference authors, not the article's. For the title/authors/year task, ESummary's abbreviated names are the clean, correct source; prefer it.
- **`count` is a string**, not int — cast if you compare.
- E-utilities and `pmc.ncbi.nlm.nih.gov` article pages are **not** blocked from the local (China-mainland) IP; only the cloud HK IP triggers the abuse block. So this whole skill runs over `http_get`, which is local-IP.
- Etiquette: keep under 3 req/s without a key; add `&api_key=...` for 10 req/s. Optionally add `&tool=...&email=...`.
