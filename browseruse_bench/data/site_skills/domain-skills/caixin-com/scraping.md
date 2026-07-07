# caixin.com scraping

Field-tested on 2026-07-04 (cloud Chrome, HK exit IP) (re-verified 2026-07-06). Caixin 财新网 站内搜索：结果页服务端渲染出标题/作者/日期/频道，用 js() 直接抓 DOM 即可；无免签名的公开 JSON 接口。

## Do this first (最优路径)

站内搜索走 `search.caixin.com`，keyword 用 URL-encode 的中文。页面会重定向到 `/newsearch/search` 并服务端渲染 20 条/页结果。用一个 js() 抓 `li.search_result_item` 即可拿全字段，再按频道过滤。

```bash
BH_LEX_REGION=zh BH_LEX_ISOLATED=1 BU_NAME=wfcaixincom ./bh-lex <<'PY'
import urllib.parse
kw = "货币政策"
url = "https://search.caixin.com/search/search.jsp?keyword=" + urllib.parse.quote(kw)
new_tab(url); wait_for_load(); wait(2)   # 会 302 到 /newsearch/search，结果已渲染

items = js(r"""
(function(){
  var out=[];
  document.querySelectorAll('li.search_result_item').forEach(function(it){
    var a=it.querySelector('h3.sr_title a');
    var dc=(it.querySelector('p.sr_date_channel')||{}).innerText||'';
    var p=dc.split('·');
    out.push({
      title:  a? (a.getAttribute('data-articletitle')||a.innerText.trim()) : '',
      url:    a? a.href : '',
      author: ((it.querySelector('p.sr_author')||{}).innerText||'').trim(),
      date:   p[0]?p[0].trim():'',      // 例 "2024年9月29日"
      channel:p[1]?p[1].trim():''       // 例 "金融频道"
    });
  });
  return JSON.stringify(out);
})()
""")
import json
rows = json.loads(items)

# 筛"金融频道"。channel 标签是最可靠的判据；也可用子域名 finance.caixin.com 兜底。
fin = [r for r in rows if r["channel"] == "金融频道"
       or "//finance.caixin.com" in r["url"]]
print("金融频道 first:", json.dumps(fin[0], ensure_ascii=False))
PY
```

实测（keyword=货币政策）第一条金融频道文章：
- title: 央行：加大货币政策调控力度 提高货币政策调控精准性
- author: 文｜财新 王石玉
- date: 2024年9月29日
- url: https://finance.caixin.com/2024-09-29/102241549.html
- channel: 金融频道

## Field map (实测可用的选择器)

单条结果 `li.search_result_item` 内：
- 标题+链接: `h3.sr_title a` — 文本或 `data-articletitle` 属性；`href` 是文章 URL（带 `?originReferrer=caixinsearch_pc`，可去掉）。`data-articleid` 是文章 ID。
- 作者: `p.sr_author`（含前缀"文｜财新 "或"专栏作家 "等，按需 strip）。
- 日期+频道: `p.sr_date_channel`，格式 `2026年7月1日 · 世界频道`，用 `·` 切分。
- 摘要: `p.sr_desc`。
- 缩略图: `div.sr_img img`。

## 频道 = 子域名映射（兜底/交叉验证）

文章 URL 子域名与频道一一对应，可用作 channel 兜底判据：
- `finance.caixin.com` → 金融频道
- `economy.caixin.com` → 经济频道
- `opinion.caixin.com` → 观点频道
- `china.caixin.com` → 政经频道
- `international.caixin.com` → 世界频道
- `weekly.caixin.com` → 《财新周刊》
- `database.caixin.com` → 财新数据通
- `www.caixin.com` → 其他频道（如地产/汽车等，channel 标签给具体名）

## Gotchas

- **首页 www.caixin.com 会因云出口 IP（香港）302 到英文站 caixinglobal.com**。别用首页；直接用 `search.caixin.com` 搜索页——它不受此重定向影响，正常返回中文结果。
- **搜索默认"智能排序"(sort=3)，不是时间倒序**。上面示例返回的"第一篇金融频道文章"是智能排序下的第一条，不等于最新。任务若要"最新一篇"，需按 date 排序（date 是中文"YYYY年M月D日"，解析后比较）；若只要"结果里第一篇"，直接取即可。
- **POST 接口 `gateway.caixin.com/api/dataplatform/common/search` 不可裸用**：无论怎么拼 keyword/category/sort/page 参数都返回 `{"code":1005,"msg":"参数无效"}`，需要站内 JS 生成的签名/头，逆向成本高。放弃走 API，用上面的 DOM 抓取。（GET 的 `web/search/cardData?keyword=` 和 `common/search/category?type=PC_SEARCH` 可直接调，但 category 返回的是"综合/杂志/金融我闻"等内容类型 code，不是文章频道，对本任务无用。）
- **顶部 `ul.channel_menu_list`（综合/杂志/金融我闻…）是内容类型 tab，不是文章频道**。要按"金融频道"筛，用每条结果的 `sr_date_channel` 标签或子域名，别点这个 tab。
- **http_get（本地大陆 IP）未在本次验证**；本 skill 全程走云浏览器 new_tab+js。若云 IP 被限，可改用本地 http_get 拉 `search.caixin.com` 搜索页 HTML 再正则/解析同样的 `li.search_result_item` 结构（结构是服务端渲染，非纯前端 JS 拼装，理论上 http_get 也拿得到）。
- 分页：结果 20 条/页；翻页参数未验证，若需要更多结果再探。
```
