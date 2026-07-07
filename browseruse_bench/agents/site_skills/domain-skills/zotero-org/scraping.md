Field-tested on 2026-07-04
zotero.org support docs are a static DokuWiki-style tree at `https://www.zotero.org/support/<slug>` — no login, no anti-bot; extract the article body from `#plugin_page` (cloud js) or the raw HTML (local http_get). Both IP paths (China local + HK cloud) work fine here.

## Do this first
Go straight to the doc by its URL slug — there is NO working site search. For "Adding items to your library" the slug is `adding_items_to_zotero`. To find any other doc's slug, read the left-sidebar nav that appears on every `/support/` page (slug map below).

```
# CLOUD path (cleanest text — use this by default)
goto_url("https://www.zotero.org/support/adding_items_to_zotero"); wait_for_load(); wait(2)
data = js("""(() => {
  const main = document.querySelector('#plugin_page');   // article container, verified present
  const heads = [...main.querySelectorAll('h1,h2,h3')].map(h => h.tagName+': '+h.textContent.trim().replace(/#$/,''));
  const txt = (main.innerText||'').replace(/\\n{3,}/g,'\\n\\n');
  return {heads, len: txt.length, text: txt};
})()""")
print(data['heads'])          # section outline
print(data['text'][:4000])    # article body to summarize
```

For "Adding Items to Zotero" this returns TITLE "Adding Items to Zotero#", ~15.7k chars of body, and this section outline (verified):
Via your web browser (Generic Webpages / PDFs / Multiple Results / Saving to a Specific Collection or Library / Data Quality and Choosing a Translator) · Add Item by Identifier · Adding PDFs and Other Files (Standalone Attachments and Parent Items) · Saving Webpages · Importing from Other Tools · Large-Scale Imports from Databases · Manually Adding Items · Editing Items · Verify and Edit Your Records.
Summary gist: the Zotero Connector browser extension's save button is the primary, highest-quality way to add items; other paths are Add-by-Identifier (ISBN/DOI/PMID/arXiv), dragging in PDFs (auto-retrieves metadata), saving webpages as snapshots, importing from other reference managers, and manual entry via the green "New Item" button — then verify/edit the fields.

## Slug map (read off the sidebar; each is `/support/<slug>`)
adding_items_to_zotero (Adding Items) · attaching_files (Adding Files) · retrieve_pdf_metadata · moving_to_zotero (Importing from Other Tools) · collections_and_tags · searching · pdf_reader · notes · related · duplicate_detection · creating_bibliographies · word_processor_integration · styles · sync (Data and File Syncing) · groups · my_publications · preferences · quick_start_guide · getting_help · kb (Knowledge Base) · installation · frequently_asked_questions · changelog.

To dump the whole slug map from any support page:
```
nav = js("""(() => {
  const seen=new Set(), out=[];
  for(const a of document.querySelectorAll('a')){
    if(/\\/support\\/[a-z_]+$/.test(a.href) && a.textContent.trim() && !seen.has(a.href)){
      seen.add(a.href); out.push(a.textContent.trim()+' -> '+a.href.split('/support/')[1]);
    }
  } return out;
})()""")
print("\\n".join(nav))
```

## Backup: local http_get (China IP, verified working)
http_get is NOT blocked here (returns full 56 KB HTML). Use if the cloud browser is unavailable. Isolate the article and strip tags:
```
import re, html
r = http_get("https://www.zotero.org/support/adding_items_to_zotero")   # ~56KB, contains "Adding Items to Zotero"
m = re.search(r'id="plugin_page"(.*?)<div id="dokuwiki__footer"', r, re.S)
seg = m.group(1) if m else r
txt = re.sub(r'\\s+',' ', html.unescape(re.sub(r'<[^>]+>',' ', seg))).strip()   # ~36k chars body
```

## Gotchas
- NO working search. The header shows a search input (placeholder "Search documentation…") but it is a JS widget with no plain URL endpoint; the DokuWiki `?do=search&q=...` URL returns an empty page (tested — zero result links). Navigate by slug instead; get slugs from the sidebar nav.
- http_get raw HTML carries inline JS. Regex heading extraction from the raw HTML picks up one bogus H3 (a JS template string `' + titleHtml + '`) before the real H1. The cloud `#plugin_page` innerText path has none of this noise — prefer it for clean text.
- Section headings end with a literal `#` (anchor char); strip trailing `#` when summarizing.
- No auth, no rate-limiting or captcha observed on `/support/` pages via either IP path. Both China-local http_get and HK-cloud js render identical article text, so no region skew for docs.
