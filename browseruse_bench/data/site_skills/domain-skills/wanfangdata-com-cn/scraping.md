Field-tested on 2026-07-04 — wanfangdata.com.cn (万方数据) 学位论文检索：用 s.wanfangdata.com.cn/thesis?q=<词> 搜索，云浏览器渲染后从 .normal-list 结果项直接读全部字段（标题/作者/单位/年份/被引），排序在 UI 里点，学位/年份筛选用客户端过滤而非 facet。

## 一句话定位
万方是 Vue SPA + gRPC-web(protobuf) 后端。**http_get 拿不到任何数据（只回空壳 HTML）**，必须走云浏览器 new_tab + js。数据全在搜索结果列表项里，无需进详情页。

## Do this first（学位论文检索最优路径）
1. `new_tab("https://s.wanfangdata.com.cn/thesis?q=" + urllib.parse.quote(关键词))` → 结果页，免登录，结果项选择器 `.normal-list`（每页20条）。
   - 频道由路径段决定：`/thesis` 学位论文、`/perio` 期刊、`/conference` 会议、`/patent` 专利、`/claim` 标准 等。全部检索用 `s.wanfangdata.com.cn/paper?q=`。
2. **排序**：点 `.sort-item` 里文字为“被引频次”（或“学位授予时间”/“下载量”）的那个 div → 触发后端重查，列表就地重排。**这条实测有效**。
3. **学位/年份筛选**：不要用左侧 facet 复选框（见 Gotchas，实测点了不重查）。改为**排序后客户端过滤**：结果按被引降序，逐条读 `.essay-type`(硕士论文/博士论文) 和年份，命中 2022-2024 且博士的收集即可。被引降序保证一旦收够 N 条、后续页被引更低不会翻盘。
4. **翻页**：点文字为 `>` 的元素（在 `.page-number` 容器内）→ 下一页，页码看 `x / 554`。

## 可原样跑的提取代码（每个 .normal-list 项的干净字段）
每个结果项有 class 化的子元素，直接取，别正则切文本 blob：
```python
new_tab("https://s.wanfangdata.com.cn/thesis?q=" + urllib.parse.quote("区块链技术"))
wait_for_load(); wait(4)
# 点“被引频次”排序
js("""(() => { const s=[...document.querySelectorAll('.sort-item')].find(e=>/被引频次/.test(e.innerText)); if(s) s.click(); return 'sorted'; })()""")
wait(4)

EXTRACT = r"""(() => {
  const extract = (it)=>{
    const g=s=>(it.querySelector(s)||{}).innerText||'';
    let cited='0'; const q=it.querySelector('.stat-item.quote'); if(q){const m=q.innerText.match(/(\d+)/);cited=m?m[1]:'0';}
    const spans=[...it.querySelectorAll('span')].map(s=>s.innerText.trim());
    const year=(it.innerText.match(/(20\d\d)/)||[])[1]||'';               // 学位授予年
    const inst=spans.find(s=>/(大学|学院|研究院|研究所|党校|学校)$/.test(s))||''; // 学位授予单位
    return {
      title:  g('.title').trim(),
      degree: g('.essay-type').trim(),         // 硕士论文 / 博士论文
      author: g('.authors').trim(),
      inst, year, cited:+cited,
      rid:    g('.title-id-hidden').trim()     // 如 thesis_D03405875
    };
  };
  return [...document.querySelectorAll('.normal-list')].map(extract);
})()"""

rows = js(EXTRACT)                              # 当前页20条结构化结果
# 客户端过滤：博士 + 2022-2024（已按被引降序）
hits = [r for r in rows if r['degree']=='博士论文' and r['year'] in ('2022','2023','2024')]
```
翻下一页再抽：
```python
js("""(() => { const n=[...document.querySelectorAll('*')].find(e=>e.children.length===0 && e.innerText.trim()==='>' && /page/i.test(e.parentElement.className||'')); if(n) n.click(); return 'next'; })()""")
wait(4)
rows2 = js(EXTRACT)
```

