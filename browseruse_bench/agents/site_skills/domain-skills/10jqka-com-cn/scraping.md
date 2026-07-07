Field-tested on 2026-07-04

同花顺 (10jqka.com.cn) 股票行情/K线/估值/研报抓取。免登录，混合路径：K线+实时报价+估值用 `d.10jqka.com.cn` 的 JSONP 接口（`http_get` 本地IP即可，不封）；市净率/机构评级/研报标题走 F10 页面（云浏览器 + js 提取，页面是 GBK 编码，http_get 会 UTF-8 解码报错）。

## Do this first

- 股票代码 → 加市场前缀：沪深个股用 `hs_<code>`（如 `hs_300750`）。
- 指数：上证指数(000001) = `zs_1A0001`；深证成指 = `zs_399001`；创业板指 = `zs_399006`。指数**不能**用 `hs_000001`（404）。
- K线、实时报价(高开低价量额)、市盈率、总/流通市值 → 全部走 `http_get` JSONP，最快，实测本地IP不被封。
- 市净率(PB)、机构评级汇总、最新研报标题 → 走云浏览器打开 F10 `worth.html`，用 js 从 innerText 正则抽取。

## 免登录 JSONP 接口（http_get 直接可用）

所有接口返回 `funcname({...json...})`，用 `re.search(r'\((\{.*\})\)', r, re.S)` 剥壳。

### 1. 日K线（含近一周走势）
```python
import json, re
code = "zs_1A0001"          # 指数上证; 个股用 hs_300750
r = http_get(f"https://d.10jqka.com.cn/v6/line/{code}/01/last.js")
obj = json.loads(re.search(r'\((\{.*\})\)', r, re.S).group(1))
# obj['name']=名称; obj['data']=分号分隔的行, 每行逗号分隔:
#   date,open,high,low,close,volume,amount,turnover_rate,...
rows = [x.split(',') for x in obj["data"].split(';')]   # 实测返回约140个交易日
week = rows[-5:]                                          # 近一周(5个交易日)
highs = [float(x[2]) for x in week]; lows = [float(x[3]) for x in week]
first_close = float(rows[-6][4]); last_close = float(week[-1][4])
print("周最高", max(highs), "周最低", min(lows),
      "区间涨跌幅%", round((last_close-first_close)/first_close*100, 2))
```
实测(2026-07-03)：上证指数近一周最高 4143.31、最低 3992.55、区间涨跌幅 +0.41%。

### 2. MACD / KDJ 技术指标（接口不直接给，从 K线 close/high/low 自算，标准参数）
```python
closes=[float(x[4]) for x in rows]; highs=[float(x[2]) for x in rows]; lows=[float(x[3]) for x in rows]
def ema(v,n):
    k=2/(n+1); e=v[0]; out=[e]
    for x in v[1:]: e=x*k+e*(1-k); out.append(e)
    return out
dif=[a-b for a,b in zip(ema(closes,12),ema(closes,26))]; dea=ema(dif,9)
macd=(dif[-1]-dea[-1])*2
macd_sig = "金叉/多头" if dif[-1]>dea[-1] else "死叉/空头"
n=9; rsv=[]
for i in range(len(closes)):
    lo=min(lows[max(0,i-n+1):i+1]); hi=max(highs[max(0,i-n+1):i+1])
    rsv.append(0 if hi==lo else (closes[i]-lo)/(hi-lo)*100)
K=D=50
for x in rsv: K=2/3*K+1/3*x; D=2/3*D+1/3*K
J=3*K-2*D
kdj_sig = ("金叉" if K>D else "死叉")+"/"+("超买" if K>80 else "超卖" if K<20 else "中性")
```
实测(上证指数 2026-07-03)：MACD DIF -4.37 / DEA -1.42 / 死叉空头；KDJ K38.89 D48.65 J19.37 死叉中性。

### 3. 实时报价 + 市盈率 + 总/流通市值（个股）
```python
r = http_get(f"https://d.10jqka.com.cn/v6/realhead/hs_300750/defer/last.js")
it = json.loads(re.search(r'\((\{.*\})\)', r, re.S).group(1))["items"]
# 字段是数字代码，实测确认的映射:
name        = it["name"]        # 宁德时代
last_price  = it["10"]          # 现价(收盘) 380.00   (盘中用 "10"; 前收="6")
high        = it["8"];  low = it["9"]     # 当日高/低
pe_ttm      = it["3153"]        # 市盈率TTM 22.261  (与页面显示的"市盈TTM"一致)
pe_dynamic  = it["2034120"]     # 市盈率(动态) 21.195
pe_static   = it["134152"]      # 市盈率(静态) 24.350
total_mktcap= it["3541450"]     # 总市值(元) 1758118300000 = 17581.18亿
float_mktcap= it["3475914"]     # 流通市值(元) 1617664600000 = 16176.65亿
turnover    = it.get("1771976") # 换手率 0.666
```
市值转"亿"：`float(v)/1e8`。以上映射用云浏览器页面渲染值逐个核对无误。

