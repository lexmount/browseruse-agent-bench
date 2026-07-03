# Failure Taxonomy 失败归因分类体系

Canonical definition lives in `browseruse_bench/eval/failure.py`
(`FAILURE_TAXONOMY`, `LEGACY_CATEGORY_MAP`, `FAILURE_CLASSIFICATION_SYSTEM_PROMPT`).
This document explains what each code means and how attribution runs.

失败归因把每个判分失败（`predicted_label == 0`）的任务归入三大因果类：
**Harness（agent 框架）/ Model（大模型）/ Environment（外部环境）**，
多标签 + 单一主因码（`primary_code`）。

## H — Harness causes（agent 框架/脚手架的问题）

| Code | Name | 含义 |
|------|------|------|
| H1 | Protocol/Artifact Breakdown 协议/产物故障 | 框架无法执行的动作输出：tool-call 结构错误、动作解析失败、最终响应缺失、文件保存失败、产物损坏或要求的输出文件未生成 |
| H2 | Interaction Execution Failure 交互执行故障 | agent **决策正确但执行层机械性失败**：坐标错位、点击落在错误元素、文本输错输入框、下拉/日期控件操作失败、元素定位实现缺陷。判据：动作历史里能看到正确的交互意图 |
| H3 | Orchestration Breakdown 编排失控 | 框架未能检测/恢复卡死状态：**同一动作重复且页面状态无变化**、重复失败后不换策略、循环中耗尽步数/超时预算、长多子项任务因调度失控被放弃 |

## M — Model causes（大模型自身的问题）

| Code | Name | 含义 |
|------|------|------|
| M1 | Requirement Following 需求遵循 | **显式**任务要求被忽略：指定网站、必填字段、输出格式、数量要求、安全/合规响应。**必须能指认被违反的具体条款**；"任务没做完"本身不可编码，要归因到导致没做完的原因 |
| M2 | Target Selection 目标选择 | 选错范围/实体/日期/城市/条目/频道/季/商品/排序标准/筛选/比较逻辑；或**动作不断变化却始终接近不了目标的徒劳策略**。页面可用但走错路 |
| M3 | Evidence Grounding 证据落地 | 可得信息未提取、字段取错或跨条目张冠李戴、编造/幻觉数值、报告不可验证数据、证据不足就作答 |
| M4 | Model Service Error 模型服务错误 | agent 自身 LLM 调用的基础设施故障：无响应、API 超时、限流、上下文超长、参数错误、内容过滤拒绝。是服务问题而非推理质量问题 |

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
2. **再判 H（框架）**：agent 是否**尝试了正确操作但机械性失败**（H2）、协议/产物崩坏（H1）、或**重复同一无效动作不恢复**（H3）？"尝试判据"是客观的：意图在动作历史里可见。
3. **最后判 M（模型）**：页面可用且执行正常时才归推理层——违反显式要求（M1）、目标/策略错误（M2）、证据问题（M3）。M4 服务错误不受顺序约束。

平局裁决：

- 卡住行为：同一动作重复且状态无变化 → H3；动作在变化但策略徒劳 → M2。
- 从未尝试所需交互 → M（推理层）；尝试了但机械性失败 → H2。
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

| 新码 | H1 | H2 | H3 | M1 | M2 | M3 | M4 | E1 | E2 | E3 | OTHER | U |
|------|----|----|----|----|----|----|----|----|----|----|-------|---|
| 旧码 | A2 | A2 | A4 | A1 | A1 | A1 | A3 | B1 | B2 | C2 | OTHER | U |

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