## 关键选择器（实测）
- 结果项容器：`.normal-list`（20/页）
- 标题：`.title` ；学位类型：`.essay-type`（“硕士论文”/“博士论文”）；作者：`.authors`
- 被引数：`.stat-item.quote`（文本如“被引 181”，取数字）；下载：`.stat-item.download`
- 记录ID：`.title-id-hidden`（如 `thesis_D03405875` / `thesis_Y3497178`）
- 学位授予单位 + 专业 + 年份：`.authors` 之后的裸 `<span>`；单位=以 大学/学院/研究院/研究所/党校/学校 结尾的那个 span；年份=文本里的 `20\d\d`
- 排序按钮：`.sort-item`（文字匹配“被引频次”“学位授予时间”“下载量”“在线出版时间”“相关度”）
- 结果总数文本：`document.body.innerText.match(/找到[\d,]+条文献/)`
- 学位facet需先展开：点 `.wf-facet-box` 里“授予学位”块的 `.facet-title-box`，展开后才出现 硕士/博士 选项（但选项点击不重查，见下）

## 实测样例结果（区块链技术 / 博士 / 2022-2024 / 按被引降序 Top3）
1. 高盛楠 · 电子科技大学 · 2023 · 被引77 · 《高校思想政治教育数字化发展研究》(thesis_D03405875)
2. 李明月 · 吉林大学 · 2023 · 被引51 · 《数字化转型对中国国有企业高质量发展的影响研究》(thesis_D03379379)
3. 李凌杰 · 吉林大学 · 2023 · 被引40 · 《数字经济发展对制造业绿色转型的影响研究》
（Top20被引里博士2022-2024只有前两条；第3条在第2页。被引降序保证收够即止。）

## Gotchas（都亲测）
- **http_get 完全不可用于取数**：s./d. 两个子域 http_get 都只回 ~165KB 的空壳 SPA HTML，无“找到/normal-list/任何标题”。本地IP(中国大陆)能连通、非被封，纯粹是内容客户端渲染。→ 只能云浏览器 new_tab+js。
- **无友好JSON接口**：真实后端是 gRPC-web，端点 `POST https://s.wanfangdata.com.cn/SearchService.SearchService/search`（还有 `/facet`、`/SearchService.MoreService/showColumnControl`），body 是 protobuf 二进制（形如逗号分隔字节数组，Content-Type application/grpc-web+proto）。手工构造不现实，别走这条。
- **左侧 facet 复选框点了不重查**：展开“授予学位”后勾选“博士”，DOM 上复选框确实变 `ivu-checkbox-wrapper-checked`，但列表/总数不刷新，也无网络请求；没有可见的“确定”按钮兜底（页面里的 `确定` 按钮属其它隐藏弹窗）。反复 .click()/真实坐标点击 都试过，均不重查。→ **放弃 facet，改用“排序+客户端过滤”**，稳。
- **排序 .sort-item 点击有效**，会发后端请求并就地重排（这条与 facet 不同，确认可用）。
- **年份 facet 只显示前3个年度**（2025/2024/2023），2022 及更早要点“更多”才出现——又一条别依赖 facet 的理由。
- **详情页 URL 未跑通**：`https://d.wanfangdata.com.cn/thesis/thesis_D03405875` 在浏览器里被截成 `.../thesis/thesis`（下划线后ID段丢失），未渲染出内容。所幸搜索结果项已含标题/作者/单位/年份/被引全部字段，无需进详情页；如需详情，另找正确ID路由。
- **CDP 抓包技巧**：goto_url/new_tab 会重建页面上下文，页面内 hook 的 fetch/XHR 会被冲掉。要抓初始请求得用 `cdp("Page.enable")` + `cdp("Page.addScriptToEvaluateOnNewDocument", source=...)` 注入拦截脚本（在新文档执行前生效），再导航。注意本仓库 cdp() 用 kwargs：`cdp("Page.addScriptToEvaluateOnNewDocument", source=script)`，不是传 dict。
- **反爬**：本次全程无验证码/无登录墙/无限流，20条/页正常返回。云出口正常访问，未见香港IP封锁（万方非Google系）。
