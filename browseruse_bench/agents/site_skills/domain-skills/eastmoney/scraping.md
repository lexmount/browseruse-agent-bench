# 东方财富 — 股票/指数行情与资金流

`https://www.eastmoney.com`。Field-tested on 2026-07-04（含一次真实 IP 封禁事故的复盘）。
**全部数据走免登录 JSON API（`http_get`），不要打开行情页面。**

## ⚠️ 最重要的 Gotcha：push2 有 IP 级风控，触发后不可重试

- 短时间约 10+ 次裸调用 `push2.eastmoney.com` 会触发 **IP 封禁**：症状是 `Remote end closed connection without response`。
- **这不是偶发抖动，重试无用**——封禁覆盖 push2 全部分片（`22.push2...` 等），且**行情页面也会同时瘫掉**（quote/data 页的数字全变 "-"，因为页面自己也靠 push2 的 XHR 喂数）。实测封禁持续 15 分钟以上。
- **遇到该错误立刻切换 `push2delay.eastmoney.com`**（延时行情主机，不在封禁范围，字段完全相同；收盘后延时数据=收盘数据，盘中约延时 15 分钟）。
- 预防：批量查询用 `ulist.np` 一次拉多只，不要循环单只调用。

## 第一步：名称/代码 → secid

secid = `市场.代码`：`0.` 深市（含创业板），`1.` 沪市。002594→`0.002594`，600519→`1.600519`，创业板指 399006→`0.399006`，上证指数 000001→`1.000001`。名称解析用 suggest（该接口不受 push2 封禁影响）：

```python
import json, urllib.parse
q = urllib.parse.quote("贵州茅台")
r = json.loads(http_get(f"https://searchadapter.eastmoney.com/api/suggest/get?input={q}&type=14&count=3"))
secid = r["QuotationCodeTable"]["Data"][0]["QuoteID"]   # "1.600519"
```

## 实时/延时行情（个股与指数通用）

```python
def em_quote(secid, fields="f43,f44,f45,f46,f47,f48,f57,f58,f60,f170"):
    for host in ("push2delay.eastmoney.com", "push2.eastmoney.com"):  # delay 在前更稳
        try:
            return json.loads(http_get(
                f"https://{host}/api/qt/stock/get?secid={secid}&fields={fields}"))["data"]
        except Exception:
            continue
    raise RuntimeError("both push2 hosts blocked")

d = em_quote("0.002594")
# f57 代码  f58 名称
# f43 最新  f44 最高  f45 最低  f46 今开  f60 昨收 —— 全部 ×100（8847 = 88.47 元；指数 401993 = 4019.93 点）
# f47 成交量（手，1手=100股）  f48 成交额（元，原值）
# f170 涨跌幅 ×100（586 = +5.86%）
```

## 批量行情 + 主力资金（首选，一次调用省额度）

```python
d = json.loads(http_get(
    "https://push2delay.eastmoney.com/api/qt/ulist.np/get"
    "?secids=0.399006,1.600519,0.002594&fields=f2,f3,f12,f14,f62"
))["data"]["diff"]
# f2 最新价×100  f3 涨跌幅×100  f12 代码  f14 名称  f62 主力净流入（元，负=净流出）
```

## 分日资金流序列

```python
d = json.loads(http_get(
    "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get?secid=0.399006"
    "&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55&klt=101&lmt=5"
))["data"]["klines"]
# 每行 "日期,主力净流入,小单,中单,大单,超大单"（元）
```

## 其他 Gotchas

- 价格/点位/涨跌幅字段是 ×100 整数，除以 100 再报告；`f48` 成交额是原值。
- f47 单位是**手**，报成交量注意标单位（83.90万手 = 8390万股）。
- 非交易时段返回最近收盘快照，字段照常有值。
- 与同花顺对价：收盘后两边"最新价"必然一致，直接比对即可。
