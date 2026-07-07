# Site-skills A/B experiment report — 2026-07-07

Skill/no-skill A/B for the bench-level site-skills prompt injection
(design: [2026-07-07-site-skills-injection-design.md](2026-07-07-site-skills-injection-design.md)).

## Setup

- Benchmark: LexBench-Browser, first_n 20 (task ids 5-42), lexmount backend
  (CN egress; zh/en profile routing), one arm = one `bubench run`.
- Arms: control (`--site-skills off`) vs skill (`--site-skills on`); the only
  difference is the injected site-knowledge section in the task prompt.
- Skill library: vendored snapshot `browseruse_bench/agents/site_skills/`
  (browser-harness domain-skills @ 8d0684a; 20/20 tasks matched a skill;
  median injection ~6k chars, p90 ~20k).
- Agents: browser-use (gpt-5.4), openclaw (gpt-5.4), claude-code
  (dmx-claude-opus-4-8 via strip proxy).
- Judge: gpt-5.4 (multimodal, screenshots included), threshold 60. An earlier
  GLM-5.2 text-only pass is retained in separate result files for reference;
  all numbers below are gpt-5.4.
- Run dirs (`experiments/LexBench-Browser/All/...`): browser-use
  20260707_113324/114522, openclaw 20260707_115337/120414, claude-code
  20260707_121653/123232.

## Headline

| agent | 完成率 对照→技能 | 翻正 | 翻负(核验后) | 双成题上的效率 |
|---|---|---|---|---|
| browser-use | 11/20 → **13/20 (+10pp)** | 3 | 1(贴线波动) | 步数 −36%~−71%,token 同降 |
| openclaw | 9/20 → **11/20 (+10pp)** | 4 | 2(1 运行波动 + 1 判分噪声) | 无改善(步数/token 略升) |
| claude-code | 6/9 → 5/9(有效配对仅 9 题) | 1 | 2(贴线) | 有效题步数约减半 |

**核心发现:同一批 skill,两种收益形态。**

- browser-use(执行模型自带绕墙能力):skill 的价值是**效率**——双成题
  平均步数近乎减半;完成率也 +2(amazon/iqiyi/ebay 翻正)。
- openclaw(老实的逐页浏览者):skill 的价值是**完成率**——对照组在
  yelp/scholar/amazon/wenku 等强反爬站点快速交白卷,技能组照 skill 的
  fallback(如 DuckDuckGo HTML snippet、站点 API)把白卷变成正确答案
  (翻正 4 题,且技能组答案中的绕墙 URL 与 skill 文档示例逐字一致——
  证明 skill 被真实执行,而非碰巧);代价是步数/token 上升(慢速成功
  取代快速失败)。
- claude-code:技能组 11/20 任务被模型额度耗尽阻断(见"基础设施事件"),
  剩余样本太小,不下结论;有效题上的效率增益方向与 browser-use 一致。

## Per-case tables (gpt-5.4 judge)

判定说明:双败 = 两组都被站点风控/封锁挡死(bilibili 412、youku/ign 风控、
yelp DataDome 直连、walmart),此类题的步数差异不计为 skill 效果。

### browser-use

```
 id 站点                 对照   技能    步数   判定
  5 xiachufang          成76  成64    4/4   双成
  6 bilibili            败31  败42    4/4   双败
  7 image.baidu         成95  成91    7/3   双成
 17 amazon              败59  成89    5/8   翻正
 18 iqiyi               败57  成100  18/6   翻正
 19 youtube             成65  成65   14/9   双成
 21 yelp                败 7  败 0    4/3   双败
 22 wenku.baidu         成75  成85   15/8   双成
 23 scholar.google      败20  败30    4/3   双败
 24 eastmoney           成75  成79    7/2   双成
 25 ign                 败15  败20   11/7   双败
 27 ebay                败56  成83    8/3   翻正
 28 v.qq                成82  成100   8/4   双成
 29 imdb                成100 成100   4/4   双成
 35 steam               成91  成98   11/5   双成
 37 walmart             败15  败48    8/16  双败
 38 youku               败 0  败 0   21/3   双败
 39 crunchyroll         成65  败60   10/4   翻负(贴线)
 41 airbnb              成78  成84   12/4   双成
 42 baidu               成100 成96    4/2   双成
通过 11/20 -> 13/20 | 翻正3 翻负1 双成10 双败6
token: 1.28M -> 1.09M (对照/技能, 全20题)
```

