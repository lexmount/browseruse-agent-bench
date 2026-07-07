# IGN — 游戏评测分数提取

`https://www.ign.com`。Field-tested on 2026-07-04（英文站，Next.js SSR）。

## 关键约束

- **`http_get` 一律被反爬拦成 HTTP 404**（无论 UA），拿不到任何页面。必须用真浏览器。
- **有基于频率的反爬**：短时间快速导航多个页面后，站点对本会话返回**假 404 页面**（标题 "IGN Error 404"，或标题正常但内容节点被清空）。这不是 URL 错，是软封禁。**每次导航之间 sleep 8~15 秒**，触发后冷却 20~30 秒再继续。批量任务尤其要慢。

## 游戏评分（game hub 页）

游戏页 URL：`https://www.ign.com/games/<slug>`，slug 为小写连字符（elden-ring、cyberpunk-2077）。评分在 `.review-score`：

```python
new_tab("https://www.ign.com/games/elden-ring")
wait_for_load(); wait(3)
score = js("var s=document.querySelector('.review-score'); s?s.textContent.trim():null")
# Elden Ring -> "10"（IGN 满分 10 制，整数或一位小数）
```

如果 `.review-score` 为 null 但标题正常，多半是渲染未完成或软封禁——`wait(3)` 再读一次；仍为空则冷却后重开。

## 用搜索解析 slug（slug 未知时）

游戏 slug 不总能从名字猜出（"Baldur's Gate 3" 不是 `baldurs-gate-3`）。用站内搜索页拿真实链接：

```python
import urllib.parse
new_tab("https://www.ign.com/search?q=" + urllib.parse.quote("Baldur's Gate 3"))
wait_for_load(); wait(3)
links = js("""JSON.stringify(Array.from(document.querySelectorAll('a[href*="/games/"]'))
  .map(a=>a.getAttribute('href')).filter((v,i,a)=>a.indexOf(v)===i).slice(0,5))""")
```

## 结构化数据（备选）

页面内嵌 `__NEXT_DATA__`（`document.getElementById('__NEXT_DATA__').textContent`，JSON），含 `score` / `scoreText`（如 "Masterpiece"）等字段，比 DOM 选择器更耐改版；软封禁时该节点同样为空。

## Gotchas

- IGN 评分是编辑评分（满分 10），与 Metacritic/用户分不同；"IGN 评分"特指这个。
- 评测正文在 `/articles/<slug>-review`；game hub 页只给分数和摘要。
- 反爬对 CDP 控制的浏览器敏感，务必控制节奏——这是 IGN 任务失败的头号原因。
