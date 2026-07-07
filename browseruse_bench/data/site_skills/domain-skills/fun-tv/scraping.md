Field-tested on 2026-07-04 — fun.tv (风行网) 剧集/电影抓取：搜索→拿 galleryid→打三个免登录 JSON 接口拿标题/主演/集数/每集时长/评论数。

## Do this first (最优路径：搜索页 DOM + 三个免登录接口，全程不用登录)

fun.tv 详情/播放页本身**不显示**主演/集数/时长/评论数的纯文本，但有干净的免登录 JSON API。流程：

1. **搜索定位 galleryid**。搜索是 GET 表单：`https://www.fun.tv/search/?word=<URL编码关键词>`。
   用云浏览器打开搜索页，从结果链接里抠出 galleryid（详情页 `/subject/<id>/`、播放页 `/vplay/g-<id>/`、分集 `/vplay/g-<id>.v-<epid>/` 里的 `<id>` 就是 galleryid，即 mediaid）。
2. **三个接口全部免登录、返回标准 JSON**，其中 profile + episode 走**云浏览器**没问题；comment 接口在 `api1.fun.tv`，页面内 fetch 会被 CORS 挡，**改用 http_get（本地IP）直接打**即可（实测本地IP不封 fun.tv）：
   - 简介/主演/集数/评分：`https://pm.funshion.com/v5/media/profile?id=<GID>&cl=web&app_code=web`
   - 分集列表/每集时长：`https://pm.funshion.com/v5/media/episode?id=<GID>&cl=web&app_code=web`
   - 评论数（按整部剧 galleryid，非分集）：`https://api1.fun.tv/comment/display/gallery/<GID>?pg=1&pg_size=50` → 取 `data.total_num`

### 可原样跑的代码块（browser-harness）

```python
import re, urllib.parse, json

# --- 1. 搜索拿 galleryid ---
kw = "西游记"                     # 换成你的关键词
new_tab("https://www.fun.tv/search/?word=" + urllib.parse.quote(kw))
wait_for_load(); wait(2)
# 结果链接里抠 galleryid（第一个电视剧/电影结果）
hrefs = js("Array.from(document.querySelectorAll('a')).map(a=>a.href)"
           ".filter(h=>/\\/(vplay\\/g-|subject\\/)\\d+/.test(h))")
gids = list(dict.fromkeys(
    re.search(r'/(?:vplay/g-|subject/)(\d+)', h).group(1)
    for h in hrefs if re.search(r'/(?:vplay/g-|subject/)(\d+)', h)))
print("候选 galleryid:", gids[:8])
GID = gids[0]                     # 或按搜索页展示的标题人工挑对目标剧

# --- 2. profile：标题/主演/导演/集数/评分/频道 ---
def hget(u):
    b = http_get(u)
    return b.get('body') or b.get('text') or str(b) if isinstance(b, dict) else b
prof = json.loads(hget(f"https://pm.funshion.com/v5/media/profile?id={GID}&cl=web&app_code=web"))
print("标题:", prof["name"])
print("主演:", prof["actor"])        # 逗号分隔，如 "马苏,六小龄童,李文颖,孙涛"
print("导演:", prof["director"])
print("集数:", prof["extend"]["totalnum"])   # 也 = episode 接口的 total
print("评分:", prof["score"])
print("频道:", prof["channel"], "| 类型:", prof["category"], "| 地区:", prof["area"])

# --- 3. episode：每集时长 + 集数 ---
ep = json.loads(hget(f"https://pm.funshion.com/v5/media/episode?id={GID}&cl=web&app_code=web"))
print("总集数:", ep["total"])
print("每集时长:", [(e["name"], e["duration"]) for e in ep["episodes"][:5]])  # duration 形如 "45:15"
print("第1集时长:", ep["episodes"][0]["duration"])

# --- 4. comment：整部剧评论数（第一集页展示的就是这个数）---
cm = json.loads(hget(f"https://api1.fun.tv/comment/display/gallery/{GID}?pg=1&pg_size=50"))
print("评论数:", cm["data"]["total_num"])   # 字符串，如 "6"；很多老剧为 "0"
```

实测输出（GID=92937《吴承恩与西游记》电视剧）：主演 马苏,六小龄童,李文颖,孙涛；导演 阚卫平；集数 46；评分 8.9；第1集时长 45:15；评论数 0。
另实测 GID=1031611《沸腾人生》评论数=6（证明该 comment 接口能返回真实非零值，不是恒为 0）。

