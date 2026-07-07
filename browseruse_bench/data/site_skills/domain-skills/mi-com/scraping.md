Field-tested on 2026-07-04
小米商城 (mi.com) 商品搜索 + 规格/评分抓取：搜索列表页服务端渲染 HTML，`.goods-item` 卡片可直接 js 提取；销量排序靠点 "销量" 标签（客户端重排，不改 URL），价格区间在 JS 里自己过滤；屏幕/电池/评分要跳到详情三件套页面抓。全程走云浏览器 new_tab+js，无免登录 JSON 接口。

## Do this first (最优路径)
1. **搜索**：`new_tab("https://www.mi.com/shop/search?keyword=<URL编码关键词>&page=1")` —— `/search` 会 302 到 `/shop/search`，20条/页，服务端渲染。
2. **销量排序**：点页内 "销量" 锚点（`javascript:void(0)`，客户端重排，URL 不变），wait 4 秒后重新提取。
3. **价格过滤**：卡片价格直接读，`2000<=price<=3000` 在 JS/Python 里自己筛，取前 3 即"销量最高3款"。
4. **补字段**（屏幕/电池/评分）：卡片没有这些，需按 product_id 打开购买页拿到 specs URL 和 comment goods_id，再分别抓。

## 可原样跑的代码

### 1) 搜索 + 销量排序 + 价格区间 + 取前3
```python
new_tab("https://www.mi.com/shop/search?keyword=%E5%B0%8F%E7%B1%B3%E6%89%8B%E6%9C%BA&page=1")
wait_for_load(); wait(4)
# 点"销量"标签（客户端重排）
js("""(function(){var e=[].slice.call(document.querySelectorAll('a')).find(function(a){return (a.innerText||'').trim()==='销量';});if(e)e.click();return 1;})()""")
wait(4)
import json
data = json.loads(js("""
(function(){
  var items=[].map.call(document.querySelectorAll('.goods-item'), function(el){
    var a=el.querySelector('a'), href=a?a.getAttribute('href'):'';
    var pid=(href.match(/product_id=(\\d+)/)||[])[1]||'';       // 卡片链接里的 product_id
    var title=((el.querySelector('.title')||{}).innerText||'').trim();
    var p=el.querySelector('.price span');                        // 第一个 span=现价，<del> 里是原价
    var price=p?parseInt(p.innerText.replace(/[^0-9]/g,'')):null;
    return {pid:pid,title:title,price:price};
  });
  return JSON.stringify(items);   // 已按当前排序（销量）返回
})()
"""))
top3 = [x for x in data if x['price'] and 2000 <= x['price'] <= 3000][:3]
print(top3)
# 实测结果(2026-07-04, 关键词"小米手机"): 
#   REDMI K90 至尊版 16GB+256GB 2999, REDMI Turbo 5 Max 12GB+256GB 2499, Xiaomi 17T 12GB+256GB 2999
```

### 2) product_id -> 购买页 -> 拿 specs URL + comment goods_id
```python
new_tab("https://www.mi.com/shop/buy?product_id=1230805941")  # /shop/buy 会跳到 /shop/buy/detail
wait_for_load(); wait(4)
links = json.loads(js("""
(function(){
  var out={};
  [].slice.call(document.querySelectorAll('a')).forEach(function(a){
    var t=(a.innerText||'').trim();
    if(t==='参数页') out.specs=a.href;        // https://www.mi.com/prod/<slug>/specs
    if(t==='用户评价') out.comment=a.href;     // https://www.mi.com/shop/comment/<goodsId>.html
  });
  return JSON.stringify(out);
})()
"""))
print(links)  # 实测: {"specs":"https://www.mi.com/prod/redmi-turbo-5-max/specs","comment":".../comment/22527.html"}
# 概述页 = specs 去掉 /specs 尾巴，即 https://www.mi.com/prod/<slug>
```

