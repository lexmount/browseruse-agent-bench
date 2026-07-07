Field-tested on 2026-07-04 (re-verified 2026-07-06) — wanwang.aliyun.com (阿里云万网) 域名注册查询：查任意域名各后缀的首年注册价 / 续费价 / 可注册状态。

## Do this first (最优路径：搜索结果页 + checkapi JSON)

万网的域名查询走一个免登录 JSON 接口 `https://checkapi.aliyun.com/check/v2/search`。
它需要一个 `umidToken`（阿里风控 token）。**不要自己造 token** —— 最稳的做法是先加载搜索结果页（页面会自动生成 token 并发一次请求），再从 `performance` 资源列表里把 token 抠出来复用。抠到的 token 可对任意关键词复用。

关键词 = 域名主体名（不带后缀），如 `zhangsan2025`。接口一次返回该主体名下所有后缀（.com/.cn/.net/.xyz...）的价格。

```python
new_tab("https://wanwang.aliyun.com/domain/searchresult/?keyword=zhangsan2025"); wait_for_load(); wait(5)
res = js(r"""
(async function(){
  // 1) 从页面已发出的请求里抠 umidToken
  var es=performance.getEntriesByType('resource').map(function(e){return e.name});
  var hit=es.filter(function(n){return n.indexOf('check/v2/search')>-1})[0]||'';
  var m=hit.match(/umidToken=([^&]+)/);
  if(!m) return {err:'no umidToken in performance; 页面可能没加载完，再 wait 几秒重试'};
  var t=m[1];
  // 2) 用该 token 调 JSON 接口（可换任意 keyword）
  var kw='zhangsan2025';
  var url='https://checkapi.aliyun.com/check/v2/search?umidToken='+t+
          '&sceneId=MainCheckPCScene&keyword='+kw+'&extendParams=%7B%7D';
  var r=await fetch(url,{credentials:'omit'});
  var j=await r.json();
  // 3) 抽取目标后缀（这里取 .com）的首年价
  var want=kw+'.com', out=null;
  var panels=(j.data&&j.data.panels)||{};
  Object.keys(panels).forEach(function(k){
    (panels[k].domains||[]).forEach(function(d){
      if(d.domainName===want){
        var dp=d.extendInfo.domainPrice;
        var first=null, renew=null, orig=null;
        (dp.productExtendInfo.productPriceList||[]).forEach(function(x){
          if(x.action==='activate'&&x.period==='12'){first=x.price; orig=x.originalPrice;}
          if(x.action==='renew'   &&x.period==='12'){renew=x.price;}
        });
        out={domain:d.domainName, avail:dp.avail, firstYear:first, originalPrice:orig, renewPerYear:renew, currency:'RMB'};
      }
    });
  });
  return {code:j.code, result:out};
})()
""")
print(res)
# => {'code':'200','result':{'domain':'zhangsan2025.com','avail':True,
#     'firstYear':'85.00','originalPrice':'85.00','renewPerYear':'95.00','currency':'RMB'}}
```

实测结果（2026-07-04）：`zhangsan2025.com` 可注册，**首年注册价 ¥85.00**（续费 ¥95.00/年）。

## JSON 结构速查

`data.panels.<panelName>.domains[]`，每个元素：
- `domainName`：如 `zhangsan2025.com`
- `extendInfo.domainPrice.avail`：true=可注册
- `extendInfo.domainPrice.price`：当前展示价（= 首年活动价）
- `extendInfo.domainPrice.productExtendInfo.productPriceList[]`：完整价目表，逐条含
  `action`（`activate`=注册 / `renew`=续费）、`period`（月，`12`=1年/`36`=3年/`60`=5年）、
  `price`（实付）、`originalPrice`（原价）、`currency`（`RMB`）。
- **首年注册价 = `action==='activate' && period==='12'` 那条的 `price`。**

panel 名不固定（见过 `main`），所以代码里遍历所有 panel、按 `domainName` 精确匹配，别写死 panel 名。

## 备用路径：直接读搜索结果页 DOM 文本（不需要 token）

如果接口路径失效，搜索结果页 `document.body.innerText` 本身就是渲染好的价目文本，可正则抽。
每个后缀块形如：`zhangsan2025.com ... ￥95.00 ￥85.00 节省￥10.00 续费￥95.00/年`
（第一个￥=原价，第二个￥=首年活动价）。DOM 文本路径实测能读到全部后缀，但字段没有 JSON 干净、且促销文案会插在中间，优先用接口。

```python
new_tab("https://wanwang.aliyun.com/domain/searchresult/?keyword=zhangsan2025"); wait_for_load(); wait(4)
txt = js("document.body.innerText")
# 在 txt 里定位 'zhangsan2025.com' 后面紧跟的两个￥金额：原价、首年价
```

## Gotchas

- **umidToken 必需**：不带 token 或空 token → `{"code":"215","message":"umidToken不能为空"}`。裸调接口拿不到数据，必须先加载页面抠 token。token 抠到后可对不同 keyword 复用（实测同一 token 查 `testabc9988`、`example4477` 均返回 code 200）。
- **不要试图用 `window.um.getToken()` 自造 token**：实测该回调不 resolve（js 调用超时）。老老实实从 `performance` 抠页面自己发的那个 token，最省事。
- **wait 要给够**：token 请求是页面异步发的，`wait_for_load` 后还得 `wait(4~5)` 秒，否则 `performance` 里还没有 `check/v2/search` 这条，抠不到 token。
- **走云 IP（new_tab/js）**：本接口在云浏览器（香港出口）里正常返回人民币价、国内域名目录，无区域化偏差，无验证码。未见反爬拦截。`http_get`（本地中国大陆 IP）未测该接口——因为接口强依赖页面生成的 token，纯 http_get 无法自造 token，所以统一走 new_tab+js fetch。
- **免登录**：查询与价格接口无需登录（页面 `queryUserBaseInfo` 会报 `USER_NOT_LOGIN` 但不影响查价）。仅下单才需登录。
- **keyword 只填主体名**（`zhangsan2025`），别带 `.com`；后缀由接口一次性返回全套，在返回里按 `domainName` 挑你要的后缀即可。
- **搜索结果页 URL**：`https://wanwang.aliyun.com/domain/searchresult/?keyword=<主体名>`（实测可直连，无需先走搜索框）。
