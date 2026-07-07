# stcn.com (证券时报网) — scraping

Field-tested on 2026-07-06 (re-verified 2026-07-06) — 证券时报官方网站；股市/行情新闻走「投资」频道，正文页服务端渲染、无反爬、本地 http_get 直取。

## 站点结构（实测）
- 顶部导航没有字面「股市」频道。**股市新闻 = 「投资」频道** `https://www.stcn.com/article/list/investment.html`（覆盖 A股/港股/涨停/解禁/板块等股市内容）。
- 相关频道：新闻 `article/list/xw.html`、快讯 `article/list/kx.html`、行情总貌 `article/list/hq.html`、金融 `article/list/finance.html`。
- 文章详情页统一模式：`https://www.stcn.com/article/detail/{id}.html`（id 为数字）。
- 行情/个股页：`https://www.stcn.com/quotes/index.html?stock_code=sz300750`（不是新闻任务需要）。

## Do this first — 本地 http_get 直取（最优，无需云浏览器）
`http_get` 跑在本地中国大陆 IP，stcn.com **对本地 IP 无任何反爬**（HTTP 200，正文服务端渲染在 HTML 里）。频道页和详情页都能一次拿到，不用 new_tab/js。

注意：本 harness 的 `http_get(url)` **返回字符串（HTML 全文），不是 dict**，直接对返回值做 regex。

### 1) 取频道头条（标题=列表第一条）
```python
import re
html = http_get("https://www.stcn.com/article/list/investment.html")
# 列表里每篇文章有 标题链接 + 摘要，取第一条即头条
links = re.findall(r'href="(/article/detail/\d+\.html)"[^>]*>\s*([^<]{4,80}?)\s*<', html)
# links[0] = ('/article/detail/3999740.html', '高德AI专车升级：标准化服务进化为个性化服务')
headline_path, headline_title = links[0]
headline_url = "https://www.stcn.com" + headline_path
print(headline_title, headline_url)
```

### 2) 取详情页 标题 + 正文核心内容
```python
import re
html = http_get(headline_url)   # 或任意 https://www.stcn.com/article/detail/{id}.html

# 标题：用 <div class="detail-title"> 或 <title>（两者都干净、无 emoji）
m = re.search(r'class="detail-title"[^>]*>\s*([^<]{4,120}?)\s*<', html)
title = m.group(1).strip() if m else \
        (re.search(r'<title>(.*?)</title>', html, re.S).group(1).strip())

# 正文：<div class="detail-content"> 块，去标签
dc = re.search(r'class="detail-content"[^>]*>(.*?)(?:<div class="share|<div class="detail-)', html, re.S)
body = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', dc.group(1))).strip() if dc else ''
print(title)
print(body[:500])   # 核心内容
```
实测 3999740：title=「高德AI专车升级：标准化服务进化为个性化服务」，body≈1200字，干净可读。

## 备份路径 — 云浏览器 new_tab + js（本地 IP 万一被封时）
云出口是香港 IP，但 stcn.com 香港 IP 也正常（未见封锁）。DOM 提取：
```python
new_tab("https://www.stcn.com/article/detail/3999740.html"); wait_for_load(); wait(2)
res = js("""(function(){
  var t=document.querySelector('.detail-title, h1');
  var c=document.querySelector('.detail-content');
  return JSON.stringify({
    title:(t?t.textContent:document.title).replace(/^🐴\\s*/,'').trim(),
    body:c?c.textContent.replace(/\\s+/g,' ').trim():''
  });
})()""")
```
频道头条按位置取（字号最大/最靠上的 `/article/detail/` 链接即头条），实测头条 fontSize=20，其余=16。

## Gotchas
- **没有「股市」频道**：任务里的「股市频道」实际对应「投资」频道 `article/list/investment.html`。若要更偏行情数据用 `article/list/hq.html`。
- **`http_get` 返回 str 不是 dict**：直接 regex，别 `.get('text')`（会 AttributeError）。
- **emoji 前缀陷阱**：云 DOM 里 `document.title` 和有时 h1 会带站点注入的「🐴」前缀；`<div class="detail-title">` 和服务端 `<title>` 标签**不带** emoji，优先用它们。本地 http_get 的 `<title>` 也干净。
- **列表页第一条 = 头条**：`investment.html` 服务端 HTML 里 `/article/detail/` 链接按版面顺序排列，`links[0]` 就是头条标题；每篇会出现标题链接+摘要链接两条（同一 id），regex 已限定 `[^<]{4,80}` 只抓标题文本。
- **详情页无 `<h1>`**：正文标题在 `<div class="detail-title">`，不要找 h1（本地 HTML 里没有）。
- **正文发布时间**：文章真实发布时间在正文区（实测「06-30 20:24」），页面另有一个页头当前时间戳 `\d{4}-\d{2}-\d{2} \d{2}:\d{2}`（如 2026-07-06 13:01），**别把页头时间误当发布时间**。
- **未发现免登录 JSON 接口**：频道页/详情页均为服务端渲染 HTML，正则直取即可，无需找 API。
