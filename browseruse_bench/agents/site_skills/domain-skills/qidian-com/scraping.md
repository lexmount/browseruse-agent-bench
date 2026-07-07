Field-tested on 2026-07-04 (retested 2026-07-06) (re-verified 2026-07-06)

起点中文网 (qidian.com) 小说搜索/书籍元数据抓取。搜一本书，拿作者、总字数、连载状态、书籍详情页 URL。

## Do this first — 单页搞定（搜索结果页直接出全字段）

搜索 URL 模式：`https://www.qidian.com/so/{URL编码关键词}.html`。首条结果 `.res-book-item` 就含标题、作者、状态、总字数，一次页面加载全拿到，无需再进详情页。

```python
import urllib.parse
kw = "诡秘之主"
url = "https://www.qidian.com/so/" + urllib.parse.quote(kw) + ".html"
new_tab(url); wait_for_load(); wait(2)

r = js("""(function(){
  var li=document.querySelector('.res-book-item');   // 首条 = 最相关
  if(!li) return {error:'no result'};
  var t  = li.querySelector('.book-info-title a');
  var au = li.querySelector('.author a.name');
  var spans = li.querySelectorAll('.author span');            // 最后一个 span 是状态
  var status = spans.length ? spans[spans.length-1].innerText.trim() : '';
  // 总字数：.total 第一段；用正则从整块文本抽最稳
  var words = (li.innerText.match(/([\\d\\.]+万)总字数/)||[])[1] || '';
  return {
    bid:        li.getAttribute('data-bid'),        // "1010868264"
    title:      t  ? t.innerText.trim() : '',       // "诡秘之主"
    author:     au ? au.innerText.trim() : '',      // "爱潜水的乌贼"
    status:     status,                             // "完结" / "连载"
    words:      words,                              // "446.77万"（字数，非章节数）
    detail_url: t ? ('https:'+t.getAttribute('href')) : ''  // //www.qidian.com/book/1010868264/
  };
})()""")
print(r)
```

实测输出（诡秘之主）：
`{'bid':'1010868264','title':'诡秘之主','author':'爱潜水的乌贼','status':'完结','words':'446.77万','detail_url':'https://www.qidian.com/book/1010868264/'}`

### 遍历多条结果
把 `document.querySelector('.res-book-item')` 换成 `document.querySelectorAll('.res-book-item')` 循环即可。搜索结果按相关性排序，`data-bid` 是书籍 ID，`//my.qidian.com/author/{id}/` 是作者主页。

## 备选：书籍详情页（当搜索结果不含所需字段时）

`https://www.qidian.com/book/{bookId}/`。实测可用选择器（其余 h1/writer 结构与老版不同，用这些）：

```python
new_tab("https://www.qidian.com/book/1010868264/"); wait_for_load(); wait(2)
r = js("""(function(){
  var q=function(s){var e=document.querySelector(s);return e?e.innerText.trim():null};
  return {
    title:  q('#bookName'),                                 // "诡秘之主"
    status: q('.book-attribute span'),                      // 首 span = "完本"/"连载"
    author: q('a.writer-name'),                             // "爱潜水的乌贼"（勿用 #authorId，那是头像链接，文本是"白金"等级）
    words:  (document.querySelector('.book-info').innerText.match(/([\\d\\.]+万字)/)||[])[1], // "446.77万字"
    update: (document.querySelector('.book-info').innerText.match(/更新时间:([\\d\\- :]+)/)||[])[1] // "2022-11-25 16:27:26"
  };
})()""")
print(r)
```
注意详情页字数带"字"后缀（`446.77万字`），搜索页不带（`446.77万`）。详情页状态用词是"完本"，搜索页是"完结"——同义。

## Gotchas

- **页面 title 是 `🐴` 一个马头 emoji**，别把它当加载失败/被封的信号——正文内容正常渲染，用 `js()` 读 DOM 即可。
- **`http_get` 不适用**：站点有 Tencent 验证码（TCaptcha.js）+ Baidu 统计埋点，且详情/搜索是 JS 渲染。走云浏览器 `new_tab`+`js` 稳定，本次全程无验证码拦截（云 IP=香港，起点不封香港）。
- **免登录 JSON 接口试过两个，均不可用**（无需登录也拿不到数据）：
  - `https://www.qidian.com/ajax/book/category?bookId=...` → `{"code":1,"msg":"失败"}`（需 _csrfToken）
  - `https://druidv6.if.qidian.com/argus/api/v1/bookdetail/get?bookId=...` → `{"Result":-3,"Message":"设备信息错误"}`（需设备签名）
  没找到可免登录直取书籍元数据的 JSON 接口——用上面的 HTML 抓取路径。
- **详情页作者名用 `a.writer-name`**，不要用 `#authorId`（那是头像 `<a>`，innerText 是作家等级如"白金"，不是名字）。
- **字段单位**：`words` 是总字数（如 446.77万字），不是章节数；同一 li 里 `3684.46万总推荐` 是推荐票，别混淆——用带"总字数"/"字"锚点的正则区分。
- **数字取小数点原样**：起点字数是估算值，直接返回字符串（"446.77万"），不要强转数字丢精度。
