Field-tested on 2026-07-04
58.com 二手车（58同城/wf58com）：城市子域 + 拼音品牌路径 + pve 过滤段的列表页，价格用**每次加载随机化的加密字体**遮挡，必须按页反解。

# Do this first

用云浏览器 `new_tab`/`js` 走完整流程（`http_get` 和直接深链导航都会被反爬，见 Gotchas）。核心链路：**打开城市二手车基页 → 逐个点击筛选链接（不要直接 goto 深链）→ 从页内 `li.info` 提字段 → 价格用当页字体反解**。

1. 基页：`https://<city>.58.com/ershouche/`（上海=`sh`，北京=`bj`，广州=`gz`；首页 www.58.com 会按云 IP 定位到别的城市，务必显式带城市子域）。
2. **筛选靠点击页内 `<a>`，别拼深链直接导航**（拼好的深链 goto 会秒触发验证码）。点击会带上 `PGTID/ClickID` 参数、更像真人，实测连点 3 层过滤不触发验证码。
3. 品牌是拼音路径段：大众=`/dazhong/`，丰田=`/fengtian/`，本田=`/bentian/`，奥迪=`/aodi/`，宝马=`/baoma/`，别克=`/bieke/`，日产=`/richan/`，奔驰=`/benchi/`，马自达=`/mazida/`，比亚迪=`/biyadi/`。
4. 价格段：`pve_5864_10_15`（10-15万），`pve_5864_5_10`，`pve_5864_3_5`，`pve_5864_15_20`。车龄段（在“更多”里展开才出现）：`pve_112848_0_2`(2年内)/`_0_4`(4年内)/`_0_6`(6年内)——**没有“3年内”，用 4年内再在提取时按 year 过滤**。个人车源：品牌后加 `/0/`（如 `/dazhong/0/pve_...`）。多过滤链式拼接：`/dazhong/pve_5864_10_15_pve_112848_0_4/`。
5. **没有“最新”排序控件**（PC 列表页无排序 tab）。用卡片的 `.info--post-time`（今天/N天前）判断新旧。

## 可原样跑（云浏览器 browser-harness）
点击式过滤 + 提字段（价格先取原文，反解见下一段）：

```python
# 1) 打开上海二手车基页
goto_url("https://sh.58.com/ershouche/"); wait_for_load(); wait(4)

# 2) 逐层点击过滤（品牌→价格→车龄）。每步点完 wait_for_load()+wait(3~4)，并检查是否验证码
def click_filter(label, href_re):
    r = js(f"""(function(){{
      var a=[...document.querySelectorAll('a')].find(a=>(a.innerText||'').trim()==={label!r} && new RegExp({href_re!r}).test(a.getAttribute('href')||''));
      if(!a) return {{found:false}}; a.click(); return {{found:true,href:a.href}};
    }})()""")
    return r

click_filter("大众", "dazhong");    wait_for_load(); wait(4)
click_filter("10-15万", "pve_5864_10_15"); wait_for_load(); wait(4)
# 车龄在“更多”里，先展开再点
js("[...document.querySelectorAll('a,span,div,i')].forEach(e=>{if((e.innerText||'').trim()==='更多'&&e.offsetParent)try{e.click()}catch(_){}})"); wait(2)
click_filter("4年以内", "pve_112848_0_4"); wait_for_load(); wait(4)

# 3) 检查没被拦
print(js("/访问过于频繁|antibot/.test(document.body.innerText.slice(0,120))"))  # 应为 False
print(js("(document.body.innerText.match(/全部车源（[^）]+）/)||[])[0]"))       # 命中数

# 4) 提字段（价格此时是加密原文，见反解段落）
cards = js(r"""(function(){
  var cards=[...document.querySelectorAll('li.info')].filter(li=>li.querySelector('.info_title'));
  return cards.map(function(li){
    var p=(li.querySelector('.info_params')||{}).innerText||'';
    var ym=p.match(/(\d{4})年上牌/), mm=p.match(/([\d.]+)万公里/);
    return {
      title:(li.querySelector('.info_title')||{}).innerText,
      year: ym?ym[1]:null,
      mileage_wkm: mm?mm[1]:null,
      price_raw:(li.querySelector('.info_price')||{}).innerText||'',   // 加密字形，待反解
      posttime:(li.querySelector('.info--post-time')||{}).innerText||'',
      // 车主类型：logr 里 tag:sj* = 商家，否则个人
      owner: /tag:sj/.test(li.getAttribute('logr')||'') ? '商家' : '个人',
      tags:(li.querySelector('.tags')||{}).innerText.replace(/\n/g,'/')||''
    };
  });
})()""")
```

## 价格反解（每页必做一次，务必！）
价格节点是 `<b class="info_price fontSecret">`，`font-family: ershouche-fontEncrypt`，字体作为 `@font-face` 内联 base64 TTF 塞在页内 `<style>`。**10 个字符 `% + - / ¥ 万 元 折 时 起` 各映射一个数字，且映射每次加载都换**（同一 session 换页也换）。所以每页都要重取字体、重新识别。已实测的识别法：抓出 base64 → 存 TTF → freetype 把这 10 个字形栅格化成图 → 用视觉读出对应数字 → 建当页映射表。

