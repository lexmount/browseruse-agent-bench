Field-tested on 2026-07-04

当当网(dangdang.com)图书搜索抓取：结果列表页(search.dangdang.com)一次性给全所有字段，不用进详情页。列表页 GBK 编码，本地 http_get 和云浏览器 js 两条路实测都能用、返回同样的排序结果。

## Do this first — 搜索列表页就是数据源
搜索 URL 模式（key 是 **GBK+URL-encode** 的关键词，不是 UTF-8）：
```
https://search.dangdang.com/?key=<GBK-urlencoded 关键词>&act=input&sort_type=<排序>
```
- "人工智能" 的 GBK 编码 = `%C8%CB%B9%A4%D6%C7%C4%DC`
- 生成任意关键词：`urllib.parse.quote(kw.encode('gbk'))`
- 排序参数 `sort_type`（实测可用）：
  - `sort_sale_amt_desc` = 按销量降序 ← 任务"销量最高"用这个
  - `sort_pubdate_desc` = 按出版时间降序
  - 默认（不带）= 综合排序
- 出版社等筛选走 `&att=s8589934592%3A<id>`（页面左侧筛选链接里带，一般不需要）
- **没有独立的"年份"或"评分"筛选参数** → 年份/评分在客户端从提取结果里过滤（见下）。

每页 60 条(`ul.bigimg > li`)。列表项字段：书名、作者、出版日期、价格、**星级(星条宽度)**、评论数 全在列表 li 里。**星级映射：星条 span 的 `style.width` 百分比 / 20 = 评分**（80%→4.0，90%→4.5，100%→5.0）。评分"4.5+" = width ≥ 90%。

## 路径A：云浏览器 js 提取（推荐，免编码处理）
```bash
cd /Users/zhu/Developer/browser-harness/.claude/worktrees/friendly-cray-566664 && BU_NAME=wfdangdangcom BU_CDP_WS='<CONNECT_URL>' ./browser-harness <<'PY'
new_tab("https://search.dangdang.com/?key=%C8%CB%B9%A4%D6%C7%C4%DC&act=input&sort_type=sort_sale_amt_desc"); wait_for_load(); wait(2)
r = js(r"""
(() => {
  const out = [];
  document.querySelectorAll('ul.bigimg > li').forEach(li => {
    const a = li.querySelector('.name a');
    const authorRaw = (li.querySelector('.search_book_author')||{}).innerText || '';
    const dateM = authorRaw.match(/(\d{4})-\d{2}-\d{2}/);
    const star = li.querySelector('.search_star_black > span');
    const w = star ? parseFloat(star.style.width) : null;          // 星条宽度%
    const cm = (li.querySelector('.search_comment_num')||{}).textContent || '';
    out.push({
      title:   (a && a.title || '').trim(),
      author:  authorRaw.split('/')[0].trim(),
      price:   (li.querySelector('.search_now_price')||{}).textContent,  // 形如 "¥31.87" / "¥75.10起"
      year:    dateM ? +dateM[1] : null,
      rating:  w!=null ? +(w/20).toFixed(1) : null,                 // 星条%÷20
      comments:parseInt((cm.match(/\d+/)||[0])[0]),                 // "6357条评论"→6357
      url:     a ? a.href : null,
    });
  });
  return out;
})()
""")
# 任务过滤：2023-2024年 + 评分>=4.5，保持销量顺序取前3
top3 = [b for b in r if b['year'] and 2023<=b['year']<=2024 and b['rating'] and b['rating']>=4.5][:3]
import json; print(json.dumps(top3, ensure_ascii=False, indent=1))
PY
```
实测输出(销量排序下取前3命中)：脑机接口(2024,5.0,776评)、我看见的世界·李飞飞自传(2024,4.5,49006评)、图解Web3.0(2024,4.5,12838评)。

## 路径B：本地 http_get（云IP被封时的备份，实测也可用）
本地IP(中国大陆)未被封，返回同样的销量排序。**返回的是 GBK bytes，且 http_get 会抛 UnicodeDecodeError** —— 从异常里取 `e.object` 拿原始 bytes 再 `.decode('gbk')`：
```python
import re, urllib.parse
kw = "人工智能"
url = "https://search.dangdang.com/?key=%s&act=input&sort_type=sort_sale_amt_desc" % urllib.parse.quote(kw.encode('gbk'))
try:
    raw = http_get(url, headers={"User-Agent":"Mozilla/5.0"})
    text = raw.decode('gbk','replace') if isinstance(raw,(bytes,bytearray)) else raw
except UnicodeDecodeError as e:
    text = e.object.decode('gbk','replace')   # e.object = 原始 GBK bytes
# 之后用正则或解析器抽 ul.bigimg li；字段选择器同路径A
prices = re.findall(r'search_now_price[^>]*>\s*¥?([\d.]+)', text)  # 注意 ¥ 在GBK里已解码
```
> 用正则抽星条宽度：`search_star_black.*?width:\s*([\d.]+)%`（贪婪度注意逐li切分）。整段解析仍推荐路径A的DOM方式，本地路径主要用于封IP兜底。

## Gotchas
- **key 必须 GBK 编码**，不是 UTF-8。UTF-8 编码的中文关键词会搜不到/乱码。用 `urllib.parse.quote(kw.encode('gbk'))`。
- **列表页整页 GBK 编码**。http_get 拿到的是 GBK bytes，会触发 UnicodeDecodeError，从 `e.object` 取 bytes 后 `.decode('gbk')`。云浏览器 js() 无此问题（浏览器已按页面编码解码）。
- **评分只在列表页有，且是星条宽度不是数字**。`.search_star_black > span` 的 `style.width`(%)÷20 = 评分。详情页(product.dangdang.com/<id>.html)结构不统一、评分字段实测抽不稳定 —— **不要为评分进详情页**，列表页够用。
- **年份/评分没有 URL 筛选参数**，只能客户端过滤提取结果。出版年份藏在作者串里(`.search_book_author` 的 innerText，形如 `作者 /2024-11-01 /出版社`)，正则 `(\d{4})-\d{2}-\d{2}` 取年。
- **价格可能带"起"**（多规格），形如 `¥75.10起`；取纯数字用 `[\d.]+`。
- 每页 60 条。销量榜靠前的畅销书评论数可上万，可信度足够做 top-N。
- 云出口IP(香港)与本地IP(大陆)对当当返回一致，无地区化差异；两条路互为备份，本任务两条都实测通过。
