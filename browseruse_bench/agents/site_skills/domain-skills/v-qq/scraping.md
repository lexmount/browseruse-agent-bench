# 腾讯视频 v.qq.com — 影视剧信息提取 skill

Field-tested on 2026-07-04 (cloud browser, real calls). Covers: 搜索剧名→拿 cid→读
导演/主演/播放量/评分/集数/更新状态；构造任意集播放页 URL。

数据分两处:
1. **搜索** → JSON API `MbSearch` POST(走云浏览器 fetch),返回 `标题 + cid`。
2. **详情/播放页** `v.qq.com/x/cover/{cid}.html` → 页内 `window.__VINFO_DATA__`
   给结构化字段(主演/集数/地区/类型/首播),外加 innerText 里的评分/播放量。

## 定位速查
- 搜索结果页 URL: `https://v.qq.com/x/search/?q={urlencode(词)}`(纯给人看,结果是 React 渲染,**无静态 `<a href>`、无 onclick,别想从 HTML 抠 cid**)。
- 剧集主页/第1集播放页: `https://v.qq.com/x/cover/{cid}.html`(会自动 302 到 `.../{cid}/{ep1_vid}.html`)。
- 第 N 集播放页: `https://v.qq.com/x/cover/{cid}/{vid}.html`,`vid = __VINFO_DATA__.coverInfo.video_ids[N-1]`。
- cid 形如 `mzc00200whsp9r6`(内地)或 `sfn1vjnjkmzedna`(外站引进),12~15位小写字母数字。

## Do this first —— 最优路径(全程走云浏览器 js(),不用 http_get)

一次搜索 + 一次开详情就能拿全字段。示例:繁花。

```python
# 步骤1: 搜索拿 cid（JSON API，一次 fetch，云 IP）
res = js("""
(async function(){
  var body=JSON.stringify({query:"繁花", pagenum:0, pagesize:30, sceneId:21, platform:"23"});
  var r=await fetch("https://pbaccess.video.qq.com/trpc.videosearch.mobile_search.MultiTerminalSearch/MbSearch?vplatform=2",
      {method:"POST",headers:{"Content-Type":"application/json"},body:body,credentials:"include"});
  var j=await r.json();
  // 只取“真正的剧集封面”：有 area 和 year 的才是正片，否则是短视频/UGC
  var hits=[];
  function walk(o){ if(!o||typeof o!=='object')return;
    if(o.videoInfo){var vi=o.videoInfo;
      if(vi.area&&vi.year){hits.push({title:(vi.title||'').replace(/<[^>]+>/g,''),
        cid:(o.doc&&o.doc.id), area:vi.area, year:vi.year, typeName:vi.typeName, subTitle:vi.subTitle});}}
    for(var k in o) walk(o[k]);
  }
  walk(j);
  return hits.slice(0,5);
})()
""")
# res[0] 即最匹配的一部，如 {'title':'繁花','cid':'mzc00200whsp9r6','area':'内地','year':2023,...}
cid = res[0]["cid"]
```

```python
# 步骤2: 开详情页，从 __VINFO_DATA__ 读结构化字段 + innerText 读评分/播放量
goto_url("https://v.qq.com/x/cover/%s.html" % cid)
wait_for_load(20); wait(4)   # 必须 wait，__VINFO_DATA__ 是异步注入的
data = js("""
(function(){
  var d=window.__VINFO_DATA__||{}; var ci=d.coverInfo||{}; var vi=d.videoInfo||{};
  var t=document.body.innerText;
  function rating(label){var m=t.match(new RegExp(label+"\\\\s*([0-9.]+)")); return m?m[1]:null;}
  var watch=t.match(/累计观看[^\\n]{0,20}/);
  var rev=t.match(/([0-9.]+万?)人点评/);
  return {
    title: ci.title,
    leading_actor: ci.leading_actor,        // 主演数组
    episode_all: ci.episode_all,            // 总集数（字符串）
    video_num: ci.video_num,                // 站内视频条数（含花絮，通常≥集数）
    area_name: ci.area_name,
    type_name: ci.type_name,                // 电视剧/动漫/电影
    publish_date: ci.publish_date,
    second_title: ci.second_title,          // 营销副标题，导演有时藏这里
    video_ids: ci.video_ids,                // 各集 vid，[0]=第1集
    // 评分（存在与否取决于内容；标签锚定 innerText 抠）
    tencent_score: rating("腾讯视频评分"),
    douban_score:  rating("豆瓣评分"),       // 内地/引进剧常有
    imdb_score:    rating("IMDb评分"),       // 外国内容常有
    watch: watch?watch[0]:null,             // 播放量 = “累计观看超过N小时”这类文案
    reviewers: rev?rev[1]:null              // “335.3万人点评”
  };
})()
""")
```

