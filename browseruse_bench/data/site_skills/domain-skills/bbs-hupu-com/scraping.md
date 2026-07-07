Field-tested on 2026-07-04 (re-verified 2026-07-06)
bbs.hupu.com is a fully server-rendered forum: the NBA thread list (`/nba` = 篮球场) and every thread's replies are in the raw HTML, so you can scrape the whole task with plain HTTP + regex — no browser render, no JSON API, no login.

## Do this first (fastest path: local http_get + regex)

`http_get` from the LOCAL machine (China-mainland IP) works and returns full HTML — but ONLY if you pass a browser `User-Agent`. Without a UA the server replies `HTTP 405 Not Allowed`. This is the single most important gotcha.

Everything below runs inside one `./bh-lex` call. It does NOT need the cloud browser at all (http_get runs locally), but bh-lex is the harness that gives you `http_get`.

```python
import re, html

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36'}

def get(url):
    r = http_get(url, headers=UA)          # 405 if UA omitted!
    return r['body'] if isinstance(r, dict) else str(r)

# ---- STEP 1: NBA thread list. /nba is page 1; /nba-2, /nba-3... paginate ----
body = get("https://bbs.hupu.com/nba")

posts = []
for m in re.finditer(r'<li class="bbs-sl-web-post-body">(.*?)</li>', body, re.S):
    blk = m.group(1)
    t  = re.search(r'class="p-title"[^>]*>(.*?)</a>', blk, re.S)
    d  = re.search(r'class="post-datum">\s*(\d+)\s*/\s*(\d+)', blk)   # replies / reads
    hf = re.search(r'href="(/\d+\.html)"', blk)
    tm = re.search(r'class="post-time">(.*?)</div>', blk)
    if t and d:
        posts.append({
            'title':   html.unescape(re.sub('<.*?>', '', t.group(1)).strip()),
            'replies': int(d.group(1)),
            'reads':   int(d.group(2)),
            'url':     "https://bbs.hupu.com" + hf.group(1) if hf else None,
            'time':    tm.group(1).strip() if tm else None,   # e.g. "07-06 12:41" or "07-03 07:12"
        })

# Most replies overall:
top = max(posts, key=lambda p: p['replies'])
# Most replies posted TODAY (07-04 -> "07-04"): filter first, then max
today = [p for p in posts if (p['time'] or '').startswith('07-04')]
top_today = max(today, key=lambda p: p['replies']) if today else None
print(top['replies'], top['title'], top['url'])

# ---- STEP 2: open that thread, pull title + total replies + first 10 replies ----
th = get(top['url'])
title = re.search(r'<title>(.*?)</title>', th).group(1)          # "🐴 <title> -NBA-篮球场-虎扑社区"
# Each reply is one .post-reply-list-container block, in page order:
blocks = re.findall(r'<div class="post-reply-list-container">(.*?)</div>\s*</div>\s*</div>', th, re.S)  # rough; prefer the JS path below for reliable splitting
```

The list `<li>` fields are stable: `.post-title > a.p-title`, `.post-datum` = `"replies / reads"`, `.post-auth`, `.post-time`. All present in raw HTML.

## Reply extraction — prefer the browser for clean per-reply fields

Reply blocks are server-rendered but nested deep; regex-splitting the 40 reply items reliably is fiddly. If you already have a cloud session open, load the thread and use these **verified** selectors (tested on `/640517688.html`, all resolve):

```python
new_tab("https://bbs.hupu.com/640517688.html"); wait_for_load(); wait(3)
out = js(r'''
(function(){
  var items = document.querySelectorAll('.post-reply-list');   // one page = 40 replies
  var reps = [];
  for (var i=0; i<Math.min(10, items.length); i++){
    var el = items[i];
    var name    = el.querySelector('.post-reply-list-user-info-top-name');
    var time    = el.querySelector('.post-reply-list-user-info-top-time');
    var content = el.querySelector('.reply-list-wrapper .thread-content-detail')
               || el.querySelector('.reply-list-wrapper');
    var like    = el.querySelector('.todo-list-text');          // "亮了(481)"
    reps.push({
      author: name && name.innerText.trim(),
      time:   time && time.innerText.trim(),
      likes:  like && like.innerText.trim(),
      text:   content && content.innerText.replace(/\s+/g,' ').trim()
    });
  }
  // total reply count appears in body text as "NNN回复"
  var m = document.body.innerText.match(/(\d+)\s*回复/);
  return {total: m && m[0], replies: reps};
})()
''')
print(out)
```

This returns exactly `{author, time, likes, text}` per reply — clean, no username/timestamp bleed. Confirmed output e.g. `{"author":"kukuga","time":"2026-07-02 09:39:58","likes":"亮了(481)","text":"我个人感觉就比艾顿好个一丢丢"}`.

Verified reply selectors:
- reply item container: `.post-reply-list` (40 per page)
- author: `.post-reply-list-user-info-top-name`
- timestamp: `.post-reply-list-user-info-top-time`
- IP location: `.post-reply-list-user-info-user-location` (e.g. "发布于北京")
- clean body text: `.reply-list-wrapper .thread-content-detail`
- like count: `.todo-list-text` (format `亮了(NNN)`)
- total reply count: body text regex `(\d+)\s*回复` → matches `.post-datum` list value

## Pagination
- Thread list: `/nba` = page 1, `/nba-2`, `/nba-3` … each ~48 posts.
- Thread replies: one page shows ~40 replies. More pages exist for hot threads (append `-2` etc. to the thread URL pattern, or scroll the browser); the list `.post-datum` reply number is the authoritative total.

## Gotchas
- **UA is mandatory for http_get.** No `User-Agent` header → `HTTP 405 Not Allowed`. With a normal desktop Chrome UA, local http_get (China-mainland IP) returns full HTML — no need for the cloud browser for the LIST and thread HTML. Local IP is NOT blocked here.
- **My earlier wrong selector:** the list reply/read count is `.post-datum` (NOT `.bbs-sl-web-post-datum` — that class does not exist; a `querySelector` for it returns null and you get no counts).
- **`/nba` is 篮球场 = the curated "精华/原创" board** (only original + translated articles; free-chat threads are disallowed there). Its top rows are pinned/original posts, and the highest-reply thread may be several days old (e.g. a `07-03` thread out-replying today's). For "today's most replies" you MUST filter `post-time` by today's `MM-DD` prefix *before* taking the max, or you'll return a stale thread. All threads (incl. general discussion) still surface in this list with real reply counts.
- **`/all-nba` is a different page** — a 虎扑NBA news/hot-topic PORTAL using `.list-item` / `.topic-item` cards, NOT the `bbs-sl-web-post-body` forum list, and it has no `replies/reads` counts. Do not scrape it for the reply-count task; use `/nba`.
- **No usable免登录 JSON API from the page.** The site is server-rendered HTML with NO `__NEXT_DATA__` / `window.__INITIAL` blob (checked: absent). The mobile app endpoints under `games.mobileapi.hupu.com` / `bbs.mobileapi.hupu.com` fail with `Failed to fetch` from the page (CORS/blocked) — do not rely on them. Just parse the HTML.
- **Cloud egress is Hong Kong.** For hupu this does not matter (no geo-gating observed, no 403). Both paths (local http_get+UA, and cloud new_tab/js) return identical content. Reply/read counts drift by the minute (live), so numbers in this doc are illustrative.
- Thread URL form is `https://bbs.hupu.com/<digits>.html`. Reply text often contains quoted parent comments; `.reply-list-wrapper .thread-content-detail` strips the quote block and gives only the new comment text.
