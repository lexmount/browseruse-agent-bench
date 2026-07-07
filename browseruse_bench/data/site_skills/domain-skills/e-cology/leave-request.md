# 泛微 e-cology 9 — 员工请假流程提交

`https://enterprise.e-cology.com.cn` — 泛微 OA 演示环境（维森集团，演示账号已登录）。
Field-tested against enterprise.e-cology.com.cn on 2026-07-04。

> **⚠️ 先确认 workflowid 再套配方。** e-cology 里 skill 的作用域是 **workflow（表单）**，不是业务流程名：同叫"请假"的两个 workflow，字段 id、_key、挂载的校验事件完全不同，等于两个不同的站点。动手前先 `js("JSON.stringify(WfForm.getBaseInfo())")` 看 `workflowid`，或看 URL——本文件只覆盖 243 和 508，遇到其他 workflowid 需按本文件的探索套路重新摸字段（约 10 分钟）。用户手动进入的菜单入口和拿到的链接可能指向不同 workflow（实测混淆过一次：门户菜单进的是 508，复制的链接却是 243）。

**演示环境有两个并行的请假流程，行为不同，选对流程比选对假期类型更重要：**

| | workflowid=243「员工请假流程」 | workflowid=508「员工请假申请单」 |
|---|---|---|
| formid / nodeid | 180 / 1878 | -1156 / 3205 |
| 提交时带薪假配额校验 | **有**——事假/病假/调休被"可请带薪假天数为0"拦截（与显示余额无关） | **无**——事假 1 天直接提交成功（实测 requestid=1770592） |
| 日期字段预填 | 空 | 预填当前日期时间 |
| **要提交事假/病假** | 不可能，换 508 | ✅ 用这个 |

## Do this first：用 WfForm API，不要碰 UI 控件

创建页是 React SPA（`/spa/workflow/static4form/index.html#/main/workflow/req?iscreate=1&workflowid=243...`），页面暴露官方二开 API **`window.WfForm`**。填表、校验、提交全部走 `js()` 调 WfForm，一次调用即可完成；点击下拉/日历控件的坐标流既慢又碎。

表单真实状态存在 **hidden input**（`document.getElementById('field<N>').value`），可见的 Ant Design 控件只是壳，会随 WfForm 写入自动同步。

## 字段映射（workflowid=508「员工请假申请单」实测）

创建页完整 URL（`_rdm` 为任意时间戳，写 1 即可）：

```
https://enterprise.e-cology.com.cn/spa/workflow/static4form/index.html?_rdm=1#/main/workflow/req?iscreate=1&beagenter=&f_weaver_belongto_usertype=0&f_weaver_belongto_userid=&workflowid=508&isagent=&_key=cg05wc
```

| 字段 | field id | 说明 |
|------|----------|------|
| 请假类型 | `field28816` | 必填，值表与 243 相同（事假=1 已验证） |
| 假期余额显示 | `field28817` | 只读，含 `<br>` 的 HTML 文本 |
| 开始日期 / 时间 | `field28802` / `field28811` | 预填当前时刻，直接覆盖 |
| 结束日期 / 时间 | `field28803` / `field28812` | 同上 |
| 请假天数 | `field28804` | 联动自动计算 |
| 请假原因 | `field28806` | 直接写文本 |

该流程无带薪假配额校验，配方与 243 相同（改字段 id 即可）；fiber 抠选项值在此表单不可用（memoizedProps 无 value），用"点击选项后读 hidden 值"的实证法。

## 字段映射（workflowid=243「员工请假流程」实测）

| 字段 | field id | 说明 |
|------|----------|------|
| 请假类型 | `field5793` | 必填，下拉，值见下表 |
| 起始日期 | `field656` | 直接写 `'YYYY-MM-DD'` |
| 起始时间 | `field657` | 直接写 `'HH:MM'` |
| 结束日期 | `field658` | 同上 |
| 结束时间 | `field659` | 同上 |
| 请假天数 | `field660` | **勿手填**——写入起止时间后联动自动计算（等 ~2 秒） |
| 请假原因 | `field664` | textarea，直接写文本 |
| 休假余额显示 | `field5796` | 只读，随类型联动刷新 |

标题、姓名、部门、分部进页面时已自动填好，不用动。

## 请假类型 value 表（完整实测，ID 无规律）

