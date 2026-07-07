# Google 搜索 — 结果提取（反爬最重，读完再动手）

`https://www.google.com/search`。Field-tested on 2026-07-05。
Google 是本数据集里最难自动化的目标。**先按下面的路径决策，别盲目 new_tab 或 http_get。**

## 路径决策（实测三条路的结果）

| 路径 | 结果 | 结论 |
|------|------|------|
| 香港云 IP 浏览器(new_tab) | "Our systems have detected unusual traffic" 验证码墙 | ❌ 不可用——数据中心 IP 被 Google 拉黑 |
| 本地 http_get(带桌面 UA) | HTTP 200、无验证码、~90KB，但**只有 JS 外壳，服务端不渲染结果**；`gbv=1` 基础版已废弃同样无结果 | ⚠️ 能连通但抓不到结果 |
| 本地真实浏览器(本地 Chrome, 非云) | 本地 IP 未被验证码标记，JS 可正常渲染出结果 | ✅ 唯一能拿到结果的路径(需本地 Chrome 已连 CDP) |

**要点：Google 属性(含 scholar.google)一律"反着来"——云 IP 被封，本地 IP 通。** 与多数站点相反。

## 推荐做法 A：本地浏览器渲染后抠 DOM

前提：本地 Chrome 已开启远程调试并连上 browser-harness(不要设 BU_CDP_WS，用默认本地 daemon)。

```python
import urllib.parse
new_tab("https://www.google.com/search?q=" + urllib.parse.quote("查询词") + "&hl=en&num=10")
wait_for_load(); wait(2)
res = js("""
(function(){
  var out = [];
  document.querySelectorAll('a:has(h3)').forEach(function(a){
    var h3 = a.querySelector('h3');
    if (h3 && a.href && a.href.indexOf('google.')<0) out.push({title:h3.innerText, url:a.href});
  });
  return JSON.stringify(out.slice(0,10));
})()
""")
# 每条结果块 div.g；标题 h3；链接是 h3 的祖先 <a href>；摘要在相邻 div 里
```

## 推荐做法 B（云环境/本地 Chrome 不可用时）：换可达的搜索引擎

数据集里 google.com 的任务多为**通用网络检索**(如"2024中国GDP增长率""某产品对比")，答案不依赖 Google 独有排序。此时改用本仓库已验证可用的引擎，把它当"找权威来源"的入口：
- **360 搜索**(`so-com/` skill)：云+本地都通，`so.com/s?q=` + `h3.res-title>a[data-mdurl]`。
- **Bing**(`bing.com/search?q=`)：云 IP 可用，多个探索任务(如搜狐视频定位)已靠它绕过站内反爬。

在报告里注明"Google 自动化被反爬拦截，改用 X 引擎检索"，比硬撞验证码墙实在。

## Gotchas

- 云浏览器打开 google 只会得到 "About this page / unusual traffic" 页(`document.title` 常显示为该文案)——检测到就立刻切本地路径或换引擎，不要重试云路径。
- 本地 http_get 返回 200 且 `<title>Google Search</title>` 会让人误以为成功，但 `r.count("<h3")==0`、无 `/url?q=`——**用"h3 数量或 /url?q= 数量为 0"判定为 JS 壳**，不是真结果。
- 学术检索优先用 `scholar.google.com`(见该站 skill：本地 http_get 直接可解析，比 google 主搜好抓)。
- 别用 `gbv=1`：Google 已移除无 JS 基础版，该参数不再返回服务端结果。