## 字段速查（哪个接口拿哪个字段，均实测可用）

| 目标字段 | 接口 | JSON 路径 |
|---|---|---|
| 标题 | profile | `name` |
| 主演 | profile | `actor`（逗号分隔字符串）|
| 导演 | profile | `director` |
| 集数 | profile / episode | `extend.totalnum` / `total` |
| 评分 | profile | `score` |
| 频道(电视剧/电影)/类型/地区/上映 | profile | `channel` / `category` / `area` / `release` |
| 每集名称+时长 | episode | `episodes[i].name` + `episodes[i].duration`（"MM:SS"）|
| 评论数（整部剧）| comment | `data.total_num`（字符串）|

## 备用路径：页面内嵌 window.vplayInfo（不打接口也能拿集数/时长）

播放页 `https://www.fun.tv/vplay/g-<GID>/` 的页面里，`window.vplayInfo` 内嵌了完整分集数组，`window.vplay` 有 `galleryid/title` 等。无需任何接口：

```python
new_tab(f"https://www.fun.tv/vplay/g-{GID}/"); wait_for_load(); wait(3)
print(js("""(function(){
  var eps=[]; (window.vplayInfo.dvideos||[]).forEach(d=>(d.videos||[]).forEach(
    v=>(v.lists||[]).forEach(e=>eps.push(e))));
  return {title: window.vplay.title, ep_count: eps.length,
          first: {name:eps[0].name, duration:eps[0].duration}};
})()"""))
```
播放器区顶部还会显示 `00:00/45:15` 形式的第1集时长（video 时长）。主演/导演也以纯文本出现在播放页底部简介区（"导演：… 主演：…"），可作 DOM 兜底。

## Gotchas（都亲测过）

- **总播放量（total_vv / 播放量）在新平台不可得**。`total_vv` 字段在 vplayInfo、v5/media/episode、v5/media/profile 里**一律为 "0"**；profile/play 接口里也**没有** `vv/hits/playnum/playcount` 任何播放计数字段（只有 `score` 评分）。新平台把播放量下线了。任务里的"总播放量"这一项在 fun.tv 无法抓到——如实标注不可得，不要拿 total_vv 的 0 当真实播放量。
- **评论按整部剧（galleryid）计，不是按分集**。comment 接口 TYPE 用 `gallery`（因为播放页 `window.vplay.galleryid` 存在，前端 metchAPITemplete 逻辑就走 gallery/galleryid）。所以"第一集评论数"= 整部剧评论数 total_num（第1集页展示的就是它）。`comment/display/video/<epid>` 也能打通但同样多为 0。
- **comment 接口不能在云浏览器页面内 fetch**：`api1.fun.tv` 跨子域，`fetch(...,{credentials:'include'})` 报 `Failed to fetch`（CORS）。**必须用 http_get 本地IP直打**（实测本地IP不封 fun.tv，200 正常返回）。profile/episode 在 `pm.funshion.com`，http_get 和云浏览器 fetch 都行。
- **搜索"西游记"没有同名电视剧**：结果里电视剧是《吴承恩与西游记》(GID 92937)、电影是《西游记之再世妖王》。要按搜索页展示的标题/频道人工挑目标剧，别默认第一个链接就是。搜索结果里 `/subject/<id>/` 是详情页、`/vplay/g-<id>/` 是播放页，两者的 `<id>` 都是同一个 galleryid。
- **搜索表单是 GET**：`input[name=word]` 的 form action = `https://www.fun.tv/search/?word=`。直接拼 URL 导航最稳；用 `frm.submit()` 提交实测会被拦回首页。
- 播放器本身报 `cdn error(3008)` 无法真正起播（云环境/版权限制），但这不影响上面所有元数据接口——它们与播放鉴权无关。
- profile 的 `actor` 字段有时比播放页底部简介的主演更全（简介区可能截断成前 3 位）。取全主演用 profile.actor。

## 反爬情况

无验证码、无强制登录。三个数据接口都免登录返回标准 JSON。本地IP（中国大陆）直打 fun.tv / pm.funshion.com / api1.fun.tv 均 200，未见封禁。云浏览器出口（香港）打 pm.funshion.com 也正常。唯一限制是跨子域 CORS（见上，用 http_get 绕过）。
