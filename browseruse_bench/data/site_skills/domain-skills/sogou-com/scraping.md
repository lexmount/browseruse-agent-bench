Field-tested on 2026-07-04 (re-verified 2026-07-06)
搜狗网页搜索 (sogou.com/web) — 关键词直达 URL，结果页自带富答案框，官方通知走 /link 重定向解析到源站。

## Do this first (最优路径)
搜狗结果页顶部通常直接渲染一个"富答案框"，把答案(放假日期/调休)以纯文本铺在 `document.body.innerText` 里。对"放假安排"这类事实查询，**不用点进任何结果**，直接从结果页读文本即可拿到日期+调休。若任务要求引用国务院办公厅官方原文，再用 `/link?url=...` 重定向解析到源站(央视/中国政府网等)取全文与发布时间。

反爬：无。云出口IP(香港)访问 sogou.com 正常，无验证码、无 403。走 new_tab+js 即可。

## 站内搜索 (URL 模式，实测可用)
```python
import urllib.parse
q = "2024年春节放假安排"
url = "https://www.sogou.com/web?query=" + urllib.parse.quote(q)
new_tab(url); wait_for_load(); wait(3)
print(page_info())   # title 形如 "🐴 2024年春节放假安排 - 搜狗搜索"
```

## 从结果页直接抽答案 (最快，无需点进)
```python
box = js("""
(function(){
  var body = document.body.innerText;
  var lines = body.split('\\n').filter(function(l){
    return /2月1[0-7]日|放假|调休|国务院/.test(l);   // 换成你任务的关键词
  });
  return lines.slice(0,20);
})()
""")
for l in box: print(l)
```
实测输出含整句富答案，例如：
`2月10日至17日放假调休，共8天。2月4日（星期日）、2月18日（星期日）上班。`
以及 `2024春节放假调休安排：2024年2月10日-17日放假调休，共放假8天…`

## 抽结果标题+链接列表 (实测 selector)
```python
res = js("""
(function(){
  var items = [];
  document.querySelectorAll('div.vrwrap, div.rb, div.result').forEach(function(el){
    var a = el.querySelector('h3 a, a');
    var t = el.querySelector('h3');
    if(a) items.push({title:(t?t.innerText:a.innerText).trim().slice(0,80), href:a.href});
  });
  return items.slice(0,12);
})()
""")
```
`div.vrwrap, div.rb, div.result` 三选一即可命中普通结果块。返回 ~13 条。

## 解析 /link 重定向到源站 (取官方原文+发布时间)
自然结果的 href 是搜狗跳转链 `https://www.sogou.com/link?url=...`，不是真实 URL。直接 new_tab 打开这个跳转链，它会 302 到源站，`page_info()['url']` 就是最终真实地址：
```python
new_tab("https://www.sogou.com/link?url=hedJjaC291NlrwQ__...")  # 从上一步 href 取
wait_for_load(); wait(3)
print(page_info()['url'])   # 实测 -> https://news.cctv.com/2023/10/25/ARTIZmUhElpsYpQHGq2NFPU9231025.shtml
```
落地页(央视网)全文含国务院办公厅通知原文。抽正文+发布时间：
```python
d = js("""
(function(){
  var body = document.body.innerText;
  var pub = (body.match(/20\\d{2}年\\d{1,2}月\\d{1,2}日\\s*\\d{2}:\\d{2}/)||[''])[0];
  var lines = body.split('\\n').map(function(s){return s.trim();}).filter(Boolean)
    .filter(function(l){return /春节|国务院|放假|调休|上班|通知/.test(l);});
  return {pub:pub, key:lines.slice(0,15)};
})()
""")
print(d['pub']); [print('-',l) for l in d['key']]
```
实测结果 (任务1 答案，已核对)：
- 发布时间: `2023年10月25日 09:36` (央视网, 据国务院办公厅)
- 春节: **2月10日至17日放假调休，共8天。2月4日（星期日）、2月18日（星期日）上班。** 鼓励安排职工在除夕(2月9日)休息。
- 来源句: "据国务院办公厅，经国务院批准，现将2024年…放假调休日期的具体安排通知如下。"

## Gotchas
- 结果 href 全是 `/link?url=...` 跳转链，不能当真实 URL 传给别处；要真实地址必须 new_tab 打开后读 `page_info()['url']`。少数结果(如公众号 mp.weixin.qq.com)是明文直链。
- 富答案框内容也散在普通结果块里，用关键词正则过滤 `body.innerText` 比定位具体 selector 稳，页面改版不易失效。
- 无免登录 JSON 接口被找到；搜狗结果页是服务端渲染 HTML，从 `innerText`/DOM 抽即可，够用。
- 香港云出口IP访问 sogou 无地区化偏差、无封锁；本路径全程走 new_tab+js，未用到 http_get。
- title 前缀带 emoji(如 🐴) 是云会话装饰，非页面真实标题，忽略即可。
