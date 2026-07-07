Field-tested on 2026-07-04 (re-verified 2026-07-06)
观察者网 (guancha.cn) — 站内搜索 + 按时间排序 + 文章正文提取。适用任务：搜某主题、按时间看最近文章、总结近期热议话题。

## Do this first（最优路径：云浏览器打开 search.htm → 点"按时间排序" → DOM 提取）

站内搜索是 JS 客户端渲染的，`http_get` 拿到的是空壳（无结果）。必须用云浏览器 new_tab 打开搜索页，让页面自己 AJAX 加载结果，再排序并用 js() 提取。整个流程一次调用即可：

```python
import urllib.parse, json
kw = urllib.parse.quote("国际关系")   # 换成你的关键词
new_tab(f"https://www.guancha.cn/api/search.htm?click=news&keyword={kw}")
wait_for_load(15); wait(2)

# 关键：点击"按时间排序"（结果按最新在前重排；同一 URL，客户端排序）
js("document.querySelector('.sort-time') && document.querySelector('.sort-time').click()")
wait(3)

# 结构化提取：标题/链接/日期/点击数/评论数/摘要
extract = r"""
(function(){
  var out=[];
  document.querySelectorAll('li').forEach(function(li){
    var a=li.querySelector('.list-item h4 a'); if(!a) return;
    var summary=(li.querySelector('.item-content')||{}).innerText||'';
    var op=li.querySelector('.op-tools'), clicks='', comments='', date='';
    if(op){
      op.querySelectorAll('li').forEach(function(x){
        var t=x.innerText.trim();
        if(/^点击/.test(t)) clicks=t.replace(/[^0-9]/g,'');
        else if(/^评论/.test(t)) comments=t.replace(/[^0-9]/g,'');
      });
      var m=op.innerText.match(/\d{4}-\d{2}-\d{2}[ \d:]*/);
      if(m) date=m[0].trim();
    }
    out.push({title:a.innerText.trim(), url:a.href, date:date,
              clicks:clicks, comments:comments, summary:summary.trim().slice(0,120)});
  });
  return JSON.stringify(out);
})()
"""
rows = json.loads(js(extract))
for r in rows[:15]:
    print(r["date"], "|", r["clicks"], "点击", r["comments"], "评论 |", r["title"])
    print("   ", r["url"])
```

实测输出（关键词"国际关系"，按时间排序后前几条，最新在前）：
```
2026-07-05 17:46:31 | 152813 点击 72 评论 | "坚定选择中国：不是权宜之计，更为了未来"
2026-07-04 10:50:06 | 32390  点击 23 评论 | 特朗普，"人民永远比你强大！"
2026-07-03 20:32:12 | 12567  点击 19 评论 | "小国团结起来，就是国际体系的绝大多数"
2026-07-03 07:22:02 | 75501  点击 56 评论 | 王毅：中欧是伙伴而不是对手
```
一页约 15 条结果全部随首屏 AJAX 一次性加载，无需翻页即可覆盖最近一两天。

## 提取文章正文（总结话题内容用）

文章详情页 `/{频道}/YYYY_MM_DD_NNNNNN.shtml` 是**服务端渲染的静态页**，本地 `http_get` 就能拿到完整正文（更快，不占云会话）。正文容器是 `.all-txt`（含 `<p>` 段落），标题用 `document.title` 去掉 `🐴 ` 前缀。

云浏览器方式（已验证）：
```python
new_tab("https://www.guancha.cn/internation/2026_07_05_822699.shtml"); wait_for_load(15); wait(2)
art = json.loads(js(r"""(function(){
  var body=document.querySelector('.all-txt');
  var paras=Array.from(body.querySelectorAll('p')).map(p=>p.innerText.trim()).filter(t=>t.length>0);
  return JSON.stringify({
    title: document.title.replace('🐴 ',''),
    pub_time: (document.querySelector('.time')||{}).innerText.split('\n')[0]||'',
    body: paras.join('\n')
  });
})()"""))
print(art["title"], "|", art["pub_time"])
print(art["body"][:500])
```
本地备份方式：`http_get("https://www.guancha.cn/internation/2026_07_05_822699.shtml")` 返回 68KB 完整 HTML，含 `.all-txt` 正文——本地 IP（中国大陆）访问文章页正常，未被封。

