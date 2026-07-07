Field-tested on 2026-07-04
优酷(youku.com) 视频/影视元数据抓取：**云浏览器IP被硬封(cloud_ip_bl)，走本地 http_get 抓 SSR 页面** —— 详情/剧集/影片元数据全在 `list.youku.com/show/id_z<showId>.html` 和 `v.youku.com/v_show/id_<vid>.html` 的服务端渲染 JSON 里，不用点页面。

## 关键架构事实（实测）
- **云浏览器 new_tab/js 对所有 youku 主机一律返回 punish 页**（`bixi.alicdn.com/punish...cloud_ip_bl`，标题 "🐴 Access denied"）。云IP彻底不能用来开 youku。
- **搜索页(so.youku.com / m.youku.com/search)对本地 http_get 也 punish**（滑块验证码 "验证码校验"）。**不要试图站内搜索**。
- **但详情/剧集/播放页(list.youku.com、v.youku.com、vo.youku.com)对本地 http_get 返回完整 SSR HTML，无 punish**。服务器还会回显你的IP，确认走的是本地IP。→ 所有抓取都用 `http_get`，不用云浏览器。
- http_get 返回**字符串**(不是dict)，HTTP错误会抛 urllib.error.HTTPError。

## Do this first —— 定位 showId/vid（搜索被封，改用外部搜索兜底）
站内搜索被封，用 WebSearch / WebFetch（或百度/必应）按 `<标题> 优酷 v.youku.com id_` 拿到 youku URL，从 URL 提取 id：
- 影片/番剧详情页：`https://list.youku.com/show/id_z<showId>.html`（showId 是 20位hex，如 `bbef9ab5a52e466a82c9`）
- 单集/正片播放页：`https://v.youku.com/v_show/id_<vid>.html`（vid 形如 `XNjUwMjMzOTk3Ng==`，注意含 `==`）
- 播放页(v_show)的 `window.__PAGE_CONF__` 里带 `"showId":"..."`，可从任意一集反查详情页。

## 抓取核心（原样可跑，本地 http_get）
```python
import re, json
h = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

def yk_initial(url):
    """抓 SSR 页面，返回 window.__INITIAL_DATA__ (dict)。punish 时返回 None。"""
    t = http_get(url, headers=h)
    if "punish" in t: return None
    m = re.search(r'window\.__INITIAL_DATA__\s*=\s*(\{.*?\});?\s*</script>', t, re.S)
    return json.loads(m.group(1)) if m else None

def _find(o, pred):
    r=[]
    if isinstance(o,dict):
        if pred(o): r.append(o)
        for v in o.values(): r+=_find(v,pred)
    elif isinstance(o,list):
        for v in o: r+=_find(v,pred)
    return r
```

### 任务A：影片详情（导演/主演/片长/类型/上映/评论数）——满江红 实测
```python
d = yk_initial("https://list.youku.com/show/id_zbbef9ab5a52e466a82c9.html")
ex = d["pageMap"]["extra"]                       # 头部元数据全在这里
intro = _find(d, lambda o:o.get("title")=="简介" and "roles" in o)[0]
director = [r["title"] for r in intro["roles"] if r.get("subtitle")=="导演"]
cast     = [r["title"] for r in intro["roles"] if str(r.get("subtitle","")).startswith("饰")]
print("片名:", ex["showName"], "| 类型大类:", ex["showCategory"])   # 满江红 / 电影
print("导演:", director)                                          # ['张艺谋']
print("主演:", cast[:8])                                          # 沈腾/易烊千玺/张译/雷佳音/岳云鹏...
print("地区·年·类型:", intro["itemList"][0]["introSubTitle"])     # 中国·2023·剧情
print("片长(min):", round(ex["duration"]/60,1))                   # 9539.27s => 159.0
print("上映:", ex["showReleaseTime"])                             # 2023-04-28 16:00:00
print("发布上线:", ex["videoPublishTime"])                        # 2023-04-28 15:36:16
print("评论数:", ex["totalComment"], "| 点赞:", ex["totalUp"])    # 8197 / 35792
```
影片简介文字：`intro["itemList"][0]["desc"]`。

