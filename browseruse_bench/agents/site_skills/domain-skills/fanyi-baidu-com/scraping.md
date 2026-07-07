Field-tested on 2026-07-04 — baidu translation extraction. The `/sug` JSON endpoint returns dictionary/idiom translations for words & known phrases with NO login, NO token, NO cookies (works from both local-CN IP and cloud-HK IP).

## Do this first — POST fanyi.baidu.com/sug (no auth, no token)

For a word, phrase, or known idiom, hit `/sug` directly. This is the fastest, most robust path and needs nothing (no cookies, no sign header). Verified from the LOCAL machine via `http_get`-style urllib (China IP) AND from the cloud browser via `fetch` (HK IP) — both return identical data.

```python
# Runs on LOCAL IP (China). No browser needed. Best default path.
import urllib.request, urllib.parse, json
def baidu_sug(q):
    data = urllib.parse.urlencode({"kw": q}).encode()
    req = urllib.request.Request(
        "https://fanyi.baidu.com/sug", data=data,
        headers={"User-Agent": "Mozilla/5.0",
                 "Content-Type": "application/x-www-form-urlencoded",
                 "Referer": "https://fanyi.baidu.com/"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read().decode())

j = baidu_sug("The early bird catches the worm")
# j == {"errno":0,"data":[
#   {"k":"the early bird catches the worm","v":"（谚）早起的鸟儿有虫吃，捷足者可先得"},
#   {"k":"The early bird catches the worm.","v":"先到灶头先得食；捷足先登"}], "logid":...}
result = j["data"][0]["v"] if j.get("data") else None
print(result)   # => （谚）早起的鸟儿有虫吃，捷足者可先得
```

Field: the translation is `data[i]["v"]`; the matched source key is `data[i]["k"]`. For an English word, `v` is the full dictionary gloss (part-of-speech + Chinese senses). For the task query "The early bird catches the worm" this returns the correct idiom translation directly.

### Same call from the cloud browser (fallback if local IP ever gets blocked)
```python
new_tab("https://fanyi.baidu.com/"); wait_for_load(); wait(2)
res = js("""
(async function(){
  var r = await fetch('https://fanyi.baidu.com/sug', {method:'POST',
    headers:{'Content-Type':'application/x-www-form-urlencoded'},
    body:'kw=' + encodeURIComponent('The early bird catches the worm')});
  return await r.text();
})()
""")
# res => {"errno":0,"data":[{"k":"the early bird catches the worm","v":"（谚）早起的鸟儿有虫吃，捷足者可先得"},...]}
```
Cloud (HK) IP is NOT blocked by fanyi.baidu.com — `/sug` returned full Chinese data over the cloud browser too.

## Gotchas

- **/sug is a DICTIONARY, not a general MT engine.** It only returns `data` for single words, set phrases, and known idioms. For an arbitrary free-form sentence it returns `{"errno":0,"data":[]}` (empty). Verified: `"I bought three apples at the store yesterday"` and `"hello world"` both returned `data:[]`. The task sentence works because it is a well-known proverb indexed in Baidu's dictionary.
- **The general translation API `/transapi` is token-gated (error 1022).** POST `from=en&to=zh&query=...` to `https://fanyi.baidu.com/transapi` returns `{"errno":1022,"errmsg":"访问出现异常，请刷新后重试"}` from local IP AND from page-context `fetch` with `credentials:'include'`. The app computes an anti-crawl `sign`/`Acs-Token` header that a bare fetch does not reproduce. Do not rely on `/transapi` for arbitrary sentences without reverse-engineering the sign.
- **The site now redirects to an AI-model SPA** at `https://fanyi.baidu.com/mtpe-individual/transText#/`. The input box is a `contenteditable` div (`textarea` count is 0; use `document.querySelectorAll('[contenteditable]')` — index 0 is the source, index 1 is the output). Typing into it and clicking the "AI翻译" button did NOT populate the output pane in an isolated (logged-out) cloud session and fired no `/transapi` request — the AI translate path appears to require login. So the DOM-scrape-the-output-pane approach is unreliable when not logged in; prefer `/sug` for dictionary-covered queries.
- **For arbitrary sentences (not covered by /sug), there is no verified no-login extraction path here.** Options if needed: (a) log in and drive the SPA UI, or (b) reverse the `/transapi` sign header. Neither was verified in this session. If a task requires translating a non-idiom sentence, this is a known blocker.
- No anti-bot wall on page load; the homepage loads fine over the cloud (HK) IP. Only the MT translate action is gated.
