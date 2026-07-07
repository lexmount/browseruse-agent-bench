Field-tested on 2026-07-04 (retested 2026-07-05) — khanacademy.org: scrape a course's units / lessons / skills counts from the React-rendered course page via the Hong Kong cloud browser; the local China IP is hard-blocked by a Fastly "Client Challenge".

## Do this first (verified path)

Go straight to the course's canonical URL `/math/<course-slug>` in the cloud browser, wait for the React app to render, then read the counts from the rendered DOM. Do NOT rely on search to reach a course, and do NOT rely on raw HTML — the unit/lesson list is injected client-side and is absent from the initial HTML.

For the benchmark task (Differential Calculus): slug is `differential-calculus`.

```python
new_tab("https://www.khanacademy.org/math/differential-calculus"); wait_for_load(); wait(3)

info = js(r"""
(() => {
  const out = {};
  // UNITS: h2 headings shaped "Unit N: ..."
  const unitHeads = [...document.querySelectorAll('h2')]
    .map(h=>h.innerText.trim())
    .filter(t=>/^Unit\s+\d+:/.test(t));
  out.unitCount = unitHeads.length;
  out.units = unitHeads;

  // LESSONS: h3 whose associated <a> href contains the course slug
  // (this excludes footer h3 like "About","Contact","Cookie List")
  const slug = 'differential-calculus';
  const lessons = [];
  for (const h of document.querySelectorAll('h3')) {
    const a = h.closest('a') || h.querySelector('a') || (h.parentElement && h.parentElement.querySelector('a'));
    const href = a ? a.getAttribute('href') : '';
    if (href && href.includes(slug)) lessons.push(h.innerText.trim());
  }
  out.lessonCount = lessons.length;

  // SKILLS: the summary line Khan renders near the top, e.g. "6 units · 117 skills"
  const bt = document.body.innerText;
  out.summary = (bt.match(/\d+\s+units?[\s\S]{0,40}?\d+\s+skills?/i) || [])[0] || null;
  return out;
})()
""")
import json
print(json.dumps(info, indent=2, ensure_ascii=False))
```

Verified output for Differential Calculus (2026-07-05):
- `unitCount` = **6**  (Unit 1: Limits and continuity … Unit 6: Parametric equations, polar coordinates, and vector-valued functions)
- `lessonCount` = **67**  (h3 lesson headings linked into the course, footer items excluded)
- `summary` = **"6 UNITS · 117 SKILLS"**  ("lessons" per the task = the 67 lesson rows; "skills" = 117 is Khan's own summary metric — report both, they answer different phrasings)

## Search (only if you don't know the slug)

The search page works and is typed, but is paginated/limited — a target COURSE may not appear on page 1. Use it to discover slugs, then switch to the direct URL above.

```python
new_tab("https://www.khanacademy.org/search?page_search_query=calculus"); wait_for_load(); wait(4)
results = js(r"""
[...document.querySelectorAll('a[href]')]
  .map(a=>({label:a.innerText.trim().replace(/\n/g,' | '), href:a.getAttribute('href')}))
  .filter(x => /^COURSE/i.test(x.label) && x.href && x.href.startsWith('/'))
""")
print(results)  # each: {label:"COURSE | <NAME> | <title> | <desc>", href:"/math/<slug>"}
```
Search-URL pattern: `https://www.khanacademy.org/search?page_search_query=<query>`. Result cards are prefixed by type: `COURSE`, `EXERCISE`, `VIDEO`, `ARTICLE`, etc. The `COURSE` href is the `/math/<slug>` you then navigate to.

## Gotchas

- **Local http_get is BLOCKED (China IP).** `http_get("https://www.khanacademy.org/...")` returns a ~3KB Fastly bot page: `<title>Client Challenge</title>` with `/_fs-ch-.../` asset paths. It is NOT the real page. Do not scrape from local http_get — always use the cloud browser (`new_tab`/`js`).
- **Cloud IP (Hong Kong) is fine.** `new_tab` renders the full page (200, no challenge). Khan Academy is not geo-restricted for HK, no login required.
- **Data is React-rendered, not in raw HTML.** A cloud-IP `fetch()` of the course URL returns 200 and ~340KB of HTML, but `Unit 1` / `6 units` are ABSENT from that HTML (client-side rendered). So: raw-HTML regex scraping fails; you MUST read the rendered DOM after `new_tab` + `wait_for_load()` + `wait(3)`.
- **Search does not surface every course on page 1.** Searching "calculus" returned MULTIVARIABLE CALCULUS as the only COURSE card; Differential Calculus was not on page 1. Don't conclude a course is missing — go to `/math/<slug>` directly.
- **Units vs. lessons vs. skills are three different numbers.** Units = the `Unit N:` h2 headings (6). Lessons = the h3 lesson rows linked into the course (67). Skills = Khan's own top-of-page summary count (117). The task asks for "units and lessons/practice" → answer units=6, lessons=67, and mention the 117-skills summary line for completeness.
- **Footer h3 pollution.** The page has trailing h3s ("About", "Contact", "Download our apps", "Courses", "Manage Consent Preferences", "Cookie List"). The href-contains-slug filter in the snippet above already excludes them; a naive `document.querySelectorAll('h3').length` over-counts by 6.
- **Internal GraphQL API not usable off the shelf.** `GET /api/internal/graphql/ContentForPath` returns HTTP 400 without the right POST body/variables/persisted-query hash; not worth reverse-engineering when the rendered DOM gives clean counts. No simpler public JSON course endpoint found.