### 3) 电池容量（specs 页可靠）+ 屏幕尺寸（用概述页）
```python
# 电池 & 分辨率/刷新率：specs 页
new_tab("https://www.mi.com/prod/redmi-turbo-5-max/specs"); wait_for_load(20); wait(3)
spec = json.loads(js("""
(function(){var b=document.body.innerText,o={};
  var m=b.match(/([0-9]{3,5})\\s*mAh/); o.battery_mAh=m?m[1]:null;
  var r=b.match(/分辨率[：:]?\\s*([0-9x× ]+)/); o.resolution=r?r[1].trim():null;
  return JSON.stringify(o);})()
"""))
# 屏幕对角英寸：specs 页对某些机型缺"英寸"字段，改用概述页 /prod/<slug>
goto_url("https://www.mi.com/prod/redmi-turbo-5-max"); wait_for_load(20); wait(3)
inch = js("""(function(){var m=document.body.innerText.match(/[0-9]{1}\\.[0-9]{1,2}\\s*英寸/);return m?m[0]:null;})()""")
print(spec, inch)  # 实测: battery 9000mAh, 屏幕 6.83英寸
```

### 4) 用户评分 / 好评数（comment 页）
```python
goto_url("https://www.mi.com/shop/comment/22527.html"); wait_for_load(20); wait(3)
rating = json.loads(js("""
(function(){var b=document.body.innerText,o={};
  var m1=b.match(/([0-9,]+)人购买后满意/); o.satisfied=m1?m1[1]:null;   // 好评人数(≈销量口径)
  var m2=b.match(/满意度[：:]\\s*([0-9.]+%)/); o.satisfaction=m2?m2[1]:null; // 满意度%
  return JSON.stringify(o);})()
"""))
print(rating)  # 实测 Turbo5Max: {"satisfied":"525736","satisfaction":"100.0%"}
#          Xiaomi 15(goods 20618): {"satisfied":"2317292","satisfaction":"99.9%"}
```

## 关键 ID / URL 映射（实测）
- 搜索卡片链接 = `//www.mi.com/shop/buy?product_id=<PID>`，PID 是 10 位（如 1230805941）。
- 购买页真实落地 = `/shop/buy/detail?product_id=<goodsId>`，goodsId 是 5 位内部号（如 22527），与 PID 不同。
- specs / 概述 页用**产品 slug**（如 `redmi-turbo-5-max`、`xiaomi-15`），不是数字；slug 只能从购买页的"参数页/概述页"锚点拿到，无法从 PID 直接拼。
- comment 页用 **goodsId**（5位），也从购买页"用户评价"锚点拿。
- 所以流程是强制两跳：搜索卡(PID) → 购买页(拿 slug+goodsId) → specs/概述/comment。

## Gotchas（都亲测过）
- **无免登录 JSON 接口可用**。试过 `api.m.mi.com/v1/search/getsearch`、`/shop/search/getsearch`、`api2.order.mi.com/user/comment/list`、`/shop/comment/getCommentList` —— 云浏览器内 fetch 全部 `TypeError: Failed to fetch`（CORS/路径不对）。页面也无 `__INITIAL_STATE__`/`__NUXT__` 等内嵌 JSON，列表纯服务端 HTML。只能 DOM 抓。
- **销量排序不进 URL**：`综合/新品/销量/价格` 都是 `javascript:void(0)`，靠 JS 点击客户端重排/重取，点完必须 `wait(4)` 再提取，否则拿到旧序。
- **价格无 URL 过滤参数**：价格区间只能 JS 侧自己筛。卡片 `.price` 里第一个 `<span>` 是现价，`<del>` 里是划线原价，别抓错。
- **屏幕对角英寸字段不稳**：`/specs` 页对部分机型（如 REDMI Turbo 5 Max）只列分辨率+刷新率，无"英寸"；Xiaomi 15 的 specs 页则有"6.36 英寸"。稳妥做法：英寸优先从概述页 `/prod/<slug>` 正文正则 `[0-9]\.[0-9]+\s*英寸` 抓；电池 mAh 从 specs 页抓（两页都有）。
- **购买页登录墙不挡抓取**：显示"请提前登录"，但商品名/价/版本/规格/tagline(含 mAh) 全部匿名可读，无需登录。
- **本地 http_get 不适用**：这些页面靠 JS 渲染排序/规格，纯 HTML 拉取拿不到销量序和完整规格，必须走云浏览器 new_tab+js。未遇到针对香港云 IP 的封锁，mi.com 云 IP 正常返回（人民币计价、大陆片库无关此站）。
- **多 tab 会拖慢 new_tab**：连开 8+ 个 tab 后 `new_tab` 出现 CDP `TimeoutError`。解决：复用现有空 tab 用 `goto_url(...)` 而非一直 `new_tab`，或及时 `close_tab()`。