### openclaw

```
 id 站点                 对照   技能    步数   判定
  5 xiachufang          败42  败57   11/11  双败
  6 bilibili            败50  败38   17/16  双败
  7 image.baidu         成80  成92   16/15  双成
 17 amazon              败49  成74   13/18  翻正
 18 iqiyi               败 5  败37   17/18  双败
 19 youtube             败10  成60   14/17  翻正
 21 yelp                败 0  败29    6/16  双败
 22 wenku.baidu         败10  成65   18/18  翻正
 23 scholar.google      败10  成84   13/20  翻正
 24 eastmoney           成92  败18   14/13  翻负(技能组页面读取连续超时,运行波动)
 25 ign                 成96  成94   18/21  双成
 27 ebay                败 5  败38    7/14  双败
 28 v.qq                成72  成65   15/18  双成
 29 imdb                成100 成80   14/12  双成
 35 steam               成68  败25   16/13  翻负(两组答案实质相同,判分噪声)
 37 walmart             败61  败32   18/14  双败
 38 youku               败 0  败 0    7/8   双败
 39 crunchyroll         成82  成84   18/15  双成
 41 airbnb              成67  成88   22/12  双成
 42 baidu               成85  成100  11/17  双成
通过 9/20 -> 11/20 | 翻正4 翻负2 双成7 双败7
token: 5.86M -> 6.78M
```

### claude-code(有效配对 9 题)

```
 id  对照   技能    步数   判定
  6  败40  败41   19/4   双败
  7  成85  成94    7/6   双成
 17  败50  败54    6/5   双败
 24  败62  成89    7/7   翻正
 27  成79  败60   14/13  翻负(贴线)
 29  成96  成99   14/8   双成
 39  成85  败46   12/7   翻负
 41  成80  成78   19/6   双成
 42  成89  成100   7/8   双成
通过 6/9 -> 5/9(样本不足,不下结论)
其余 11 题技能组被 dmx-claude-opus-4-8 额度耗尽阻断(对照组恰在可用窗口完成)。
```

## 与 browser-harness 原始 A/B(Haiku 4.5)的关系

原始实验(harness 仓库 docs/lexbench-benchmark):skill 使 Haiku 成功率
+14.6pp、token −60%。本轮 gpt-5.4/强模型上完成率 +10pp、token 变化因
agent 而异。合并解读:**执行模型越弱,skill 越换成功率;执行模型越强,
skill 越换效率;老实型 harness(openclaw)不论模型强弱都靠 skill 换成功率**。

## 基础设施事件与工程产出

- claude-code 技能组 11 题失败根因 = LiteLLM 网关把 dmx-claude 模型的
  **额度耗尽**表现为 500 NoneType 崩溃且阈值随状态漂移(issue #84 三条
  评论:复现、阈值漂移、额度根因)。strip proxy 获得 stream→非流式转换
  + SSE 合成、tool_result 图像剥离两项加固。
- 本轮修复/立项的 bench 缺陷:
  - `build_task_prompt` 丢弃预组装 prompt(5 个 agent 静默失去注入)——
    已修(f9a9611);
  - `--force-reeval` 结果 JSONL 追加污染 summary——已立项;
  - root logger 重复 handler、dry-run 落盘空目录、action_history 语义
    不统一——已立项;
  - GLM 纯文本 judge 支持 cherry-pick(b9e2e9e)。
- result.json 现将注入与任务分离记录(`task` 为纯任务,`site_skills`
  记命中文件与字符数,7a8b644)。

## 已知局限

1. n=20、单次运行,±1 题 ≈ ±5pp;翻正/翻负均做了答案原文核验,但
   完成率差异仍在噪声边缘,放大结论需全量 split 复跑;
2. lexmount CN 出口对部分 en 站点(amazon/walmart/ign/youku/bilibili)
   有系统性风控,压低所有 arm 的绝对成功率(双败题 6-7 个);
3. judge(gpt-5.4)存在贴线波动(60 分阈值附近);
4. claude-code 数据不完整(额度事故),不进结论;
5. openclaw 的 token 成本本身是 browser-use 的 ~5 倍,skill 未改善之,
   若在意成本应优先优化其浏览循环而非 skill。

