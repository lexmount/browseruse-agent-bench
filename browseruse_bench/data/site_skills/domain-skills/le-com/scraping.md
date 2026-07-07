Field-tested on 2026-07-04
乐视视频 le.com — 搜电影 → 拿详情页(导演/主演/评分/年份/地区/评论数) + 播放页(总时长)。站内搜索走 so.le.com；详情页 www.le.com/movie/{id}.html；播放页 www.le.com/ptv/vplay/{vid}.html。

## Do this first (最省事的路径)

字段分两处：**详情页**(movie/{id}.html) 出导演/主演/评分/年份/地区/评论数；**播放页**(ptv/vplay/{vid}.html) 出总时长。
搜索页 + 详情页可直接用**本地 http_get**(中国大陆 IP，le.com 不封)静态解析；总时长在播放页是 JS 渲染的，必须用**云浏览器 new_tab + js** 读 DOM。

### 1. 站内搜索 → 拿候选 vid / movie id（本地 http_get 即可）
```python
import re, html, urllib.parse
kw = "叶问3"
r = http_get("https://so.le.com/s?wd=" + urllib.parse.quote(kw))
b = r.get('body','') if isinstance(r,dict) else str(r)
# 全片(电影) → movie 详情页；每条结果块里配一个 vplay 播放页
movies = re.findall(r'/movie/(\d+)\.html', b)      # 详情页 id
plays  = re.findall(r'/ptv/vplay/(\d+)\.html', b)  # 播放页 vid
print("movie ids:", list(dict.fromkeys(movies))[:10])
print("play vids:", list(dict.fromkeys(plays))[:10])
# 结果标题：搜索页每个卡片有 h1/标题文本，配合上下文判断是哪个片
```
实测 `so.le.com/s?wd=叶问3` 返回 80KB HTML，含 vplay 链接与片名，本地 http_get 直接可读。

### 2. 详情页字段（本地 http_get，静态 HTML 里就有）
```python
r = http_get("http://www.le.com/movie/10043974.html")
b = r.get('body','') if isinstance(r,dict) else str(r)
def field(label):
    i = b.find(label)
    if i < 0: return None
    seg = b[i:i+160]
    seg = html.unescape(re.sub(r'<[^>]+>', ' ', seg))
    return re.split(r'[:：]', seg, 1)[1].strip()[:80] if ('：' in seg or ':' in seg) else None
print("导演:", field('导演'))       # 叶伟信
print("主演:", field('主演'))       # 甄子丹 / 吴樾 / ...
print("上映:", field('上映'))       # 2019-12-20
print("评论数:", field('总评论数'))  # 272
print("地区:", field('国家/地区'))   # 中国香港
```
注意：`导演` 首次命中在 meta description 里(逗号分隔)，第二次才是正文 `导演：<a>叶伟信</a>`；上面按 label 定位后剥标签取值即可，正文命中更干净。评分在正文标签内，静态 HTML 的评分位可能为空(JS 填)，评分建议用下面云 DOM 路径。

### 3. 详情页 + 播放页字段（云浏览器 DOM，最稳、评分/总时长齐全）
```python
# ---- 详情页 ----
new_tab("http://www.le.com/movie/10043974.html"); wait_for_load(); wait(3)
detail = js(r"""
(() => {
  const t = document.body.innerText;
  const pick = l => { const m = t.match(new RegExp(l+'[:：]\\s*([^\\n]+)')); return m?m[1].trim():null; };
  return {
    director: pick('导演'),
    actors:   pick('主演'),
    year:     pick('上映'),
    region:   pick('国家/地区'),
    comments: pick('总评论数'),
    rating: (t.match(/评分[:：]?\s*\n?\s*([\d.]+)/)||[])[1] || null
  };
})()
""")
print(detail)  # 实测: director=叶伟信, actors=甄子丹/吴樾/吴建豪/斯科特·阿金斯, year=2019-12-20, region=中国香港, comments=272, rating=7.0

# ---- 播放页：总时长 ----
new_tab("http://www.le.com/ptv/vplay/67010305.html"); wait_for_load(); wait(3)
dur = js("(document.querySelector('.hv_total_time')||document.querySelector('.js-seek-tottime')||{}).innerText || null")
print("总时长:", dur)   # 实测 106:59  (即 1小时6分59秒)
```
播放页总时长实测选择器(任一均可，值一致=`106:59`)：`.hv_total_time`、`.js-seek-tottime`、`.time_info`；进度条文本形如 `00:00/106:59`，斜杠后即总时长。

## Gotchas

- **叶问3 本身不在 le.com 片库**。搜"叶问3"返回的是 **叶问4：完结篇 / 叶问外传：张天志 / 叶问前传** 三部全片 + 12 个"叶问3"相关花絮/预告片段；没有《叶问3》正片的 movie/vplay 页。上面代码用 叶问4(movie 10043974 / vplay 67010305)作实测样本，字段结构对全站通用。任务若要求"叶问3 播放页/导演/主演"，如实报"le.com 无该片"。
- **le.com 没有"播放量/观看次数"字段**。详情页和播放页整页扫描 `播放量/播放次/次播放/观看/热度/播放数` 全部命中不到。能拿到的最接近计数是详情页的 **总评论数**(272)。任务里的"播放量"这一项在 le.com 不可得 —— 别硬凑。
- **总时长只在播放页(vplay)，且 JS 渲染**：本地 http_get 抓播放页 HTML 里没有 `106:59`(要等 JS)，必须云浏览器 new_tab + js 读 `.hv_total_time`。详情页/搜索页则本地 http_get 静态可解，无需云浏览器。
- **JSON 接口没找到**。试过 `d.api.le.com`、`api.le.com`(本地 http_get 域名根本不解析/DNS 失败)、`a-static.le.com/movie/detail/{id}.json`、`player-pc.le.com/...playJson`(云 fetch 全部 `Failed to fetch`/CORS)。页面里也没有 `pageData/videoInfo/__NUXT__/window.player` 之类内联 JSON。**结论：走 HTML+DOM 解析，别指望免登录 JSON API。**
- **本地 http_get vs 云浏览器**：le.com 对中国大陆本地 IP 不封(http_get 详情页 200、33KB、含"导演")；云出口(香港)也能开 le.com。两条都通，按字段选路：搜索/详情 → 本地 http_get 更快；总时长 → 云 DOM。
- 反爬：未见验证码/JS 挑战，普通 http_get 直出 HTML；无需特殊 header。