### 任务B：番剧最新一季最后一集（集名/发布时间/评论数）——鬼灭之刃 S1 实测
```python
d = yk_initial("https://list.youku.com/show/id_zecbb8662ae5f4872a3b8.html")
sel = _find(d, lambda o:o.get("title")=="选集")[0]  # 选集组件
eps = sel["itemList"]                               # 有序剧集列表
last = eps[-1]                                       # 最后更新那集
stage = last["stage"]                                # 集数序号 26
name  = last["title"]                                # 集名「新的任务」
vid   = last["action"]["value"]                      # XNjUwMjMzOTk3Ng==（用于播放页）
print("番剧:", d["pageMap"]["extra"]["showName"], "| 总集:", len(eps))

# 单集发布时间/时长/评论数 → 抓该集播放页的 JSON-LD + PAGE_CONF
pt = http_get(f"https://v.youku.com/v_show/id_{vid}.html", headers=h)
ld = json.loads(re.findall(r'application/ld\+json">(.*?)</script>', pt, re.S)[0])["@graph"][0]
tc = re.search(r'"totalComment":\s*(\d+)', pt)
print("集名(LD):", ld["name"])            # 鬼灭之刃 第26话 新的任务-动漫-...
print("发布时间:", ld["uploadDate"])       # 2025-11-14 20:00:11
print("时长:", ld["duration"])             # PT23M45S (ISO8601)
print("评论数:", tc.group(1) if tc else None)   # 174
```
番剧的演员/声优、简介同样在 `简介` 组件的 `roles` / `itemList[0]["desc"]`。

### 播放页(v_show) 通用字段（JSON-LD，SSR，无 punish）
每个 `v.youku.com/v_show/id_<vid>.html` 头部有一段 `<script type="application/ld+json">`：
`name`(标题+分类)、`description`(简介)、`uploadDate`/`datePublished`(发布时间)、`duration`(ISO8601时长)、`genre`(分类)、`thumbnailUrl`。
另有 `window.__PAGE_CONF__`(JSON)：`title`,`desc`,`publishTime`,`seconds`(秒数),`showId`,`videoCategory`,`videoId`,`stage`。

## Gotchas（实测，含失败路径）
- **云浏览器完全不可用**：new_tab 任意 youku URL → cloud_ip_bl punish 页。**本域skill全程用 http_get，勿开云浏览器**。
- **站内搜索全被封**：`so.youku.com/search_video/q_<kw>`、`m.youku.com/search`、`search.youku.com/suggest` 对本地IP也返回滑块验证码。→ 必须靠外部搜索(WebSearch/WebFetch/百度)拿 youku URL，再 http_get。
- **`m.youku.com/` 首页本地能抓但没用**：是 JS 壳，链接运行时注入，SSR HTML里提不到 id。真正有数据的是 list/v_show/vo 三类 SSR 页。
- **播放量(VV)不在 SSR**：show 页和播放页 SSR 都没有单集/影片播放量，前端另调签名接口渲染。任务A/B里「播放量」这一项从 SSR 拿不到。
- **热评/评论列表不在 SSR**：评论接口 `p.comments.youku.com/ycp/comment/pc/commentList` 本地可达(不封IP)但要签名(`{"code":-3,"message":"缺少接口签名验证参数"}`)；`acs.youku.com` MTOP 网关对 appKey 24679788 既不下发 `_m_h5_tk` token 也报 `FAIL_SYS_API_NOT_FOUNDED`。**只有评论总数(totalComment)能从SSR拿到，前N条热评内容拿不到**（未攻破签名）。
- **豆瓣评分不在 youku SSR**：满江红 show 页 JSON 里唯一的「豆瓣 7.1分」标签属于相关推荐位里的另一部片(《用武之地》)，不是本片评分。本片自身无豆瓣分字段。→ 豆瓣评分需去豆瓣站取，youku 抓不到。
- **编剧字段缺失**：满江红 `roles` 只有「导演」+ 演员(饰XX)，无「编剧」条目。编剧不一定有。
- `openapi.youku.com/v2/searches/...` 返回 `{"error":{"code":1004,"description":"Client id null"}}`——需申请的 client_id，公开不可用。
- vid 常含 `==` 结尾(base64)，拼 URL 时保留，别 urlencode 掉。showId 前缀是 `z`(如 `id_zbbef9...`)，show 页URL里带 z，但 `pageMap.extra.showId` 字段值不带 z。
