Field-tested on 2026-07-06 (re-verified 2026-07-06) — 网易新闻站内搜索(news.163.com / www.163.com/search)：抓关键词搜索结果的标题/来源/日期，并从详情页取完整发布时间。

## Do this first (最优路径：本地 http_get，不走云浏览器)
网易搜索结果页是**服务端渲染(SSR)**，一次 GET 就返回全部 ~50 条结果的 HTML。本地 `http_get`（中国大陆 IP）实测直连成功、无反爬、无需登录。比云浏览器 DOM 更全（云端 DOM 首屏只懒加载 8 条，http_get 直接给 50 条）。

搜索 URL 模式（keyword 需 URL 编码）：
```
https://www.163.com/search?keyword=<urlencoded>
```

可原样跑（bh-lex 内，Python）：
```python
import urllib.parse, re, html
kw = urllib.parse.quote("人工智能")
r = http_get("https://www.163.com/search?keyword=" + kw,
             headers={"User-Agent": "Mozilla/5.0"})
body = r if isinstance(r, str) else r.get("body", "")

def clean(s): return html.unescape(re.sub(r'<[^>]+>', '', s)).strip()

# 每条结果是一个 <div class="keyword_new ...> 块
blocks = re.split(r'<div class="keyword_new', body)[1:]   # 实测 ~50 块
rows = []
for b in blocks:
    tm = re.search(r'<h3><a[^>]*href="([^"]+)"[^>]*>(.*?)</a></h3>', b, re.S)  # 标题+链接
    sm = re.search(r'class="keyword_source">(.*?)</div>', b, re.S)            # 来源(网易号发布者)
    dm = re.search(r'class="keyword_time">(.*?)</div>', b, re.S)              # 日期 YYYY-MM-DD
    if tm:
        rows.append({
            "title": clean(tm.group(2)),
            "url":   tm.group(1),
            "source": clean(sm.group(1)) if sm else "",
            "date":  clean(dm.group(1)) if dm else "",
        })

# 结果是相关度排序，不是时间序 → 要"最新一条"须自己按 date 降序
newest = sorted(rows, key=lambda x: x["date"], reverse=True)[0]
print(newest["date"], "|", newest["source"], "|", newest["title"])
print(newest["url"])
```
实测输出（2026-07-06，keyword=人工智能）：`2026-07-06 | 新浪财经 | 江苏发布住建领域"人工智能+"行动方案！` → https://www.163.com/dy/article/L159N6GF05568W0A.html

## 拿完整发布时间(HH:MM:SS)：进详情页
搜索页 `keyword_time` 只有日期精度(YYYY-MM-DD)。要精确到秒，GET 该条 url，取 `post_info`：
```python
r = http_get(newest["url"], headers={"User-Agent": "Mozilla/5.0"})
body = r if isinstance(r, str) else r.get("body", "")
m = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', body)   # 实测: 2026-07-06 10:50:17
print(m.group(1))
# 或整段: <div class="post_info">2026-07-06 10:50:17　来源: 新浪财经 …</div>
```

## 字段选择器速查（搜索结果 SSR HTML，实测可用）
- 结果条目容器：`<div class="keyword_new ...">`（regex 用 `re.split(r'<div class="keyword_new', body)`）
- 标题+链接：`<h3><a href="URL">标题</a></h3>`（标题含 `<em>关键词</em>` 高亮，clean() 去标签即可）
- 来源(发布者)：`.keyword_source`
- 日期：`.keyword_time`（仅日期）
- 缩略图：`.keyword_img a img`
- 文章 url 类型：正文多为 `www.163.com/dy/article/<ID>.html`(网易号)；也混有 `/v/video/<ID>.html`(视频) 和子站 `*.news.163.com/.../....html`

## Gotchas
- **没有"科技频道"筛选参数**。实测 `&channel=tech`、`&channelname=科技`、`&cat=`、`&type=` 全部被忽略，返回同一批结果。网易 web 搜索结果不带频道 facet，网易号文章的"来源"只是自媒体发布者名（如"CNMO科技""新浪财经"），无结构化频道标签。若任务要求"科技频道"：只能按来源名/标题做启发式过滤（含"科技/tech/IT/数码"的来源），或直接取全量按日期排最新——请在结果里如实说明该站无原生频道筛选。
- **结果是相关度排序，不是时间倒序**。要"最新一条"必须自己 `sorted(rows, key=date, reverse=True)`，不能取第 1 条。
- **搜索页时间只精确到"日"**，多条同为当天 → 无法在搜索页内区分谁更新。要在同日多条中定"最新"，得逐条进详情页读 `post_info` 的秒级时间戳再比。
- **http_get 可能持续返回截断响应**（只 8 条 / ~87KB，而非满 50 条 / 278KB）。首测重取即补齐；但 2026-07-06 re-verify 时**重取仍稳定 8 条**——同一 keyword 不同时段行为不一，别指望重试必然补全。8 条通常已够定"最新一条"（当天条目多落在前 8 内）；若确要满 50 条且 http_get 一直短，改走云浏览器 new_tab+js（`.keyword_new`，需滚动懒加载补齐）。校验 `len(body)` 与 `keyword_new` 计数决定是否切换路径。
- 云浏览器路径(new_tab+js)也能用：选择器 `.keyword_new`→`h3 a`(标题)、`.keyword_source`、`.keyword_time`。但云端首屏 DOM 只渲染 8 条，需滚动懒加载才补齐；除非 http_get 被封，否则**优先本地 http_get**。
- 未见独立的搜索 JSON 接口——结果直接内联在 SSR HTML 里（页面只额外请求 `gw.m.163.com/search/api/v1/pc-wap/hot-word` 热词，与结果列表无关）。
- 反爬：本地大陆 IP 直连无验证码、无 403、无需 Cookie/登录。带个普通 `User-Agent` 即可。
