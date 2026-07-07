Field-tested on 2026-07-04

Evernote's blog (evernote.com/blog) is a client-rendered Next.js page whose content comes from a **public Prismic CMS API** — hit that JSON API directly instead of scraping the DOM. No login, no anti-bot; both the local `http_get` path (China IP) and the cloud `js()` fetch reach the Prismic CDN unblocked.

## Do this first (blog articles / productivity tips)

The blog is powered by Prismic. There is NO WordPress REST API (site is Next.js, `/wp-json/*` → 404) and blog posts are NOT in the sitemaps. Use the Prismic content API `https://evernote.cdn.prismic.io/api/v2`.

`http_get` works fine here (Prismic CDN does not block the local China IP), so you can do the whole task without the cloud browser:

```python
import urllib.parse, json

# 1. Get the current master ref (changes when they publish; always fetch fresh)
api = json.loads(http_get('https://evernote.cdn.prismic.io/api/v2'))
ref = next(r['ref'] for r in api['refs'] if r.get('isMasterRef'))

def prismic(pred, page_size=100, order='[my.blogPost.publicationDate desc]'):
    url = ('https://evernote.cdn.prismic.io/api/v2/documents/search?q='
           + urllib.parse.quote(pred)
           + '&lang=en-us&pageSize=' + str(page_size)
           + '&ref=' + ref
           + '&orderings=' + urllib.parse.quote(order))
    return json.loads(http_get(url))

def plaintext(node):
    # Prismic rich-text -> flat string (recurse; grab every {"text": ...})
    if isinstance(node, list):  return ' '.join(plaintext(n) for n in node)
    if isinstance(node, dict):
        if isinstance(node.get('text'), str): return node['text']
        return ' '.join(plaintext(v) for v in node.values())
    return ''

# 2. Full-text search across all blog posts (this is the "site search")
j = prismic('[[at(document.type,"blogPost")][fulltext(document,"productivity tips")]]')
print(j['total_results_size'])                      # 29 hits for "productivity tips" (87 for just "productivity")
for d in j['results'][:10]:
    print(d['uid'], '|', d['data'].get('title'), '|', d['data'].get('publicationDate'))

# 3. Pull one article's full text + metadata
top  = j['results'][0]
d    = top['data']
title    = d.get('title')            # plain string, e.g. "AI-Powered Search: Helping you recall anything, instantly"
excerpt  = d.get('excerpt')          # plain string one-liner
metadesc = d.get('metaDescription')
author   = d.get('author')
date     = d.get('publicationDate')  # "YYYY-MM-DD"
body     = plaintext(d.get('body'))  # full article text (~2k-8k chars)
url      = 'https://evernote.com/blog/' + top['uid']
```

### List all posts (141 total, 2 pages of 100)
```python
j = prismic('[[at(document.type,"blogPost")]]', page_size=100)
# j['total_results_size'] == 141 ; j['total_pages'] == 2 (add &page=2 for the rest)
```

### Fetch one post by its slug/uid
```python
j = prismic('[[at(my.blogPost.uid,"templates-boost-productivity-at-work")]]')
d = j['results'][0]['data']
```

## Cloud-browser variant (identical, runs on the HK cloud IP)
Same API, same result — use if you ever need it from the browser context. This is exactly how the site itself loads posts (captured from the blog's "Load more" button):
```python
js("""
(async function(){
  var api = await (await fetch('https://evernote.cdn.prismic.io/api/v2')).json();
  var ref = api.refs.find(r=>r.isMasterRef).ref;
  var q = encodeURIComponent('[[at(document.type,"blogPost")][fulltext(document,"productivity")]]');
  var url = 'https://evernote.cdn.prismic.io/api/v2/documents/search?q='+q
          + '&lang=en-us&pageSize=100&ref='+ref
          + '&orderings='+encodeURIComponent('[my.blogPost.publicationDate desc]');
  var j = await (await fetch(url)).json();
  return {n:j.total_results_size, hits:j.results.map(d=>({uid:d.uid,title:d.data.title,date:d.data.publicationDate}))};
})()
""")
```

## Key document fields (type = `blogPost`)
- `uid` (top level, = URL slug → `evernote.com/blog/<uid>`)
- `data.title` — **plain string** (NOT rich-text; do not do `title[0].text`)
- `data.excerpt` — plain string summary
- `data.metaTitle`, `data.metaDescription` — SEO strings (sometimes differ from `title`)
- `data.author`, `data.publicationDate` (`YYYY-MM-DD`)
- `data.body` — rich-text array → use `plaintext()` above to flatten
- `data.tags` (top-level `tags[]`): the only tag in use is `"BlogUpdates"` (product-update posts). Most content/tips posts have `tags: []`.

## Gotchas
- **"Most popular" is NOT available.** Prismic exposes no view/like/popularity counter anywhere in the schema. The blog UI has no "trending" section either — it just lists `BlogUpdates`-tagged posts newest-first with a "Load more" button. To answer a "most popular" task, use a defensible proxy and state it: e.g. the newest matching post, or the top result of the relevance-ordered full-text search (drop the `&orderings=` param to let Prismic rank by relevance instead of date). Do not claim a real popularity metric.
- **The master `ref` expires** whenever Evernote republishes. Always fetch `/api/v2` first and read `isMasterRef`; a stale ref → HTTP 400/invalid results. (Ref seen on test day: `ajmaOBIAACcA-EvW`.)
- **No WordPress API.** Site is Next.js app-router; `evernote.com/wp-json/wp/v2/posts` returns a 404 Next error page. Don't waste time on it.
- **Blog posts are absent from sitemaps.** `sitemap.xml` → `sitemaps/core-en-us.xml` etc. contain only `/blog` (the index), not individual posts. The Prismic API is the only complete list.
- **DOM scraping the blog page is nearly useless** — the server HTML has only ~4 featured posts hard-coded; everything else is fetched client-side from Prismic. Go straight to the API.
- **`http_get()` returns a raw string**, not a response object — `json.loads()` it; don't call `.get()`/`.status` on it.
- The `?lang=en-us` filter matters: Prismic hosts de/es/fr/it/ja/ko/pt-br copies too. Use `en-us` for the English blog.
- Related but separate: the help center lives at `help.evernote.com` (different system, not Prismic). For "blog" tasks the Prismic API above is the right target.
