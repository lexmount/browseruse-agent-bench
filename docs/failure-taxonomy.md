# Failure Taxonomy 失败归因分类体系

Canonical definition lives in `browseruse_bench/eval/failure.py`
(`FAILURE_TAXONOMY`, `LEGACY_CATEGORY_MAP`, `FAILURE_CLASSIFICATION_SYSTEM_PROMPT`).
This document explains what each code means and how attribution runs.

失败归因把每个判分失败（`predicted_label == 0`）的任务归入三大因果类：
**Harness（agent 框架）/ Model（大模型能力）/ Environment（外部环境）**，
多标签 + 单一主因码（`primary_code`）。Model 类按能力维度切分，
直接服务于"模型哪项能力弱"的诊断。

## H — Harness causes（agent 框架/脚手架的问题）

| Code | Name | 含义 |
|------|------|------|
| H1 | Execution Defect 执行缺陷 | 框架错误处理了**合法的模型决策**：解析/执行格式正确的输出失败、坐标映射缺陷（点击落点与模型选择的元素不一致）、文件/产物写入失败、会话管道 bug |
| H2 | Orchestration Guard Absence 编排护栏缺失 | 框架扣留了模型需要的信息或护栏：动作失败从未回传给模型、模型对页面状态被误导且无卡死检测、预算管理失当。**仅在轨迹能证明是框架侧时使用** |

## M — Model causes（大模型能力/服务的问题，按能力维度）

| Code | Name | 含义 |
|------|------|------|
| M1 | Task Planning 任务规划 | 任务分解/路径规划错误；**显式**要求被忽略（指定网站、字段、输出格式、数量、安全响应——必须能指认具体条款）。"没做完"本身不可编码，要归因到原因 |
| M2 | Page Understanding & Grounding 网页理解与定位 | 误读页面/DOM/截图；选错元素/实体/条目/日期/筛选/排序；页面可用却没落实"最新/最高/最多播放/前 N/日期窗口"等判据 |
| M3 | Evidence Fidelity 证据与幻觉 | 可得信息未提取、字段取错或跨条目张冠李戴、编造/幻觉数值、报告不可验证数据、证据不足就作答 |
| M4 | Error Recovery 错误恢复 | **失败信号已在模型上下文中可见**，却重复相同或等效徒劳的动作、不换策略、耗尽步数/时间预算、放弃剩余子项。卡死循环归这里（除非能证明框架隐藏了错误 → H2） |
| M5 | Tool/Structured Output 工具调用与结构化输出 | 模型产出畸形动作 JSON/非法 tool-call、最终答案或要求的文件结构/格式不合规、缺失最终响应 |
| M6 | Model Service Error 模型服务错误 | agent 自身 LLM 调用的基础设施故障：无响应、API 超时、限流、上下文超长、参数错误、内容过滤拒绝。是服务问题而非推理质量 |

## E — Environment causes（外部网络环境的问题）

| Code | Name | 含义 |
|------|------|------|
| E1 | Bot Defense 反爬风控 | CAPTCHA、Cloudflare/PerimeterX、滑块验证、自动化触发的 403、限流 "Too Many Requests"、风控/异常流量拦截页 |
| E2 | Access Barrier 访问门槛 | 登录墙、会话过期、短信/扫码认证、会员/VIP/付费墙、权限限制、版权或地域访问限制 |
| E3 | Site Limitation 站点限制 | 站点宕机/不可达、404/服务端错误、空 DOM/SPA 渲染失败、缺少所需筛选/数据、目标内容在指定站点确实不存在 |

## 特殊码

| Code | 含义 |
|------|------|
| OTHER | 以上类别均不能刻画核心失败时使用，必须附 `other_phrase` 短语 |
| U | **归因管线自身故障**（判分调用失败/被内容过滤拦截等），由代码兜底赋值，judge 不可选。统计时应单列，不计入 agent 侧失败 |

## 判定规则（写入 judge system prompt）

按顺序判定，消除类间模糊：

1. **先判 E（环境）**：站点/环境是否阻断了必经路径？只要外部障碍实质性参与，就纳入相应 E 码——即使 agent 之后还犯了别的错。
2. **再判 H（框架）**：框架是否错误处理了合法的模型决策（H1）、或可证明地扣留了反馈/护栏（H2）？判据客观：动作历史里模型意图正确，执行效果却不同。
3. **最后判 M（模型）**：其余归到失败的能力维度——规划/要求（M1）、理解与定位（M2）、证据（M3）、错误恢复（M4）、工具/结构化输出（M5）。M6 服务错误不受顺序约束。

平局裁决：

- 卡住行为：失败对模型可见却不调整 → M4；框架对模型隐藏了失败 → H2。
- 点错元素：模型选错元素 → M2；模型选对了但点击落点偏移 → H1。
- 畸形输出：模型产出的 → M5；合法输出被框架处理错 → H1。
- "任务不完整"是结果不是类别：编码其原因。

## 输出 schema

每个失败记录的 `evaluation_details.failure_classification`：

```json
{
  "category": "E1",            // = primary_code，同时写入顶层 failure_category
  "codes": ["E1", "M3"],       // 所有实质性贡献因子（多标签）
  "reasoning": "...",           // judge 的分析过程
  "other_phrase": null,         // 仅 OTHER 时必填
  "legacy_category": "B1",     // 由 primary 经映射表确定性导出
  "raw_response": "..."
}
```

## Legacy 映射（兼容历史 A/B/C 报表）

| 新码 | H1 | H2 | M1 | M2 | M3 | M4 | M5 | M6 | E1 | E2 | E3 | OTHER | U |
|------|----|----|----|----|----|----|----|----|----|----|----|-------|---|
| 旧码 | A2 | A4 | A1 | A1 | A1 | A1 | A2 | A3 | B1 | B2 | C2 | OTHER | U |

历史数据不做原位迁移；用 `bubench attribute --force` 重打即可。

## 使用方法

归因默认在 `bubench eval` 尾部内联执行；也可对已有结果单独打标：

```bash
# 对已有 eval 结果单独跑一次归因（独立打标 pass）
bubench attribute --agent browser-use --data LexBench-Browser \
  --model-id gpt-5.5 --timestamp 20260703_140007 --num-worker 10

# --force：清掉已有标签全量重打（换 judge/换体系后使用）
bubench attribute ... --force

# judge 模型默认读 config.yaml 的 eval 节，可用 --model/--api-key/--base-url 覆盖
```

打标完成后会自动刷新同目录 summary 的 `failure_category_statistics`
（按 `failure_category` = primary_code 统计）。
