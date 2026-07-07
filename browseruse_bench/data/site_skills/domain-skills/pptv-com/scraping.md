Field-tested on 2026-07-04
PPTV聚力 (pptv.com / PP视频) 视频搜索抓取：搜索结果页服务端渲染，含标题/时长/日期，无需浏览器、无需登录。

## Do this first (最优路径：本地 http_get + 正则，秒级)

搜索页 `https://sou.pptv.com/s_video?kw=<urlencoded关键词>` 是**完全服务端渲染**的 HTML，本地 `http_get`（中国大陆 IP）直接返回 200 + 全部结果，无反爬、无需 cookie/登录。不要开云浏览器抓这个站——浪费时间。

关键词用空格分词效果最好，例如 `英超 曼联 集锦 全场`。

```python
import re, urllib.parse

def pptv_search(kw):
    url = "https://sou.pptv.com/s_video?kw=" + urllib.parse.quote(kw)
    r = http_get(url)                      # 本地IP，实测 200 / ~600KB
    html = r if isinstance(r, str) else r.get('body', '')

    # --- 区块1: 正片/专题结果 (顶部 positive-box + 正片列表)。有标题/链接/video_id，但没有时长/日期 ---
    top = []
    for m in re.finditer(
        r'href="(//v\.pptv\.com/show/[^"]+)"[^>]*title="([^"]+)"[^>]*ext_info="[^"]*?video_id\'?:\s*\'?(\d+)',
        html):
        top.append({'title': m.group(2), 'url': 'https:' + m.group(1), 'video_id': m.group(3)})

    # --- 区块2: news-list 普通视频 (集锦/片段)。按 <li> 切块，含 时长+日期 ---
    nl = html[html.find('news-list'):]
    items = []
    for li in re.split(r'<li[ >]', nl):
        tm = re.search(r'href="(//v\.pptv\.com/show/[^"]+)"[^>]*title="([^"]+)"', li)
        if not tm:
            continue
        dur  = re.search(r'class="listtime">([\d:]+)<', li)          # 时长, 如 14:30
        date = re.search(r'时&emsp;间：</span>([\d\-]+)', li)        # 上传/比赛日期, 如 2013-11-19
        vid  = re.search(r"video_id'?:\s*'?(\d+)", li)
        items.append({
            'title': tm.group(2),
            'url': 'https:' + tm.group(1),
            'duration': dur.group(1) if dur else None,
            'date': date.group(1) if date else None,
            'video_id': vid.group(1) if vid else None,
        })
    # 去重 (video_id)
    seen, uniq = set(), []
    for x in top + items:
        k = x.get('video_id')
        if k and k in seen:
            continue
        seen.add(k); uniq.append(x)
    return uniq

for x in pptv_search("英超 曼联 集锦 全场")[:15]:
    print(x)
```

实测输出样例（news-list 段，字段齐全）：
```
{'title':'英超-曼联国王坎通纳曼联时期82进球集锦-专题','url':'https://v.pptv.com/show/Ngdo50ib1JWPGRKw.html?fp=searchResult','duration':'14:30','date':'2013-11-19','video_id':'16973622'}
{'title':'桑切斯曼联首秀集锦：无缝连接！他用40分钟融入曼联','duration':'09:37','date':'2018-01-28','video_id':'27328427'}
```
整场比赛结果样例（在标题里带对阵+比分）：
```
{'title':'英超-1213赛季-联赛-第38轮-西布朗5：5曼联-全场','video_id':'16564948','url':'https://v.pptv.com/show/1FQLiafFXxwVo5k4.html?fp=searchResult'}
```

## 字段位置（实测 HTML 结构）
- 标题：`<a ... title="...">` 属性 + `<h5>`/`.video-title` 内文，两处一致。含 `<strong>` 高亮标签需忽略（用 title 属性最干净）。
- 时长：`<i class="listtime">14:30</i>`（缩略图角标，仅 news-list 段有）。
- 日期：`<p><span>时&emsp;间：</span>2013-11-19</p>`（注意是 HTML 实体 `&emsp;`，正则里要原样匹配 `时&emsp;间`）。
- video_id：`ext_info="{'video_id': '16564948', ...}"`（等于 `channelId`）。
- 详情播放页：`https://v.pptv.com/show/<slug>.html`，会 302 到 `https://sports.pptv.com/vod/pg_h5video/<video_id>/<video_id>`。

## 关于任务字段（对阵/比分/进球球员/时长）——重要
PPTV 是**视频门户，不是赛事数据站**。没有结构化的比分/进球球员/球队字段。
- **对阵 + 比分**：只能从**标题字符串**里读，如 `西布朗5：5曼联`、`曼联VS沙尔克`。比分分隔符是全角冒号 `：`。用正则从标题抽：`re.search(r'([一-龥A-Za-z]+)\s*(\d+)\s*[:：]\s*(\d+)\s*([一-龥A-Za-z]+)', title)`。
- **集锦时长**：`duration` 字段（listtime 角标），实测可靠。
- **进球球员**：站内**无此字段**。整场集锦标题一般不列进球者；只有球员个人集锦（如"坎通纳...82进球集锦"）标题带人名。如任务硬要进球者，需打开视频看内容或换数据站——PPTV 抓不到。如实回报"不可得"。
- **比赛时间**：`date` 字段是上传/节目日期，不一定等于比赛日期；老比赛（如 1213 赛季）date 常缺失，只能从标题的"XX赛季/第X轮"推断。

## Gotchas
- 两类结果区分：顶部 `positive-box` 精选卡 + `正片`列表 **没有** listtime/date（只有标题/链接/video_id）；`news-list` 段（普通视频/集锦）才有时长+日期。按上面代码分区块解析，别指望所有条目都有全字段。
- 结果里混大量"展开更多(余N集)"折叠项，一次搜索能正则出 ~700 条 video_id（含专题子集）。要精确定位单场比赛，关键词加 `全场`/`第X轮`/具体赛季缩小范围。
- 详情/播放页多为**会员专享**（"本节目为会员专享内容，请开通会员"），且走 Flash/H5 player，页面 body 无有用元数据。**不要**去详情页抓字段，全部信息都在搜索结果页拿。
- 云浏览器出口是香港 IP；本站两条路（本地 http_get 与云 fetch）实测**都返回 200 且内容一致**（同为 618KB 服务端渲染 HTML），没遇到封锁。默认走本地 http_get 即可，无需云浏览器。
- 页面 title 前缀有个 `🐴` emoji（占位），不影响解析。
- 需要浏览器渲染时：搜索框在首页顶部；结果页 URL 模式 `sou.pptv.com/s_video?kw=` 可直接 new_tab 打开，`document.querySelectorAll('.news-list li')` 逐项取 `.listtime` / `时间` 文本，与 http_get 路径等价。
