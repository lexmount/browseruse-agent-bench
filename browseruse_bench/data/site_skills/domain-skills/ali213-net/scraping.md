Field-tested on 2026-07-04
ali213.net (游侠网) — 单机游戏门户；游戏攻略在子域 gl.ali213.net，长篇"主线流程攻略"是分页文章，第N页=第N章，页脚有完整章节目录(TOC)。**云浏览器(香港IP)和本地 http_get(中国IP)两条路都实测可用、返回一致**，无登录墙、无验证码。

## Do this first —— 定位某游戏的"主线流程攻略"并抽取章节结构

游侠攻略的最优入口不是站内搜索页(结果混杂 MOD/新闻)，而是**游戏攻略专区页 `gl.ali213.net/z/<zid>/`**，那里直接挂着"主线流程攻略"和"图文攻略"两篇长文。找 zid 的最快办法是全局搜索定位到专区，或直接搜攻略子类。

三步走(以"最终幻想16"为例，实测 zid=48145，主线流程攻略 articleId=1088235)：

```python
import urllib.parse, re
kw = urllib.parse.quote("最终幻想16")

# 1) 全局搜索 -> 找攻略文章。sub=96 = 攻略子类过滤
new_tab("https://so.ali213.net/s/s?sub=96&keyword=" + kw); wait_for_load(); wait(2)
links = js("""(function(){
  return Array.from(document.querySelectorAll('a'))
    .map(a=>({t:(a.innerText||'').trim().slice(0,50), h:a.href}))
    .filter(x=>/gl\\.ali213\\.net\\/(z\\/|html\\/)/.test(x.h))
    .filter(x=>/流程|图文|专区|攻略/.test(x.t));
})()""")
print(links)   # 找 '主线流程攻略' 或 'z/<zid>/' 专区链接
```

```python
# 2) 打开主线流程攻略文章，抽出完整章节目录 + 章数 + 分页URL模式
new_tab("https://gl.ali213.net/html/2023-6/1088235.html"); wait_for_load(); wait(2)
toc = js("""(function(){
  return Array.from(document.querySelectorAll('a'))
    .map(a=>({t:(a.innerText||'').trim(), h:a.href}))
    .filter(x=>/^第\\d+页/.test(x.t));   // 页脚章节目录：'第N页：<章节名>流程攻略'
})()""")
print("章节数:", len(toc))          # -> 28 (=28章主线)
print("前3章:", [t['t'] for t in toc[:3]])
# 分页URL模式：首页 .../<id>.html，第N页 .../<id>_N.html
```

```python
# 3) 逐章抽正文(战斗机制/任务步骤/boss技巧)。正文容器选择器 = .content
for suffix in ["", "_2", "_3"]:            # 前3章
    new_tab(f"https://gl.ali213.net/html/2023-6/1088235{suffix}.html"); wait_for_load(); wait(1)
    body = js("""(function(){
      var b=document.querySelector('.content');
      return b ? b.innerText.replace(/\\s+/g,' ').trim().slice(0,600) : 'NO-BODY';
    })()""")
    print(suffix or "1", "=>", body)
```

实测这套流程一次跑通即可回答"章节数量/总时长/前3章关键任务与boss技巧"整个任务。

## 已验证事实(2026-07-04实跑)
- **主线流程攻略 = 28 页 = 28 章**，第1章"火之召唤兽-暗杀显化者"…第28章"舞向冰晶"。页脚 `第N页：X流程攻略` 目录用 `a` 文本 `/^第\d+页/` 过滤，稳定拿全 28 条。
- **总时长估计**：单独的"FF16主线时长"文章 `gl.ali213.net/html/2022-11/945947.html` 明确写 **35-40小时**(主线)。用 http_get 抓正则 `\d+[-~到至]\d+小时` 即得。
- **前3章要点**(正文实测抽到)：第1章开场教学、跟剧情走无难点；第2章"骑士的骄傲"战斗教学——方块=剑(最多4连击)、三角=魔法、R1闪避、精准闪躲减速敌人、圆圈=不死鸟变移、R2+方块处决(敌人HP下黄条=毅力量表,归0倒地可造成重伤);第3章"黄昏迫近"——长按L3显示导览标志,前往揭见厅。
- 搜索框：首页 `<form action="https://so.ali213.net/s/c?">`，参数 `keyword=`(需 URL 编码)。攻略子类过滤用 `s/s?sub=96&keyword=`。

## 关键选择器 / URL 模式
- 攻略专区: `https://gl.ali213.net/z/<zid>/`  (FF16=48145)
- 攻略专题(单机站): `https://www.ali213.net/zt/<slug>/`  (FF16 slug=ff16)
- 长文分页: 首页 `https://gl.ali213.net/html/<YYYY-M>/<id>.html`，第N页加 `_N` 后缀
- 章节目录锚点: `document.querySelectorAll('a')` 里 innerText 匹配 `/^第\d+页/`；DOM 上它们在 `.changtiaodaohang .changtiaobox li a` 下(下拉章节导航)，但按文本过滤更稳。
- 正文容器: **`.content`** (js().innerText 取，返回当前页章节正文)

## 本地 http_get 备份路径(中国IP,实测可用)
两条路返回同一页；http_get 不走云浏览器、跑本地中国大陆IP，未被封。用于纯文本抽取：
```python
import re
resp = http_get("https://gl.ali213.net/html/2023-6/1088235.html")
html = resp['text'] if isinstance(resp,dict) else str(resp)
toc = re.findall(r'第(\d+)页[：:]\s*([^<"]{2,30}?)流程攻略', html)  # -> 28 条,与云浏览器一致
```
时长文章同样 http_get 抓 `35-40小时` 成功。

## Gotchas
- **`.content` 正文里混有页脚新闻/游戏信息卡**(如"小岛秀夫看病"、上市时间、评分等噪声)。攻略正文永远在最前面——取 innerText 后截前 500-600 字符即为纯净章节内容，或在正文与"更多内容："之间截断。
- **http_get 抓到的原始 HTML 头部有大量百度 JSON-LD/cambrian 结构化噪声**，不适合直接给正文；要正文本文优先走云浏览器 `js('.content')`，要 TOC/章数/时长这种可正则的字段则 http_get 更省事。两条路都可，按需求选。
- 站内搜索页 `so.ali213.net/s/c` 结果高度混杂(下载/MOD/补丁/新闻/攻略全混)，别在里面翻找流程攻略；直接用 `sub=96` 攻略过滤或走 `z/<zid>` 专区。
- 香港云出口IP实测**未触发**游侠网任何区域封锁/验证码，页面与本地一致(游侠是中国站，香港IP无地区化偏差)。ali213 全站可正常用云浏览器。
- 分页 URL 首页无 `_1`(是裸 `<id>.html`)，第2页起才是 `_2`…`_N`。批量抓时 range 从空后缀开始。
- 站点混用 http:// 和 https:// 链接，两者都可访问；统一用 https 更稳。