## 免登录 JSON 接口（可用但需现摘 token）

搜索结果真正的数据来自 JSON 接口：
`https://s.guancha.cn/main/search-v2?page=1&type=search_news&order=<1|2>&keyword=<urlenc>&gczs=<TOKEN>`
- `order=1` = 按相关度；`order=2` = 按时间（最新在前）。
- 返回干净 JSON：`data.items[]` 每条含 `title, created_at, view_count, comment_count, url, summary, special`(栏目/专题名), `special_url`, `total_found`。
- **`gczs` token 是必需的，且绑定到本次精确参数（含 order），由页面里那段 sojson.v4 混淆 JS 现算，无法离线伪造**。缺 token 或参数不匹配返回 `{"code":4,"msg":"验证错误"}`。

因此用法必须"先在页面里生成 token，再复用它 fetch JSON"——比 DOM 提取更干净：
```python
# 打开搜索页并点时间排序，让页面为 order=2 也铸出一个有效 token
new_tab(f"https://www.guancha.cn/api/search.htm?click=news&keyword={kw}"); wait_for_load(15); wait(2)
js("document.querySelector('.sort-time').click()"); wait(3)
# 从 performance 里捞出页面刚请求过的、带有效 token 的 search-v2 URL
urls = json.loads(js(r"""JSON.stringify([...new Set(performance.getEntriesByType('resource')
        .map(r=>r.name).filter(n=>/search-v2/.test(n)))])"""))
time_url = [u for u in urls if "order=2" in u][0]   # order=2 = 按时间
data = json.loads(js(f"""(async()=>{{
   var r=await fetch("{time_url}",{{credentials:'include'}}); return await r.text();
}})()"""))
for it in data["data"]["items"][:15]:
    print(it["created_at"], it["view_count"], it["comment_count"], it["title"], it["url"])
```
（`fetch` 在页面上下文里跑=走云 IP，且带页面 cookie；`s.guancha.cn` 云 IP/香港出口访问正常。）

## Gotchas
- **搜索是纯客户端渲染**：`http_get("https://www.guancha.cn/api/search.htm?...")` 只返回 24KB 空壳 HTML，**没有任何结果**（`list-item`/关键词都不在里面）。别用 http_get 抓搜索结果——必须走云浏览器。
- **`/search`、`https://so.guancha.cn/` 都无效**：`/search` 302 回首页；`so.guancha.cn` 不存在（DNS/连接错误）。正确入口是首页搜索框调用的 `/api/search.htm?click=news&keyword=...`（源自 `gotoUrl()`）。
- **排序无独立 URL**："按相关度/按时间"是两个 `<span class="sort-hot|sort-time">`，点击触发 JS 客户端重排（URL 不变）。要时间序就 `.sort-time` click 后再提取；对应 JSON 接口是 `order=2`。
- **`gczs` token 不可离线伪造**：由 `static.guancha.cn/news/www/js/search.js`（sojson.v4 混淆）现算，绑定精确参数串。想用 JSON 接口只能"页面先铸 token → performance 捞 URL → fetch 复用"，token 不能跨关键词/跨 order 复用。
- **文章 ID 递增**：URL 形如 `internation/2026_07_05_822699.shtml`，日期段=发布日，`822699` 是递增文章号，可据此判新旧。频道路径示例：国际=`/internation`，财经=`/economy`，军事=`/military-affairs`，政务=`/politics`。
- **标题带 🐴 前缀**：`document.title` 是 `🐴 观察者网 - …`，提取标题记得 `.replace('🐴 ','')`。
- 反爬：未遇到验证码/登录墙/IP 封锁。文章页本地 IP 可直取；搜索页/JSON 接口云浏览器可取。唯一"墙"是搜索接口的 gczs 校验，按上面办法即可绕过。