## F10 页面路径（PB / 机构评级 / 研报标题）—— 必须走云浏览器

`basic.10jqka.com.cn` 是 GBK 编码，`http_get` 会 `'utf-8' codec can't decode` 报错；改用云浏览器打开再 js 读 innerText。

### 市净率 / 市盈率（公司概要页）
```python
new_tab("https://basic.10jqka.com.cn/300750/"); wait_for_load(20); wait(3)
d = js(r"""(function(){var t=document.body.innerText||"";
  function around(k){var i=t.indexOf(k);return i<0?null:t.slice(i-20,i+40).replace(/\s+/g,' ');}
  return {pb:around('市净'), pe:around('市盈率(动态)')};})()""")
# 实测: 市净率 4.49；市盈率(动态) 21.195 / (静态) 24.35
```

### 机构评级汇总 + 最新研报标题（盈利预测页 worth.html）—— 一页拿全
```python
new_tab("https://basic.10jqka.com.cn/300750/worth.html"); wait_for_load(20); wait(3)
d = js(r"""(function(){
  var out={}, t=document.body.innerText||"";
  var m=t.match(/买入\((\d+)\)\s*增持\((\d+)\)\s*中性\((\d+)\)\s*减持\((\d+)\)\s*卖出\((\d+)\)/);
  out.rating = m?{买入:m[1],增持:m[2],中性:m[3],减持:m[4],卖出:m[5]}:null;
  var rows=[], re=/([一-龥]{2,10}证券|[一-龥]{2,10}研究|中金公司|华泰证券)：([^\n]{5,60}?)\s*(20\d\d-\d\d-\d\d)/g, mm;
  while((mm=re.exec(t))&&rows.length<6) rows.push({org:mm[1],title:mm[2],date:mm[3]});
  out.reports=rows;
  // 汇总文字: 截至日期/机构数/预测EPS 也在 innerText 里, 可直接正则取
  var s=t.match(/截至(20\d\d-\d\d-\d\d)，6个月以内共有 (\d+) 家机构/);
  out.summary = s?{date:s[1], n_orgs:s[2]}:null;
  return out;})()""")
```
实测(宁德时代 2026-07-03)：评级 买入32/增持3/中性0/减持0/卖出0；31家机构预测2026年度业绩；
最新研报「兴业证券：业绩超预期，新产品新业务助力公司行稳致远 2026-05-09」、
「国投证券：动力恒强，储能发力，创新引领市场 2026-04-23」、「华泰证券：超级科技日... 2026-04-22」。

## 搜索/定位

- 已知代码时**直接拼 URL**，无需搜索：
  - 行情/K线页 `https://stockpage.10jqka.com.cn/<code>/`
  - F10 概要 `https://basic.10jqka.com.cn/<code>/`，盈利预测 `.../<code>/worth.html`
- 只知名称时：全站搜索 `https://news.10jqka.com.cn/`（或问财 `https://www.iwencai.com/`），但个股任务一般题面已给代码，跳过搜索最省步。

## Gotchas

- **指数代码前缀**：指数用 `zs_`（`zs_1A0001`=上证），个股用 `hs_`。`hs_000001` 会 404，`zs_000001` 也 404，上证只有 `zs_1A0001` / `hs_1A0001` 可用。
- **basic.10jqka 是 GBK**：`http_get("https://basic.10jqka.com.cn/...")` 直接抛 `'utf-8' codec can't decode byte 0xc4`。这些页面（PB/评级/研报）**只能走云浏览器**读 innerText，不要试 http_get。
- **realhead 是数字字段码**，本 skill 已核对好映射；若同花顺改版导致某码取不到，回退到云浏览器打开 `stockpage.10jqka.com.cn/<code>/` 读页面顶栏（"市值/流通/市盈TTM/高/低/开"标签清晰）。
- **F10 页面标题带 🐴 emoji**（如「🐴 宁德时代...」）是页面装饰，不影响正文抽取，忽略即可。
- **MACD/KDJ 接口不直接给**，必须从日K线自算（上面代码实测可跑）。金叉/死叉看 DIF vs DEA、K vs D。
- **`d.10jqka.com.cn` JSONP 实测本地IP(http_get)不被封**，是最快路径；万一将来被封再改成云浏览器内 `js("fetch(...)")`。
- realhead 里 `10`(现价) 收盘后等于收盘价，`6` 是前收盘；盘中要涨跌幅用 `(10-6)/6`。
