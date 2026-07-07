Field-tested on 2026-07-04 (re-verified 2026-07-06)

Bank of China (boc.cn) foreign-exchange rate board (外汇牌价): a single static HTML table at one fixed URL lists ~29 currencies with all five bank rates. No login, no JSON API needed, no anti-bot.

## Do this first

Page: `https://www.boc.cn/sourcedb/whpj/` — a plain server-rendered HTML table. USD (美元) and all major currencies are on page 1, so no pagination is needed for the common task.

**Best path: local `http_get` (China-IP).** boc.cn is fully reachable from the China-mainland local IP and returns the same rates as the cloud browser. It is simpler and needs no cloud session. Column meanings (per 100 units of foreign currency, in CNY):

| col | field | Chinese |
|-----|-------|---------|
| 0 | currency name | 货币名称 |
| 1 | **spot buy / 现汇买入价** | ← "100 USD 现汇买入" uses this |
| 2 | cash buy | 现钞买入价 |
| 3 | spot sell | 现汇卖出价 |
| 4 | cash sell | 现钞卖出价 |
| 5 | BOC reference | 中行折算价 |
| 6 | publish datetime | 发布日期/时间 |

`现汇买入价` (col 1) is the "spot exchange buy" rate — the price at which BOC buys foreign currency spot from you. It is per 100 units, so 100 USD → CNY is simply the raw number. On 2026-07-06, USD 现汇买入价 = **677.65**, i.e. 100 USD = 677.65 CNY.

```python
import re
body = http_get("https://www.boc.cn/sourcedb/whpj/")          # runs on local China IP
rows = re.findall(r'<tr[^>]*>(.*?)</tr>', body, re.S)
rates = {}
for row in rows:
    cells = [re.sub(r'<[^>]+>', '', c).strip()
             for c in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)]
    if len(cells) >= 6 and re.match(r'^\d', cells[1] or ''):
        rates[cells[0]] = cells          # keyed by Chinese currency name

usd = rates['美元']
spot_buy = float(usd[1])                  # 现汇买入价, per 100 USD
print("100 USD (现汇买入价) =", spot_buy, "CNY")   # -> 677.65
```

## Alternative: cloud browser (new_tab + js)

Works equally well; use it if the local IP ever gets blocked. Note the cloud IP is Hong Kong but boc.cn serves identical rates.

```python
new_tab("https://www.boc.cn/sourcedb/whpj/"); wait_for_load(); wait(2)
cell = js("""
(function(){
  var tables = document.querySelectorAll('table');
  for (var k=0;k<tables.length;k++){
    var rows = tables[k].querySelectorAll('tr');
    if (!rows.length) continue;
    if (rows[0].innerText.indexOf('现汇买入价')<0) continue;   // the rate table
    for (var i=1;i<rows.length;i++){
      var c = rows[i].querySelectorAll('td');
      if (c.length && c[0].innerText.trim()==='美元') return c[1].innerText.trim();
    }
  }
  return null;
})()
""")
print("USD 现汇买入价 =", cell)   # -> "677.65"
```
In the live DOM the rate table is `document.querySelectorAll('table')[1]` and each USD row has 8 `<td>`s: `['美元','677.65','677.65','680.5','680.5','680.66','2026/07/06','12:48:48']` (col indices match the table above).

## Gotchas

- **`http_get` returns a plain `str`, not a response object.** Don't call `.get('text')` / `.status` on it — that raises `AttributeError: 'str' object has no attribute 'get'`. It's already the HTML body.
- **Raw HTML vs live DOM differ by one column.** In `http_get` HTML the publish date+time are merged into one `<td>` (`'2026/07/06 12:48:48'`) and there's a trailing duplicate time cell, so a USD row is `['美元','677.65','677.65','680.5','680.5','680.66','2026/07/06 12:48:48','12:48:48']`. Cols 0–5 (the rates you care about) are identical to the DOM version — only col 6+ shifts. Always index rates by 0–5, not from the end.
- **`<tr>` tags carry attributes.** Match rows with `r'<tr[^>]*>'`, not `r'<tr>'` — the bare form returns zero rows.
- **Filter data rows by `re.match(r'^\d', cells[1])`.** The page has header rows and non-rate tables; requiring col 1 to start with a digit cleanly isolates the 29 currency rows.
- **Units are per 100 foreign-currency units.** The number for USD (677.65) already IS "100 USD in CNY" — do not multiply by 100 again. For a per-1-unit rate divide by 100.
- **`现汇` vs `现钞`:** 现汇 (spot/wire, cols 1&3) is for electronic funds; 现钞 (cash, cols 2&4) for physical banknotes. The USD task asks for 现汇买入价 = col 1.
- No search box is needed for this task; the page's `sword` search input is for site-wide content search, not for the rate table.
