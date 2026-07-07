Field-tested on 2026-07-04 — 新浪财经外汇行情：查任意货币对的实时价/今开/昨收/最高/最低/振幅 + 日K历史(近一周走势)，全部走免登录 JSON 接口，无需打开浏览器。

## Do this first (最优路径：纯 http_get，走本地IP，无需云浏览器)

任务里"美元兑人民币"对应 symbol `fx_susdcny`（前缀 `fx_s` + 小写货币代码）。两个接口都免登录、都要带 `Referer: https://finance.sina.com.cn/`：

- **实时快照** `https://hq.sinajs.cn/list=fx_susdcny` → 返回一行 GBK 文本，`最新/今开/最高/最低` 全在里面。
- **日K历史(含近一周走势)** `https://vip.stock.finance.sina.com.cn/forex/api/jsonp.php/var%20_x=/NewForexService.getDayKLine?symbol=fx_susdcny` → 1994 至今全部日线，每根 `date,open,low,high,close`。

hq.sinajs.cn 返回 **GBK 编码**，`http_get()`(它按 utf-8 decode) 会抛 UnicodeDecodeError —— 用下面自带 urllib 的版本手动 `.decode('gbk')`。这两个接口实测从本地IP可直接取；也可在云浏览器页面里跑（页面已在用它们）。

```python
import urllib.request, re
def _get(url, ref="https://finance.sina.com.cn/"):
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0","Referer":ref})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()

symbol = "fx_susdcny"   # USD/CNY. 换货币对只改这里: fx_seurcny(欧元), fx_sgbpcny(英镑), fx_susdjpy(美元日元)...

# --- 1) 实时快照 (GBK) ---
raw = _get(f"https://hq.sinajs.cn/list={symbol}").decode("gbk", "replace")
vals = re.search(r'="([^"]*)"', raw).group(1).split(",")
# 实测字段索引 (对 fx_s* 前缀): 0=时间 5=今开 6=最高 7=最低 8=最新价
snap = {
    "time":  vals[0],
    "last":  vals[8],   # 最新价 (= 当日截至此刻收盘)
    "open":  vals[5],   # 今开
    "high":  vals[6],   # 最高
    "low":   vals[7],   # 最低
    "name":  vals[9],
}
print(snap)   # 实测: {'time':'03:00:02','last':'6.7780','open':'6.7830','high':'6.7837','low':'6.7779','name':'在岸人民币'}

# --- 2) 日K历史，取近一周走势 (UTF-8, JSONP) ---
kl = _get(f"https://vip.stock.finance.sina.com.cn/forex/api/jsonp.php/var%20_x=/NewForexService.getDayKLine?symbol={symbol}")
body = kl.decode("utf-8", "replace")
payload = re.search(r'=\("(.*)"\)', body, re.S).group(1)   # 去掉 JSONP 包壳
bars = []
for row in payload.split("|"):
    p = row.split(",")
    if len(p) >= 5:
        bars.append({"date":p[0], "open":p[1], "low":p[2], "high":p[3], "close":p[4]})  # 注意顺序: 开-低-高-收
week = bars[-5:]   # 最近5个交易日 = 近一周走势
for b in week: print(b)
# 实测最后一根 2026-07-03: open 6.7912 low 6.7675 high 6.7912 close 6.7780
```

字段/顺序都是对着实时页面 (今开6.7830 昨收6.7780 最高6.7837 最低6.7779) 逐一核对过的。快照里没有独立的"昨收"字段，昨收 = 日K倒数第二根的 close（或就用 last，收盘后两者相等）。

## Fallback：云浏览器 DOM 提取（接口被改/被封时）

页面 `https://finance.sina.com.cn/money/forex/hq/<PAIR>.shtml`（如 `USDCNY.shtml`）会把所有字段渲染成文本。用云浏览器 new_tab + js 按中文标签正则抓，实测可用：

```python
new_tab("https://finance.sina.com.cn/money/forex/hq/USDCNY.shtml"); wait_for_load(); wait(3)
r = js("""(function(){
  var body=document.body.innerText;
  var pick=(l)=>{var m=body.match(new RegExp(l+'\\\\s*([0-9.]+)'));return m?m[1]:null;};
  return {last:(body.match(/\\(USDCNY\\)[\\s\\S]*?([0-9]+\\.[0-9]+)/)||[])[1],
          open:pick('今开'), prevclose:pick('昨收'),
          high:pick('最高'), low:pick('最低'),
          amplitude:pick('振幅'), range:pick('波幅')};
})()""")
# 实测: {'last':'6.7780','open':'6.7830','prevclose':'6.7780','high':'6.7837','low':'6.7779','amplitude':'0.0856','range':'0.0058'}
```

## Gotchas

- **GBK**: `hq.sinajs.cn` 返回 GBK，直接 `http_get()`(helper 按 utf-8 decode) 会 `UnicodeDecodeError: 0xd4`。必须自己 urllib + `.decode('gbk')`。日K接口 (`vip.stock.finance.sina.com.cn`) 是 UTF-8，正常。
- **Referer 必带**：两个接口不带 `Referer: https://finance.sina.com.cn/` 会被拒。
- **反爬**：无验证码、无登录墙，本地IP直连即可；日K JSONP 响应头部会塞一段 `/*<script>location.href='//sina.com';</script>*/` 防直接浏览器打开，用正则抠 `=("...")` 里的内容即可，不影响程序解析。
- **symbol 前缀**：直盘/在岸都用 `fx_s`+小写代码；另有一个不带前缀的 `list=USDCNY`（新浪自算），字段索引 5/6/7/8 = 开/高/低/最新 与 fx_s 版一致，二选一都行，fx_susdcny 更稳。
- **DNS 失效的旧接口**（别用，实测 `nodename nor servname` 解析失败）：`gu.sina.com.cn/global/finance/foreign/kline`、`gu.sina.com.cn/api/openapi.php/GlobalService.getDayKLineNew`。当前有效的日K入口就是上面 `vip.stock.finance.sina.com.cn/forex/api/jsonp.php/.../NewForexService.getDayKLine`。
- **云出口是香港IP**：本次页面(new_tab)从香港IP也能正常加载 hq.sinajs.cn，未见对新浪的香港封锁；实时接口用本地 http_get 即可，无需为此站切云。
