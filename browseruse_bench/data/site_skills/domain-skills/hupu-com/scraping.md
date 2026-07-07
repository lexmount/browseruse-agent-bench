Field-tested on 2026-07-04 (re-verified 2026-07-06)
虎扑 hupu.com 帖子搜索与「步行街」板块抓取：站内搜索走 `bbs.hupu.com/search?q=<kw>`，页面把结果 SSR 进 `window.$$data`，用云浏览器 js() 直读即可，无需登录。

## Do this first（最优路径：搜索 → 提取 → 客户端排序取最热）
云浏览器打开搜索页，直接读 `window.$$data.searchRes.data`（20 条/页，含 title/replies/lights/forum_name）。默认「综合排序」已把讨论最热的帖排在第 0 位；要严格取最热就按 `replies` 客户端排序。**http_get 不可用**（见 Gotchas），必须走 new_tab+js。

```python
# 实测：搜 "NBA"，查看最热门帖子标题
new_tab("https://bbs.hupu.com/search?q=NBA"); wait_for_load(); wait(3)
result = js("""
(function(){
  var strip = s => (s||'').replace(/<[^>]+>/g,'').trim();   // title 里含 <font color> 高亮标签，必须剥掉
  var rows = window.$$data.searchRes.data.map(x=>({
    title:   strip(x.title),
    forum:   x.forum_name,          // 所属专区名，如 湿乎乎的话题 / 篮球资讯
    replies: +x.replies || 0,
    lights:  +x.lights  || 0,       // 亮评数
    id:      x.id,                  // 帖子数字 id
    web:     'https://bbs.hupu.com/' + x.id + '.html'   // schema 字段是 App deeplink(huputiyu://)，别用；用这个网页 URL
  }));
  var hottest = rows.slice().sort((a,b)=>b.replies-a.replies)[0];
  return { total: window.$$data.searchRes.count, first: rows[0], hottest: hottest, top5: rows.slice(0,5) };
})()
""")
print(result)
# 实测输出：hottest = "勒布朗去哪儿：里奇-保罗播客白板逐一剖析10大潜在下家"（湿乎乎的话题, replies=1087, id=640618963）
# first 与 hottest 一致——综合排序天然把最热帖放第 0 位。
```

搜索 URL 模式（实测）：`https://bbs.hupu.com/search?q=<keyword>`。keyword 可为中文/英文，直接放 `q=`。
- `count`=命中总数（NBA 实测 1000，封顶值），`totalPage`≈50，`hasNextPage` 标翻页。
- **翻页参数没验证成功**：`o` / `sort` / `order` 等排序参数会被写进 `$$data.query` 但服务端忽略，返回结果不变。想要更多/重排，靠客户端排序 `searchRes.data`，或直接读第 0 位。

## 「步行街」板块（两种含义，按任务选）
虎扑「步行街」是一个顶级大类（cateId=1，landing = `/all-gambia`），下辖 步行街主干道/湿乎乎的话题/历史区/恋爱区 等子专区。搜索页**没有**按「步行街」过滤的 URL 参数或 Tab（搜索页的专区 Tab 如 `NBA版`→`/all-nba` 只是跳转到分类落地页、会丢掉搜索词）。两条实测可用路径：

**A. 只要「步行街」板块的热帖（不带搜索词）** → 读步行街落地页：
```python
new_tab("https://bbs.hupu.com/all-gambia"); wait_for_load(); wait(3)
print(js("""
window.$$data.pageData.threads               // 70 条帖子, 字段 title/replies/lights/topic.name/url
  .map(t=>({title:t.title, replies:+t.replies||0, lights:+t.lights||0,
            forum:t.topic&&t.topic.name, web:'https://bbs.hupu.com'+t.url}))
  .sort((a,b)=>b.replies-a.replies).slice(0,5)
"""))
# 另有 window.$$data.pageData.trending (10 条) = 热门关键词(只有 title/url, 非完整帖子)
```

**B. 搜索词 + 步行街过滤** → 用路径 A 拿不到搜索词命中；改在搜索结果里按 `forum_name` 客户端过滤到步行街子专区（湿乎乎的话题/步行街主干道/历史区/恋爱区/搞笑趣味 等）：
```python
new_tab("https://bbs.hupu.com/search?q=NBA"); wait_for_load(); wait(3)
BXJ = ['步行街主干道','湿乎乎的话题','历史区','恋爱区','摄影器材','酒水饮料','美食天地','搞笑趣味','校园大学生'];
print(js(f"""
window.$$data.searchRes.data
  .filter(x=>{BXJ}.includes(x.forum_name))
  .map(x=>({{title:x.title.replace(/<[^>]+>/g,''), forum:x.forum_name, replies:+x.replies||0}}))
  .sort((a,b)=>b.replies-a.replies)
"""))
# 实测 "NBA" 搜索里步行街命中集中在「湿乎乎的话题」(NBA区步行街), 最热即 replies=1087 那条。
```
注意：NBA 相关的「湿乎乎的话题」严格属于 NBA 大类(cateId=8)下的步行街风格讨论区，日常口语里就叫步行街/湿乎乎。若任务只说「搜 NBA + 步行街 + 最热」，直接取 `searchRes.data` 第 0 位（湿乎乎的话题那条）就是答案。

## 分类映射（实测，来自搜索页 window.$$data.categories）
name→cateId→landing：综合体育 10 /all-sports · **步行街 1 /all-gambia** · 影视娱乐 5 /all-ent · 英雄联盟 40 /all-lol · 游戏 7 /all-gg · NBA 8 /all-nba · 装备 11 /all-gear · CBA 22 /all-cba · 国际足球 9 /all-soccer · 中国足球 23 /all-csl · 数码 6 /all-digital · 汽车 13 /all-cars。

## Gotchas
- **http_get（本地中国大陆 IP）对 `bbs.hupu.com/*` 全部返回 HTTP 405 Not Allowed**（`/search`、`/all-gambia`、`/<id>.html` 都 405）。只有 `https://www.hupu.com/`(首页) 本地 http_get 能拿到(含 $$data)。结论：搜索/板块抓取**必须用云浏览器 new_tab+js**，别指望 http_get 走本地 IP 兜底。
- 云出口是香港 IP，但 hupu 对香港云 IP **不封**，云浏览器访问完全正常、内容是国内正常内容（无地区化偏差）。
- 搜索结果 `title` 字段含 `<font color='#c01e2f'>关键词</font>` 高亮标签，提取标题务必 `replace(/<[^>]+>/g,'')` 剥标签。
- 结果条目的 `schema` 字段是 App 深链 `huputiyu://bbs/topic/<id>`，**网页链接要用** `https://bbs.hupu.com/<id>.html`（id 在 `x.id`）。
- 首页/板块页的搜索框在 SSR HTML 里查不到 `<input>`/`<form>`（React 后挂载）；不要靠找搜索框，直接拼 `search?q=` URL 最稳。
- 排序/翻页 query 参数(o/sort/order/page/categoryId)服务端一律忽略，只能靠 `searchRes.data` 客户端排序过滤；单页 20 条通常够用。
- 免登录：以上全部路径无需登录，`$$data` 直接可读。
- 偶发 `WebSocket connection closed`（云会话掉线）；`./bh-lex --close` 后重开即恢复，SSR 数据一致。