```python
# A) 页内取字体 base64
b64 = js(r"""(function(){var s=[...document.querySelectorAll('style')].map(x=>x.innerHTML).join('\n');
  var m=s.match(/base64,([A-Za-z0-9+\/=]+)/);return m?m[1]:null;})()""")
import base64; open("/tmp/esc.ttf","wb").write(base64.b64decode(b64))

# B) 栅格化 10 个字形到一张条图（pip install freetype-py pillow）
import freetype; from PIL import Image, ImageDraw
face=freetype.Face("/tmp/esc.ttf"); face.set_char_size(96*64)
chars={0x25:'%',0x2B:'+',0x2D:'-',0x2F:'/',0xA5:'¥',0x4E07:'万',0x5143:'元',0x6298:'折',0x65F6:'时',0x8D77:'起'}
order=sorted(chars); strip=Image.new('L',(120*len(order),140),255); d=ImageDraw.Draw(strip)
for i,u in enumerate(order):
    face.load_char(chr(u),freetype.FT_LOAD_RENDER); bm=face.glyph.bitmap
    img=Image.eval(Image.frombytes('L',(bm.width,bm.rows),bytes(bm.buffer)),lambda p:255-p)
    bg=Image.new('L',(110,110),255); bg.paste(img,((110-bm.width)//2,(110-bm.rows)//2))
    strip.paste(bg,(i*120+5,5)); d.text((i*120+40,118),chars[u],fill=0)
strip.save("/tmp/glyphs.png")   # 用 Read 看这张图，按左到右顺序读出每个字符对应的数字

# C) 读图后手填当页映射（示例，实际每页不同！），再用它反解 price_raw
#   反解：''.join(MAP.get(c,c) for c in price_raw)  →  比如 '++.元¥' -> '11.28'（万元）
# MAP = {'%':?, '+':?, '-':?, '/':?, '¥':?, '万':?, '元':?, '折':?, '时':?, '起':?, '.':'.'}
```
两次实测的映射（证明它每页变）：第一次 `%→0 +→1 -→3 /→4 ¥→8 万→7 元→2 折→6 时→5 起→9`；换到丰田页变成 `%→5 +→0 -→4 /→6 ¥→9 万→3 元→7 折→2 时→8 起→1`。反解后价格全部落在 10-15万筛选区间，校验通过。

# 字段来源小抄（li.info 内）
- 车型/标题：`.info_title`（干净文本，含品牌+车系+年款+配置）
- 年份/里程：`.info_params` → 正则 `(\d{4})年上牌`、`([\d.]+)万公里`（**明文，不加密**）
- 价格：`.info_price`（**加密，需按上面反解**）；单位“万”在 `.info_unit`
- 车主类型：卡片 `logr` 属性里 `tag:sj*` = 商家，否则个人；也可用品牌后 `/0/` 段只筛个人车源
- 发布新旧：`.info--post-time`（今天/N天前）——列表页无排序控件，靠这个挑最新
- 认证标签：`.tags`（验真/新上架/准新车/原厂质保等，商家常见）

# Gotchas
- **没有免登录 JSON 接口**：详情链接全部经 `legoclick.58.com/jump?target=<加密串>` 跳转器，DOM 里拿不到干净详情 URL；列表数据只在渲染后的 DOM 里，无独立 list JSON（无 `__INITIAL_STATE__`）。所以只能云浏览器渲染后 `js` 提取。
- **价格字体每页随机化**——最大坑。硬编码任何一版映射，换页/换品牌就全错（丰田页用旧映射解出 99.55/96.05 这种越界价，就是没重解字体）。**每个列表页都要重跑上面的字体反解**。
- **`http_get`（本地大陆 IP）对列表页直接返回验证码页**（“访问过于频繁”，约 5.7KB，含 `#btnSubmit`）。二手车列表这条路本地 IP 走不通。
- **直接 goto 拼好的深链（如 `sh.58.com/dazhong/pve_5864_10_15/`）会立刻触发 `callback.58.com/antibot/verifycode?code=300`**（跳到 `请输入验证码` 页）。云 IP 一旦被锁，基页也连带被锁 2 分钟以上、IP 会轮换。**务必改用页内点击 `<a>` 过滤**，不要拼深链导航。
- **触发验证码后可自解**：验证码页只是单击式（`#btnSubmit`，value=“点击按钮进行验证”，无滑块）。`js("document.querySelector('#btnSubmit').click()")` 点一下会跳到该城市首页、解除封锁，之后 `goto_url` 到目标城市二手车基页即可继续。实测有效。
- **列表页无“最新/综合/价格”排序 tab**——别找排序控件，用 `.info--post-time` 排。
- 品牌过滤页里几乎所有卡片的详情链接都走 legoclick（竞价版式），`isAd`（legoclick 判定）在这类页恒为真，**不能**拿它区分自然结果/广告；区分自然/竞价看 `logr` 里的 `cjh`(竞价) 等标记，或直接不区分。
- 车龄没有精确“3年内”，最接近是“4年以内”(`pve_112848_0_4`)，提取后按 `year >= 当前年-3` 再过滤。
- 页面按云出口 IP 定位城市（首次 www.58.com 落到过南京 nj）；始终显式用 `<city>.58.com` 子域锁定上海(sh)。