拿到后:主演=`leading_actor`;总集数=`episode_all`;评分见对应字段;
播放量=`watch`(注意腾讯给的是**累计观看时长**文案,不是“N亿播放”数字,见 Gotchas)。

## 构造第 N 集播放页 URL
```python
# 已在详情页、已有 data
vid_ep2 = data["video_ids"][1]           # 第2集
ep2_url = "https://v.qq.com/x/cover/%s/%s.html" % (cid, vid_ep2)
# 验证：开这个 URL 后 __VINFO_DATA__.videoInfo.episode == "02"
```
已实测:咒术回战第2季 cid=`mzc00200agxtrid`,`video_ids[1]="y0047ynntzv"`,
开 `.../mzc00200agxtrid/y0047ynntzv.html` → `videoInfo.episode=="02"`,title 含“_02”。

## 更新状态(连载/完结)
- **完结剧**:详情页 innerText 里出现 `全{N}集`(如“全23集”“全30集”),`episode_all`=N。
- **连载中**:页内有 `更新至第{N}集/话` 或动态区“第{N}集已经更新了”文案;
  正片区角标 imgTag 里也带更新文案(如“41分钟前”)。`coverInfo.episode_updated` 字段
  实测常为空字符串,**别只依赖它**,以 innerText 的 `全N集`/`更新至` 文案为准。
- 判定:`全N集` 出现 = 已完结;只有 `更新至` = 连载中。

## 已验证的三个真实任务结果
- **繁花**(cid `mzc00200whsp9r6`):导演王家卫(仅在 second_title“王家卫执导”里,无独立字段),
  主演 胡歌/马伊琍/唐嫣/辛芷蕾,全30集,腾讯9.2,豆瓣8.7,播放量“累计观看超过19小时”。
- **咒术回战 第2季**(cid `mzc00200agxtrid`):总集数23,area 日本,已完结(“全23集”),
  腾讯9.3,IMDb8.0(**外国番无豆瓣显示,是IMDb**),播放量“累计观看少于1小时”。
- **权力的游戏第一季**(cid `sfn1vjnjkmzedna`):主演肖恩·宾等7人,全10集,
  腾讯8.4/豆瓣9.5/IMDb9.2。**导演:腾讯页面完全不给**(见 Gotchas)。

## Gotchas(踩过的坑)
1. **`http_get` 不能用于搜索/详情**:这些接口和页面有反爬/IP 关联,`http_get` 走本地 IP
   会失败或被判。搜索 API 和详情页**都必须走云浏览器**(`js()` 里 fetch,或 `goto_url`)。
   实测 `js()` 内 `fetch(MbSearch, credentials:'include')` 从云 IP 返回 275KB 正常 JSON。
2. **搜索结果页 HTML 抠不出 cid**:结果是虚拟列表 + React handler,标题文本还夹零宽字符
   `​`,`<a href>`/`onclick` 里都没有 cover 链接。**不要**尝试解析搜索页 DOM——直接用
   步骤1 的 MbSearch JSON API 拿 cid。(替代:点“立即播放”按钮会 `window.open`
   到 cover URL,但坐标点击脆弱,API 更稳。)
3. **导演不是结构化字段**。腾讯视频**没有**独立的“导演”字段:`__VINFO_DATA__` 无 director,
   页面无“导演”标签(has '导演' == false)。有时导演出现在 `second_title` 营销语里
   (繁花“王家卫执导”),但**外国剧(如权力的游戏)连这都没有,页面根本查不到导演**。
   如实告知用户:腾讯页面查不到时,导演需另找豆瓣/维基,不要编。
4. **播放量口径**:腾讯给的是“累计观看超过N小时/少于1小时”这类**观看时长文案**,
   不是传统“N亿次播放”。如实按文案报,别硬转成播放次数。另有“{N}万人点评”是评分人数,别混淆。
5. **豆瓣评分不一定在**:内地/引进剧详情页常带“豆瓣评分”,外国番常只有“IMDb评分”。
   豆瓣是豆瓣的数据、由腾讯页面转载展示——**有就抠,没有就说没有**,需要权威豆瓣分应去豆瓣站。
6. **必须 `wait(3~4)` 再读 `__VINFO_DATA__`**:它是页面异步注入的,goto 后立刻读会拿到 undefined。
7. `episode_all` 是字符串;`video_num` ≥ `episode_all`(含预告/花絮 vid),按集数请用 `episode_all`,
   按 vid 取集请用 `video_ids`(索引0-based对应第1集)。
8. MbSearch 请求体里 `sceneId:21, platform:"23"` 是实测可用组合;省略部分字段也返回 200,
   但保留 `pagesize:30` 能一次拿够候选。返回里混大量短视频/UGC,**务必用 `area&&year` 过滤出正片**。