## 追加:openclaw 第三 arm — native 自管 skill(20260707_162026)

`--site-skills native`:命中 skill 安装为 OpenClaw workspace skill
(`<workspace>/skills/site-knowledge/SKILL.md`,`openclaw skills list` 确认
indexed/ready),prompt 不注入,模型自行决定是否 `read`。

```
通过:   对照 9/20 | 注入 11/20 | native 8/20
步数:   285      | 306        | 329
token:  5.86M    | 6.78M      | 8.40M
skill 阅读率(native): 13/20 主动读取
读取与通过无相关: 读且过 5/13(38%) vs 未读且过 3/7(43%)
```

关键差异 case:amazon(注入翻正,native 没读 skill → 败)、youtube(native
读了仍败)、crunchyroll/baidu(native 败于对照/注入都过的题,疑似运行波动)。

结论:**在 gpt-5.4 + openclaw 上,自管模式是"两头亏"**——付出了 skill 索引
的系统开销 + 读取回合 + 中途注入的上下文(token 比注入 arm 还高 24%),但
知识进入上下文的时机晚于规划期(注入 arm 在第一步前就有知识),且 35% 的
任务根本没读。单次运行方差不小(2 题疑似波动),但方向明确:**如果要给
openclaw 上 skill,推荐 prompt 注入而非自管发现**。

## 追加:codex A/B(20260707_180953 对照 / 20260707_184249 技能)

模型 gpt-5.5(网关仅部分模型组支持 codex 必需的 /v1/responses;gpt-5.4 后端
404)。判分 gpt-5.4;codex 不落截图(缺口已立项),judge 走产物证据路径,
两组同口径。前置排障:codex CLI 重装;`_run_subprocess` stdin 改为 DEVNULL
(88d5f74)——codex exec 会读 stdin 到 EOF,批次脚本下继承的开放 stdin 使
整个 arm 20 题全部 0 步超时。

```
 id 站点                 对照   技能    步数   判定
  5 xiachufang          败37  败55    9/7   双败
  6 bilibili            败24  败32   13/6   双败
  7 image.baidu         成85  成89    7/5   双成
 17 amazon              成64  成83   12/8   双成
 18 iqiyi               败10  成88   70/7   翻正(70步失败→7步做对)
 19 youtube             成71  败44   17/16  翻负(两组答案实质相同,判分噪声)
 21 yelp                败23  败28    7/4   双败
 22 wenku.baidu         败 8  成92   11/4   翻正
 23 scholar.google      成79  败20   15/6   翻负(技能组撞反爬,对照组未撞)
 24 eastmoney           成94  成80    6/5   双成
 25 ign                 成93  成90   21/14  双成
 27 ebay                成69  败56   50/15  翻负(贴线;两组均给出实质结果)
 28 v.qq                败30  成74   20/6   翻正
 29 imdb                成100 成78   15/8   双成
 35 steam               成84  成98   18/12  双成
 37 walmart             成83  败56   25/17  翻负(贴线)
 38 youku               败 2  败 0    6/4   双败
 39 crunchyroll         败18  败48   16/6   双败
 41 airbnb              成77  成93   18/7   双成
 42 baidu               成86  成91   25/6   双成
通过 12/20 -> 11/20 | 翻正3 翻负4(1噪声+2贴线+1反爬方差) 双成8 双败5
步数 381 -> 163 (−57%) | token 13.1M -> 5.5M (−58%) | 空答案 3 -> 0
```

codex 结论:**完成率持平(差异在判分噪声带内),效率收益是四个 agent 中
最大的**——步数与 token 双双近乎减半,且对照组的 3 个空答案(70/50 步耗尽
超时的迷路任务)在技能组全部消失。形态与 browser-use 一致:强模型 + 直接
消费任务 prompt 的 agent,skill 的价值兑现为效率。

## 建议下一步

1. browser-use + openclaw 全量 split(约 210 题)复跑两 arm,固化结论;
2. openclaw 增加"撞墙后主动查 skill fallback"的提示词强化,可能把
   双败题(yelp/ebay 直连失败类)再翻回一部分;
3. skill 覆盖缺口与站点变更(wenku 排序、wenku 文档差异)回流到
   browser-harness 仓库的 skill 维护流程。
