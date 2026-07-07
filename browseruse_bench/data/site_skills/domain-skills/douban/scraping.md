# 豆瓣 — 书影音检索与详情提取

`https://www.douban.com` / `book.douban.com` / `movie.douban.com` / `music.douban.com`。
Field-tested on 2026-07-04（未登录状态）。

## Do this first：搜索用 suggest JSON API，详情用浏览器

**搜索定位 subject 不要开浏览器**——每个子站都有免登录的 suggest API，直接 `http_get` 返回 JSON：

```python
import json, urllib.parse
q = urllib.parse.quote("三体")
books  = json.loads(http_get(f"https://book.douban.com/j/subject_suggest?q={q}"))
movies = json.loads(http_get(f"https://movie.douban.com/j/subject_suggest?q={q}"))
music  = json.loads(http_get(f"https://music.douban.com/j/subject_suggest?q={q}"))
# 每项: title, url(subject 详情页), id, year, author_name(书)/episode(剧), pic, type
```

**详情页必须用真浏览器**：`http_get` 详情页只会拿到 ~3KB 的反爬壳页（title 是"豆瓣"，无内容），不报错但数据是假的。浏览器里是完整 SSR HTML。

## 详情页提取（book/movie/music 通用选择器）

```python
new_tab(books[0]["url"])   # 或 goto_url
wait_for_load(); wait(2)
r = js("""
(function(){
  var out = {title: document.title};
  var info = document.querySelector('#info');           // 作者/出版社/出版年/ISBN/导演/主演/发行时间...
  out.info = info ? info.innerText.trim() : null;
  var rating = document.querySelector('strong.rating_num');
  out.rating = rating ? rating.textContent.trim() : null;
  var votes = document.querySelector('a.rating_people span, [property="v:votes"]');
  out.votes = votes ? votes.textContent.trim() : null;
  return JSON.stringify(out);
})()
""")
```

- `#info` 的 innerText 是"作者: 刘慈欣\n出版社: 重庆出版社\n出版年: 2008-1..."格式，按行 split 后 `: ` 分割即可结构化。
- 评分 `strong.rating_num`，评价人数 `a.rating_people span`（如 516371）。

## 短评（详情页内嵌前 5 条）

```python
js("""
(function(){
  var out = [];
  document.querySelectorAll('.comment-item .comment').forEach(function(c){
    out.push((c.querySelector('.comment-content') || c).textContent.trim());
  });
  return JSON.stringify(out);
})()
""")
# 更多短评在 <subject_url>/comments 页,同样选择器
```

## 小组搜索

```python
goto_url("https://www.douban.com/group/search?cat=1019&q=" + urllib.parse.quote("Python学习"))
wait_for_load(); wait(2)
# 结果项 .result, innerText 形如 "python学习小组 17002个成员在此聚集 ..."
# 用正则 (\d+)个成员 提取成员数排序即可找"成员最多"
```

## Gotchas

- **`http_get` 拿详情页是静默失败**：返回 200 和一个假壳页，不看内容长度（<5KB 即假）会误以为成功。
- suggest API 的 `q` 必须 URL 编码中文。
- 结果按相关性排序，"三体"第一条即原著；找"评分最高的那部"（如三体的多个改编影视）需要逐个打开详情页比评分，suggest 结果里没有评分字段。
- 电影详情页导演/主演在 `#info` 内（`[rel="v:directedBy"]` 也可）；播放量豆瓣没有——那是视频平台的数据。
- 未登录即可完成以上全部操作；登录墙只在写操作（打分/发帖）时出现。
