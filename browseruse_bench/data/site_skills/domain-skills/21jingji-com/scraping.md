Field-tested on 2026-07-04 (re-verified 2026-07-06)
21jingji.com (21世纪经济报道/21财经) — 站内文章搜索：用移动站搜索页拿结果列表（标题+日期+链接），再进详情页取来源+精确时间。

## Do this first (最优路径：云浏览器 new_tab + js DOM 提取)

搜索结果由 JS/AJAX 渲染，原始 HTML 里 `#data_list` 是空的，**必须用云浏览器渲染后读 DOM**（不能只 fetch HTML，也不能走本地 http_get 直接拿列表）。结果按发布日期严格倒序（最新在最前），所以第一条 `.news.search_list > a` 就是"最新一篇"。

关键词用 param `k`，搜索页在移动站：`https://m.21jingji.com/channel/search/?k=<urlencoded关键词>`

```python
import urllib.parse, json
kw = urllib.parse.quote("能源转型")   # 换成你的关键词
new_tab(f"https://m.21jingji.com/channel/search/?k={kw}"); wait_for_load(); wait(3)

# 1) 取最新一篇（列表已按日期倒序，第一条即最新）
top = js("""(() => {
  const a = document.querySelector('.news.search_list > a');
  if(!a) return null;
  return {
    title: a.querySelector('h2').innerText.trim(),
    date:  a.querySelector('span').innerText.trim(),   // 仅到日 YYYY-MM-DD
    href:  a.getAttribute('href')
  };
})()""")
print(json.dumps(top, ensure_ascii=False))

# 2) 进详情页取 来源 + 精确时间（列表页没有来源字段）
goto_url(top['href']); wait_for_load(); wait(2)
detail = js("""(() => ({
  title:  document.querySelector('h1').innerText.trim(),
  source: document.querySelector('.newsInfo')?.innerText.trim(),   // 例: 人民日报
  time:   document.querySelector('.newsDate')?.innerText.trim()    // 例: 2026-07-02 13:40
}))()""")
print(json.dumps(detail, ensure_ascii=False))
```

实测输出（关键词"能源转型"，2026-07-04）：
- 最新一篇：`为全球能源转型提供更高效的路径`
- 来源：`人民日报`  发布时间：`2026-07-02 13:40`  链接：`https://m.21jingji.com/article/20260702/herald/04cf8bd21dcfdec7b6d2e2bb075f2bc7.html`

## 取整页列表（20 条/页）

```python
rows = js("""(() => {
  const list = document.querySelector('.news.search_list');
  return [...list.querySelectorAll(':scope > a')].map(a => ({
    title: a.querySelector('h2')?.innerText.trim(),
    date:  a.querySelector('span')?.innerText.trim(),
    href:  a.getAttribute('href')
  }));
})()""")
```

已验证选择器（移动站 m.21jingji.com，2026-07-04 实测）：
- 结果列表容器：`.news.search_list`，每条结果是它的直接子 `<a>`（每页 20 条）
- 列表项标题：`h2`（命中词被 `<font color="red">` 包裹，`.innerText` 已是纯文本）
- 列表项日期：`span`（仅 `YYYY-MM-DD`，无时分）
- 详情页标题：`h1`
- 详情页来源：`.newsInfo`（如"人民日报"；文末通常还有一句 `（来源：XXX）`）
- 详情页精确时间：`.newsDate`（`YYYY-MM-DD HH:MM`）
- 详情页 URL 里含发布日期：`/article/YYYYMMDD/.../<hash>.html`

## Gotchas

- **原始 HTML 无结果**：`fetch(搜索页).text()` 里 `#data_list` 是空 div（`load_more` AJAX 填充）。所以本地 `http_get` 直接抓搜索页拿不到列表，务必用云浏览器 `new_tab`+`wait`+`js` 读渲染后 DOM。
- **JSON 搜索接口存在但被加密，不可直接用**：真实接口是 `https://so.21jingji.com/elk/search/searchWeb/?keywords=<kw>&page=N`（GET，返回 `application/json`）。但响应 `{"status":1,"page":1,"list":"..."}` 里 `list` 是 **crypto-js 加密的密文**（页面加载了 crypto-js 在前端解密），没有密钥无法直接解析。已实测：直接导航到该接口 URL 能看到密文 JSON，但不可用。**结论：不要走这个 API，走 DOM。**
- **CORS**：在 `m.21jingji.com` 页面里 `fetch("https://so.21jingji.com/...")` 会 `TypeError: Failed to fetch`（跨域）。只能整页导航过去，或直接走 DOM 路径。
- **两个可能返回空的接口**（探测记录，别用）：`/plus/channel/search/formatList`（POST，需 `list` 参数，只传 `k` 返回 `{"status":0,"list":[]}`）；`/api/search`、`/elk/search/searchWeb/`（在 m 站相对路径下）均 404。
- **排序**：结果严格按发布日期倒序，第一条即最新；同日多篇时相互间顺序未定，但都在最前。用列表项 `span` 的日期即可确认"最新"。
- **来源只在详情页**：搜索列表项没有"来源"字段（只有标题/摘要/日期），要来源必须点进详情页读 `.newsInfo`。
- **桌面站搜索表单** action 指向的正是移动站 `https://m.21jingji.com/channel/search/`（param `k`），所以直接用移动站 URL 最省事。
- 反爬：未遇到验证码/封禁。云出口 IP（香港）可正常访问，无地区化偏差。本任务全程走云浏览器 DOM，未依赖本地 http_get。