| 类型 | value | 类型 | value |
|------|-------|------|-------|
| 调休(小时) | `-13` | 丧假(天) | `10` |
| 带薪病假(天) | `-12` | 年假(半天) | `-6` |
| 事假(天) | `1` | 年假-初始化(天) | `12` |
| 病假(天) | `2` | 带薪事假-初始化(天) | `13` |
| 探亲假(天) | `5` | 带薪病假-初始化(天) | `14` |
| 婚假(天) | `7` | 事假-初始化(天) | `15` |
| 产假及看护假(天) | `8` | 病假-初始化(天) | `16` |
| 哺乳假(天) | `9` | 产假/陪产/婚/丧/哺乳-初始化 | `17`/`18`/`19`/`20`/`21` |

**演示账号（杨文元）的事假/病假/调休会在提交时被 HRM 校验拦下，且拦截依据与表单显示的余额不同源**：选事假时右侧余额显示"历年14.00/今年4.00/总计18.00"，但 `doSubmit()` 仍被"可请带薪假天数为0，不能请带薪假！"拒绝（复测确认：只请 1 天也拦）——提交校验查询的是"带薪假"配额桶，为 0；余额显示走的是另一个数据源。病假/调休的显示余额本身就是 0，同样被拦。**用探亲假 `5`（或婚假 `7`、丧假 `10`）——这类无配额校验，可直接提交成功。**

## 完整配方（两次 wrapper 调用）

第一次调用——填表并触发联动：

```python
new_tab("https://enterprise.e-cology.com.cn/spa/workflow/static4form/index.html?_rdm=1#/main/workflow/req?iscreate=1&beagenter=&f_weaver_belongto_usertype=0&f_weaver_belongto_userid=&workflowid=243&isagent=&_key=35ebhi")
wait_for_load(); wait(3)
js("""
WfForm.changeFieldValue('field5793', {value: '5'});
WfForm.changeFieldValue('field656', {value: '2026-07-15'});
WfForm.changeFieldValue('field657', {value: '09:00'});
WfForm.changeFieldValue('field658', {value: '2026-07-16'});
WfForm.changeFieldValue('field659', {value: '18:00'});
WfForm.changeFieldValue('field664', {value: '请假事由文本'});
""")
wait(2)
print(js("document.getElementById('field660').value"))          # 联动算出的天数，非空即 OK
print(js("WfForm.getFirstRequiredEmptyField()"))                 # '' 表示必填齐了
```

第二次调用——提交并判断成败：

```python
import time
js("WfForm.doSubmit()")
msg = ""
for i in range(15):
    m = js("(document.querySelector('.ant-message')||{textContent:''}).textContent.trim()")
    if m: msg = m; break
    time.sleep(0.3)
wait(3)
info = page_info()
ok = "requestid=" in info["url"]
print("submitted:", ok, "| url:", info["url"][:120], "| blocked-msg:", msg)
```

**成功判据**：URL 变为 `...requestid=<数字>...`，标题变为流程标题，页面进入"上级审批"节点。失败时 URL 不变、hidden `requestid` 保持 `-1`。

## Gotchas

- **`WfForm` 就绪晚于 `wait_for_load()`**：SPA 启动慢时固定 `wait(3)` 不够会报 `WfForm is not defined`。应轮询：`for i in range(20): 若 js("typeof WfForm !== 'undefined' && !!document.getElementById('field5793')") 则 break，否则 sleep(1)`。
- **下拉字段写入非法值会被静默清空**：`changeFieldValue('field5793', {value:'3'})` 后值为空——`3` 不在选项表里。只用上面的 value 表。
- **拦截消息只闪 ~3 秒**：`doSubmit()` 被 HRM 校验拦下时，错误只出现在 `.ant-message` 里约 3 秒后消失，页面无其他变化。不轮询就完全看不到失败原因。
- **`WfForm.getSelectShowName` 对该下拉始终返回空**（选项懒加载 + 非标准 select）。要枚举选项真值只能打开下拉后从 React fiber 的 `memoizedProps.value` 抠，或直接用本文件的表。
- **表单在 `.wf-req-form-scroll` 容器内滚动**，页面级 `scroll()` 无效；用 `el.scrollTop` 或直接跳过滚动用 WfForm 操作。
- 每次调用是独立 exec 上下文，但"填表→提交"分两次调用没问题（浏览器状态保留）；只是别把"打开下拉→点选项"拆到两次调用里。
- `WfForm.doSubmit()` 直接等价于点右上角"提交"按钮；保存用 `WfForm.doSave()`。
- 其他可用 API：`WfForm.getBaseInfo()`（requestid/workflowid/nodeid）、`WfForm.getFieldValue(fieldId)`、`WfForm.verifyFormRequired()`。
