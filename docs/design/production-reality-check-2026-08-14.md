# 生产实况核查（2026-08-14）

## 0. 这份文档是什么

一次只读的生产核查，起因是用户观察到"她怎么没动静了"。核查范围：主动联系为什么全线失败、
`harness-restructure-execution-plan.md` 的 H1–H16 到底落地了多少、当前世界能不能支撑
`girl-agent-design-intent.md` §9 场景库里承诺的体验。

本文件是**专项证据**，不是路线图。当前权威业务与架构意图仍只有
[`girl-agent-design-intent.md`](./girl-agent-design-intent.md)，唯一执行计划仍只有
[`harness-restructure-execution-plan.md`](./harness-restructure-execution-plan.md)。本文与两者冲突时以两者为准；
本文提出的修复建议须先进入执行计划才产生任务。

核查口径：只读打开 `data/companion.epoch2.sqlite`，只读 `http://127.0.0.1:8787/health` 与
`http://127.0.0.1:8765/health`，读 `logs/napcat.err.log`、`logs/daemon.err.log`，不写库、不重启进程、不改代码。
核查时刻：2026-08-14 15:30–16:00（UTC+8）。

> **接手动手的 agent 请直接跳到 [§9 接手施工单](#9-接手施工单)**，其中 §9.0 是必读的环境事实
> （生产跑的就是这个 worktree 的代码，改完 kickstart 即生效）。§1–§8 是根因与证据，
> 施工时按 §9 各任务里的「为什么」回链即可，不必从头读。

## 1. 一句话结论

**世界是活的，但她的嘴被一道 11 秒的门夹住了；同时这个 epoch 才 14 小时大，生活器官大多还没通电。**

24 小时内她 3 次想主动联系用户，3 次都在第 11.0 秒被取消，一条都没发出去；三次的模型输出都已完整生成并计费，
然后被丢弃，账本里连"她说过话"的痕迹都没留下。

## 2. 主动联系全线失败：11 秒重选窗口

### 2.1 现象

`http://127.0.0.1:8787/health` 的 `scheduler.initiative.reliability_24h`：

```json
{"attempt_count": 3, "consideration_count": 3,
 "technical_failure_attempt_count": 3, "model_silent_count": 0,
 "authorized_count": 0, "delivered_count": 0,
 "model_decision_success_rate": 0.0, "technical_failure_rate": 1.0,
 "visible_delivery_rate": 0.0,
 "technical_failure_codes": {"primary_timeout": 3},
 "warning": true, "warning_reasons": ["technical_failures_24h"]}
```

`model_silent_count: 0` 是关键：**她一次都没有选择沉默**。三次都是想说话的，三次都失败在系统侧。

### 2.2 不是 provider 慢

`world_v2_model_usage` 里三次主动联系全部成对出现，每对都是「一次 succeeded + 一次 caller_cancelled」：

| 起 | primary 延迟 | 第二段延迟 | 合计 |
| --- | ---: | ---: | ---: |
| 21:44:52.235Z | 5660 ms（成功，10710 tok，¥0.0095） | 5325 ms（cancelled） | **10.995 s** |
| 01:44:57.641Z | 7921 ms（成功，11168 tok，¥0.0104） | 3061 ms（cancelled） | **10.990 s** |
| 01:46:08.822Z | 4183 ms（成功，10784 tok，¥0.0094） | 6806 ms（cancelled） | **10.994 s** |

三次跨度都是 10.99 秒，毫秒级重合。provider 从未超时——primary 全部在 4–8 秒内正常返回并计费。

### 2.3 第二段是受约束重选，串行是设计使然

第二段调用每次都在 primary 返回后 **10 毫秒内**启动，而 hedge 阈值是 6.5 秒（三次里有两次 primary 在
5.66 s 和 4.18 s 就返回了，hedge 不该触发）。所以第二段不是 hedge、不是 backup，而是**受约束重选**：
它必须先拿到 primary 的输出，才知道哪里不合法、才能把精确失败原因讲回给同一个角色模型。

这正是 `AGENTS.md` 要求的行为（"模型结果不合法时……允许一次受约束重选"）。**串行不可避免，问题不在串行。**

### 2.4 根因：重选没有自己的时间预算

`interactive_turn_budget.py:53-59` 定义交互式回复的取消上限：

```
total_seconds = 12.0
hedge_after_seconds = 6.5
acceptance_dispatch_reserve_seconds = 1.0
```

候选 deadline = 12.0 − 1.0 = **11.0 s**，与实测完全吻合。

`production_turn_application.py:3603-3604` 把这个 policy 灌给了所有 purpose 共用的 `Deliberation`：

```python
main_timeout_seconds=config.interactive_turn_budget_policy.total_seconds,
quick_timeout_seconds=config.interactive_turn_budget_policy.total_seconds,
```

于是**后台车道（没有任何人在等）背上了交互式对话的延迟上限**。

真正决定重选能拿到多少时间的是 `deliberation.py:517-521`：

```python
def fit(self, requested_seconds: float) -> float | None:
    deadline = self.reselection_deadline or self.recovery_deadline or self.author_deadline
    available = max(0.0, deadline - self.budget.clock())
    fitted = min(max(0.0, requested_seconds), available)
    return fitted if fitted > 0 else None
```

只要 `begin_reselection()` / `begin_recovery()` 没被调用过，deadline 就退回 `author_deadline`（11 秒）。
重选能拿到的时间 = 11 秒 − primary 已用时间。**primary 越慢，她纠正自己的机会越少。**

讽刺的是系统里备好了充裕窗口，`deliberation.py:494-502` 的 `hard_deadline` = 11 + 46 + 100 = **157 秒**。
但这些窗口只由 `run_validation_review` 与 `begin_validation_reselection_recovery` 打开，
两者的 docstring 都写明是 "visible chat source review" 车道专用。主动联系走不到那里，157 秒形同虚设。

### 2.5 数学上必然失败

`interactive_turn_budget.py:54-57` 自己记录的实测分布：p50 4242 ms、p90 6155 ms、p99 8508 ms。
两段同等复杂度的调用串联，p50 就要 8.5 秒，p90 要 12.3 秒——**11 秒的窗口在 p90 处必爆**。三次实测三次全爆。

### 2.6 账本里没有她说过话的痕迹

三条 `ModelResultRecorded` 的 `audit_json` 全部是：

```json
{"failure_code": "primary_timeout", "outcome": "budget_exhausted", "slot": "primary",
 "status": "main_timeout", "attempt_count": 1, "attempt_index": 0,
 "response_hash": null, "input_tokens": null, "output_tokens": null, "model_id": null}
```

`response_hash` / `output_tokens` / `model_id` 全 null，`attempt_count` 是 1。账本认定的事实是
"预算耗尽，模型没被调用过"；而 `world_v2_model_usage` 里躺着 6 条真实记录、32662 tokens、¥0.0293。
第二次调用连独立 audit 都没有——这与"model-facing 的调用必须记录 ModelResult 供 replay"冲突。

两个命名都在误导：`primary_timeout` 的 timeout 不是 provider 超时，`budget_exhausted` 的 budget 不是钱
（chat 账户 0/10000，proactive 0/1000，无任何 `BudgetExhausted` 事件）。两者指的是同一件事——11 秒挂钟走完了。

### 2.7 同一份冲动死了两次

21:44 与 01:44 两次尝试的 `trigger_ref` 是**同一个** `event:affect-mutation:17b6ece6...`。
同一份情绪冲动，隔 4 小时重试一次，又死在同一个地方。

## 3. 生产实况普查

### 3.1 这个世界只有 177 个事件

`epoch2` 从 2026-08-13T17:42Z 起至核查时刻共 **177** 个事件（约 14 小时）。全量类型普查：

- **有**：ProposalRecorded 16、ModelResultRecorded 16、TriggerProcess 四件套约 39、ClockAdvanced 11、
  RandomDrawRecorded 9、AcceptanceRecorded 6、AppraisalAccepted 2、AffectEpisodeOpened 2、
  ObservationRecorded 2、ExpressionPlan* 各 2、Action* 各 2、NpcRegistered 1、BiographicalTimelineConfigured 2。
- **完全没有**：`ActivityStarted` / `ActivityCompleted` / 任何 LifeArc 事件 / `WorldOccurrenceSettled` /
  `NpcPlan` / `NpcOccurrence` / `ExperienceRecorded` / `AspirationRecorded` / `ThreadOpened` /
  `PrivateImpressionAccepted` / 任何媒体事件 / `FactCommittedV2`。

对话总量：**2 条用户消息、2 次回复**。

### 3.2 内心确实在动，但很浅

15:44（07:44:53Z）抓到一次实时现场：`worker:world-v2:life-ecology` 产出 ModelResultRecorded +
2 条 ProposalRecorded（`attempt:life-development:world_author:...`），随后
`inner-state-settlement` → `AppraisalAccepted`，`affect-settlement` → `AffectEpisodeOpened`。

即：**评估与情绪的接受链是通的**（H6 的成果），但 life_development 的提案没有变成任何领域事件。

### 3.3 交互事实一直落不了地

`InteractionFactTechnicalFailureRecorded` 6 条，同一条 trigger 的 `retry_ordinal` 已到 3，
退避拉到 2 小时，`failure_code` 恒为 `provider_exception`。她与用户互动产生的事实至今没有一条落账
（`FactCommittedV2` 为 0），记忆的写入链因此是空的。

### 3.4 语义召回从 8 月 8 日起就在降级

`com.girl-agent.local-embedding` 的 plist 存在，但 `launchctl list` 里没有它，
`logs/embedding-server.err.log` 停在 8 月 8 日 15:51。每次编译上下文都刷
`semantic recall degraded to local index` 加整页 `Connection refused` 堆栈。

这与执行计划裁决 6（"记忆召回先保证不依赖 embedding 可用；embedding 只作为后接的加分项"）方向一致，
不算违规，但意味着 S17「触景生情」目前只有精确召回。

### 3.5 H12–H16 一行都没进生产

daemon 与 napcat 均于**北京时间 01:43:59 启动**，连续运行 13 小时 50 分未重启。
`world_v2_occasion_spends` 表在生产库中不存在——H16 的 Occasion 队列尚未生效。
H12–H16 的全部改动都在 `src/` 里等一次重启。

调度器本身健康：1662 次 pass、0 失败、间隔 30 秒、最近一次在核查时刻前几秒完成。
15:44 之后的静默是正常冷却（`next_consideration_at` 排在 16:29），不是死机。

## 4. 成本

14 小时窗口，11 次调用，114288 tokens，合计 **¥0.0913**。

| purpose | 状态 | 次数 | 花费 | tokens | 平均延迟 |
| --- | --- | ---: | ---: | ---: | ---: |
| world_stimulus_appraisal | succeeded | 2 | ¥0.0359 | 51886 | 6971 ms |
| proactive_contact | succeeded | 3 | ¥0.0293 | 32662 | 5921 ms |
| inbound_turn | succeeded | 2 | ¥0.0261 | 29740 | 4655 ms |
| proactive_contact | failed | 3 | ¥0 | 0 | 5064 ms |
| inbound_turn | failed | 1 | ¥0 | 0 | 5844 ms |

两个结构性问题：

1. **32.1% 的钱买了空气。** proactive 的 ¥0.0293 / 32662 tokens 全部被 §2 的 11 秒门丢弃，
   产出为 0。这是纯浪费，不是"投资在看不见的内心"——它连状态都没留下，违反裁决 §12.1
   （"判据是下周二还在不在她身上"）。
2. **缓存命中率远低于 G5 门。** inbound_turn 16.4%、proactive 18.8%、appraisal 35.4%，
   全部低于 G5 要求的 50% 告警线。执行计划 §1 立项时实测是 23.1%，H3 做过稳定前缀改造，
   但 inbound 现在是 **16.4%，比立项时更低**。DeepSeek 缓存命中价是未命中价的 1/50，
   这一项的杠杆比换模型大得多。

绝对金额目前很低（外推约 ¥4.7/月），但那是因为**世界几乎没在动**——2 轮对话、0 个活动、0 个 NPC 事件。
不能据此认为成本已受控；它只是还没开始花。

## 5. 对照执行计划：进行到哪一步，是否跑偏

### 5.1 没有跑偏

H1–H16 的每一条执行记录都对得上 §2 用户裁决与 §4 强制门，方向一致：

- 裁决 1（新纪元）→ H10 已切 epoch2。
- 裁决 2（不换 pro、做缓存前缀）→ H3 已做稳定前缀；但**效果未达标**（见 §4.2）。
- 裁决 3（情绪连续、会升级）→ H6 已交付，生产可见 Appraisal/Affect 接受链在工作。
- 裁决 4（删双模型审查）→ H1d 已删。
- 裁决 5（主动联系次数由她决定、不做表面去重）→ 未见任何配额或去重代码，遵守。
- 裁决 6（召回不依赖 embedding）→ 当前正以降级模式运行，符合。
- 裁决 7（one-shot 默认）→ H1d + G4 契约瘦身在做。

§17 的"剩余缺口"栏目写得诚实，没有粉饰。这份计划的执行纪律是好的。

### 5.2 但有一个系统性偏差：所有验收都停在"代码已提交"，没有一条闭合到"生产上真的发生了"

H12 至 H16 五个包的"生产证据"栏几乎一致地写着「进程未重启，数字不会变」。叠加起来的后果是：
**连续 5 个工作包没有任何一条生产实证**，而其间生产上正在发生 100% 的主动联系失败——
这个失败与 H12–H16 无关（是 §2 的 11 秒门），但没有任何一个包的验收流程会发现它。

这不是计划跑偏，是**验收口径缺了最后一环**：交付模板（§14）要求"生产证据"，
但接受"进程未重启所以数字不变"作为合格证据。建议在模板里加一条硬要求：
**每个包必须在重启后回采一次生产数字，或明确写下"本包不重启则不产生任何生产影响"并挂到下次重启的回采清单上。**

### 5.3 优先级排序与实际瓶颈不匹配

执行计划把力气放在"编译给她的'现在'太薄"（Present 加厚、连续性、期待语义），这个判断在
2026-08-13 的 34809 事件账本上是对的。但切到 epoch2 之后，实际瓶颈换了：

- 旧账本的问题是**内容太多但编译得太薄**。
- epoch2 的问题是**根本没有内容**——0 活动、0 经历、0 事实、0 NPC 事件。

Present 加厚在一个空世界里没有东西可加厚。H12 的执行记录已经诚实记下这一点
（"epoch2 不会追溯拿到这 41 条经历，她要等到修复后新写的经历"）。

## 6. 逐条回答

### 6.1 角色内心是否持续且连续？

**部分是，但连续性目前只存在于最短的一环。**

- 通的：同回合内 Appraisal → Affect 的接受链在生产上可见（15:44 现场）。情绪能落账。
- 通的：H12 修好了经历进入 Present 的编译（`_updated_at` 回退、rank 提升），代码层面已验证 41 条能编出 24 条。
- **断的**：epoch2 里 `ExperienceRecorded` 为 0，所以 H12 修好的通道**没有任何东西流过**。
- **断的**：`FactCommittedV2` 为 0（§3.3 的 provider_exception 重试链），记忆写入链是空的。
- **断的**：`PrivateImpressionAccepted` 为 0，H14 的 `stuck_with_me` 尚未接到接受链（H14 执行记录已明说验收闭不上）。

结论：她**这一轮**是连贯的，但**跨轮的连续性还没有载体**。执行计划想解决的正是这个，
H12/H13 修好了管子，但水（经历、事实、印象）还没开始流。

### 6.2 事件是否达到预期（实习 / 日常生活）？

**没有。而且实习那条线有明确的硬阻塞。**

- **实习**：`configs/world_seed.yaml` 已升到 `reviewed-life.14`，`publishing-intern-interview` 的 offer outcome
  带上了 `life_arc_effect`（employment / 30 天 / `role:intern` + `workplace:publishing`）。但 H15 执行记录已明确记下：
  **`ReviewedLifeSeedCatalog.candidates_at` 没有生产调用方，单靠 seed 行不会自动开始实习面试**——
  要等已有 activity 计划以该 `activity_kind` 完成、aftermath 才会冻 effect。而 §3.1 显示
  `ActivityStarted` 为 0。所以这条链的**第一环就没启动**，实习永远不会自己发生。
  对应设计意图 S15「人生大事迁移」——机制标 [active]，实际未通电。
- **日常生活**：S11「日常作息」标 [active]，但 epoch2 里 0 条 `ActivityStarted` / `ActivityCompleted`。
  H8 交付的日程骨架与每日 day_open 尚未在这个 epoch 产生任何活动事件。

调度器本身没有问题：`LifeEcologyRuntime.advance_once` 的顺序（biographical → activity → aftermath →
life_development → npc_initiative → open_world → visual_evidence → media）已装配 `installed_and_active`，
4 次 wake 每次都跑到了 life_development。**卡点是三处互相独立的断路**：

1. **模型从来没被问过。** 4 次 life_development **全部** `decision: "no_op"`——但这**不是模型的选择，
   是代码写死的返回值**：

```python
# life_development_runtime.py:4297-4300
catalog = getattr(self._manifest_compiler, "catalog", None)
if isinstance(catalog, ReviewedLifeSeedCatalog):
    raw = '{"decision":"no_op"}'
    parsed = LifeDevelopmentNoOpDraft.model_validate_json(raw)
```

   返回的 `model_id` 是 `"deterministic:weighted-table"`，**根本没有发生 provider 调用**——
   这也解释了为什么 `world_v2_model_usage` 里连一条 `life_development` 记录都没有。
   这是 §12.9 第 8 条（加权表）为省钱装上的短路，用户已于 2026-08-14 撤回该条，但**代码还没改**
   （执行计划 §17 原话是"代码暂不改"）。所以世界作者至今一次都没被征询过要不要写点什么。
   没有 propose 就没有 `ActivityPlanned`，没有 plan 就没有 `ActivityStarted`；
   `ActivityOpeningCatalog` 只枚举**已有** plan 上的 start/pause/complete，它不创建活动
   （`life_ecology_activity.py:222-309`），零 plan 时直接 `no_openings`。

   **按 AGENTS.md 这条短路本身就越线**（确定性代码替角色/世界作者做了语义决定），
   撤回它是恢复宗旨，不只是恢复功能。但撤回会把成本带回来，见 §10。
2. **`LifeAvailabilitySnapshotRecorded` 没有任何生产者。** 带 `location_ref` 的 plan 要求 evidence 里绑定
   该事件才能开始（`life_ecology_activity.py:440-460`），但全仓库 `src/` 内无人写入它，只有
   `legacy_life_author_events.py` 定义了 payload 与 reducer。life_development 产出的 `ActivityPlanned`
   走的是 `location_capability.authority_refs`，不满足这个检查。**即使模型开始选题，带地点的活动仍会卡住。**
3. **动态 Life Arc 腿的模型入口被类型钉死。** 这一条是前两条之外的真正断点，也是 H15 验收标准
   （"补 `life_arc_effect` 生产者 / 出现**非目录来源**的生活细节"）真正指向的东西。

**先厘清一个口径**：`ReviewedLifeSeedCatalog.candidates_at()` 在 `src/` 中零调用**不是缺陷，是设计意图**。
用户已于 2026-08-14 撤回 §12.9 第 8 条的剧情库路线（见执行计划 §17：
「用户要求撤回本条两边（NPC 模型路径 + 世界作者）」），权威路线是**模型自由生成事件 + 配置事件影响**。
设计总纲也把「人生发展（**无剧情库**）」标为 [active]，`life_development_draft.py:240` 的注释同样写着
*"It is not a menu of life directions"*。所以种子目录不接线是对的，**不该接回去**。

真正的问题是：Life Arc 有两条腿，**目录腿通、动态腿断**。

| | 目录腿（reviewed） | 动态腿（模型生成） |
| --- | --- | --- |
| 载体字段 | `OutcomeCandidateDescriptor.life_arc_effect` | `.dynamic_life_arc_context` |
| 来源 | `catalog.frozen_life_arc_effect_for_outcome()`（`life_aftermath_runtime.py:465-470`） | 模型提议 |
| schema | 有 | 有（`DynamicLifeArcContextDescriptor`） |
| reducer | 有 | 有（`reducers.py:12759-12783`） |
| 消费 | 有 | **有**（`biographical_lifecycle_runtime.py:203-206` 已经 `life_arc_effect or dynamic_life_arc_context`） |
| **模型提议入口** | 目录行 | **被钉死** |
| **采纳开关** | 不需要 | **硬编码 False** |

两处硬堵点：

```python
# life_development_draft.py:600 —— 模型压根无法声明动态人生方向
dynamic_life_direction: None = None
```

```python
# life_aftermath_runtime.py:1361 —— 角色自选 outcome 的路径上硬编码不采纳
audit_response_text = outcome_selection_audit_text(
    candidate_result_ref=selected.candidate_result_ref,
    adopt_proposed_life_direction=False,
    ...
)
```

而 reducer 侧要求 `dynamic_life_arc_context is not None and payload.adopt_proposed_life_direction is True`
才会进 `pending_biographical_settlements`。**两个条件各被堵死一个，动态 Life Arc 永远开不出来。**

**所以 H15c 的方向是反的**：它给 `world_seed.yaml` 的 `publishing-intern-interview` 加了一条
`life_arc_effect`，走的是**已被用户撤回的目录腿**；而 H15 自己的验收标准写的是"非目录来源"。
即使生活生态点火成功，open-life plan 的 `activity_kind` 是 `open_life.<hash>`，aftermath 走 dynamic outcomes，
**永远不会命中那条 seed outcome**。这一行改动在当前路线下是无效功，不是"挂在没入口的路径上"那么中性——
它是往回走了半步。H15 真正欠的是打通上面两处堵点。

NPC 情况需要更正一处口径：`NpcRegistered` 的 1 条属于 `world:companion-v2:geoff`；生产 QQ 世界
（`world:companion-v2:qq-c2c:geoff`）的 `NpcRegistered` 是 **0 条**，它的 6 个 NPC 来自 `WorldStarted.continuity`
纪元迁移——**投影里有 NPC，事件流里没有**。NPC initiative 尚未触发，且暑假 context
（`residence:family_home_jiaxing`）会挡掉要求 `residence:campus_dorm` 的 `lin-wan` 等种子 NPC。

结论：**这个世界不是"活着但年轻"，是"心脏在跳、消化管有三处断路"。** 在修复接线前，
即使再等数周也只会积累更多 `ProposalRecorded(no_op)`，不会自然长出活动或生活弧线。

### 6.3 用户当前能感知到多少？

**几乎只有"她会回消息"这一件事。**

14 小时里用户可见的全部是：2 次回复。此外：

- 0 次主动联系送达（§2）。
- 0 张照片（媒体链在 epoch2 无任何事件；设计意图 §5.1 已标 `limited-production`，自动投递未全局启用）。
- 0 个可被提起的生活事件（0 活动、0 NPC、0 外部感知落地）。
- 记忆无法体现（0 事实、召回降级）。

按设计意图 §9 场景库，S2（主动发起聊天）、S9（分享后的期待）、S11（日常作息）、S15（人生大事迁移）、
S20（拍照分享）目前对用户都是**不可感知**的。

### 6.4 成本情况

见 §4。要点：绝对值很低（¥0.0913/14h）但不具参考性，因为世界没动；
结构上 32.1% 的花费是纯浪费；缓存命中率 16–35%，低于 G5 门，且 inbound 比立项时更差。

### 6.5 她是"被调用了就联系"还是"想起了才联系"？

**随机性没有越线，但这个问题目前无法回答——因为她的三次回答都被丢掉了，从来没有人读过。**

先澄清随机抽签。9 条 `RandomDrawRecorded` 解开后，`system:social-initiative` 抽的全部是**延迟时长**，
不是"要不要联系"：

- `social-initiative-delay.1`：候选 `delay:21600` / `delay:25200` / `delay:28800`（6 / 7 / 8 小时）
- `social-initiative-situation-delay.1`：候选 `delay:120` / `delay:900` / `delay:2700`（2 / 15 / 45 分钟）

即抽的是"多久之后再想一次"，**决定权仍在模型手里**。这符合 AGENTS.md
（"随机性可以决定机会、时机、注意力……但不能预先决定角色的行为"），**没有触碰随机 act/hold 红线**。

而且权重不是固定表，`weight_policy_version` 是 `social-initiative-context.2`，随情境变化。同一组候选在三次抽签里
权重分别是 10/50/40、25/50/25、40/50/10（ppm 万分比）。配合 health 里的
`cadence_reason_codes: ["relationship:stranger", "affect:neutral", "activity:available", "daypart:day"]`，
可以确认**关系深度确实参与了节奏计算**。

三次主动联系的 `trigger_ref` 中，两次是 `event:affect-mutation:17b6ece6...`（情绪变化派生），
一次是用户消息 observation。所以机会不是纯时间驱动的空转，它挂在她的情绪变化上。

**但核心质疑无法证实也无法证伪：** health 显示 `model_silent_count: 0`、`consideration_count: 3`——
三次考虑没有一次的结论是沉默。表面上像"来者不拒"。然而这三次的 `audit_json` 里
`response_hash` / `output_tokens` / `model_id` 全是 null（§2.6），**模型到底说了什么、是不是真的想联系，
系统从未记录**。三次都在读到她的回答之前就被 11 秒的门砍掉了。

因此现在的结论只能是：她**有机会想起用户的频率**是 6–8 小时一次（陌生人关系下这个频率偏高，
真实陌生人不会一天想起你三次）；她**是否真的想联系**——这个问题在修好 §2 之前拿不到任何证据。

另需注意：`social-initiative-context.2` 目前调节的是"多久再想一次"，即**频率**。用户期望的
"关系深了以后因为脑子里常想着他才联系"是另一件事——那要求关系深度进入**她的内心材料**
（她能读到"我最近总想起他"），而不只是缩短系统的轮询间隔。这两者是否已经打通，见 §7 调研。

### 6.6 期待机制是否存在、是否工作良好？

**四段里通了三段，最后一段"她主动追问"是断的——而且断点是 H13e 自己引入的。**

已经生产就绪的部分（设计上是干净的）：

1. **期待是一等公民，且由她自己声明。** `ResponseExpectationAuthority`（`schemas.py:1161-1179`）带
   `hoped_response`（自由文本，≤128 字）、`pressure_bp`、`importance_bp`、`not_before`/`expires_at`。
   不是"她在等回复"这种布尔量，而是"她具体盼着什么"。`ResponseExpectationDraft` 的 docstring 写明
   *"never inferred from punctuation"*，H13 的红测 `test_slim_does_not_infer_expectation_from_a_question`
   锁住了这一点——问号不会被推断成期待。**符合宗旨，没有关键词/正则替她做语义决定。**
2. **用户回话时她能看见并评估。** inbound pinned turn 注入 pending advisory
   （`production_turn_application.py:3623`），有 advisory 时强制返回四态评估
   （fulfilled / superseded / still_pending / uncertain）。端到端测试 `test_expectation_feelings.py:360-396`
   证明第二条 inbound 的 `model_content_json` 里确实带 `HOPED`。
3. **沉默约 1 小时后她会被问"这安静意味着什么"。** `silence_appraisal` 车道默认 3600 秒 idle 打开，
   锚定她最后一条可见消息的回执；`world_stimulus.py` 只把它标为 `unanswered_visible_expression`，
   **由模型自己诠释**（想念/不安/无所谓/没什么），确定性代码不解释沉默含义。同时 pending 期待会折进
   advisory 一起给她（`production.py:157-161`）。
4. **有 pending 期待时不会另开普通 idle 主动联系**（`social_initiative.py:1174-1179`），
   避免她一边等回复一边没头没脑地再找人。

**断掉的第四段：期待过期后的主动追问。**

H13e 的 `_expired_expectation_contact`（`social_initiative.py:1139-1147`）mint 出的机会是：

```python
source_kind="spontaneous_contact",
source_id=expired.plan_id,                  # plan_id
source_event_ref=expired.receipt_event_id,  # ExecutionReceiptRecorded
```

而 `proactive_action.py:1056-1070` 对 `spontaneous_contact` 的 source binding 校验要求：

```python
message = next((item for item in projection.message_observations
                if item.observation_id == opportunity.source_id), None)
valid_source = (event.event_type == "ObservationRecorded"
                and message is not None
                and message.world_revision == opportunity.source_world_revision
                and projection.message_observations[-1] == message)
```

用 `plan_id` 去 `message_observations` 里按 `observation_id` 查必然查不到，事件类型也是
`ExecutionReceiptRecorded` 而非 `ObservationRecorded`。**两个条件同时不满足** →
`_ProactiveSourceBindingError`（1151）→ 在 `advance_due_once` 被捕获（1541）→
`outcome="source-binding-invalid"`、`status="failed_safe"`。

更糟的是这个失败会把 process 写成 **terminal**，而 `_expired_expectation_contact` 开头正好有：

```python
if existing is not None and existing.state == "terminal":
    return None
```

**于是这次期待落空的机会被永久废掉，不会重试。** 净效果是：她永远不会因为"我盼的回应没等到"而追问。

H13e 的测试只覆盖到 `SocialInitiativeCompiler.next_opportunity()` 这一层（机会有没有被 mint 出来），
**没有任何测试走到 `ProactiveActionRuntime.advance_due_once()`**，所以这个断点在交付时没有被发现。
这是交付模板的一个真实教训：红测停在编译器边界，就测不到宿主的 source binding 契约。

另外三个较小的缺口：

- **过期后内心上下文会丢。** 期待一旦过期，`pending_response_expectation()` 返回 `None`，
  advisory 不再注入，而**没有任何"落空"专用的 Appraisal/Affect 事件**。也就是说"心里觉得奇怪"
  目前只能发生在期待**仍然 pending** 且刚好撞上 1 小时沉默内省的时候；真正过期之后，
  这件事反而从她的视野里消失了。
- **时间尺度不对。** slim 声明的默认值是 wait ≈ 12 小时、`expires_after_seconds` = 86400（24 小时）
  （`present_prompt.py:276-280`）。用户期望的"较短时间内主动发问"在这个默认下不可能发生。
  注意这个默认值不能由系统单方面改小成硬规则——按宗旨它应该由她自己声明，系统只提供合理默认。
- **她不知道自己为什么被叫醒。** `cadence_reason_codes`（含 `expectation:expired_unanswered`）
  只进 dashboard/metrics，**不进模型 Context**。即使 binding 修好，她也读不到"这次是因为你盼的回应没来"。

最后是生产事实：epoch2 `ExpressionPlanAccepted` **0/2** 带期待、`ResponseExpectationAssessed` **0**，
归档库 **0/106**。**她至今一次期待都没声明过**，所以上面整条链在生产上还没有触发源。
对应设计意图 S9「分享后的期待与忐忑」，原标记 [disconnected] 至今成立。

### 6.7 一个意外的正面发现：她自己的表达开始回流了

07:44:53Z 的 `AppraisalAccepted` 里 `evidence_type` 是 `committed_world_event`，
`ref_id` 是 `event:trigger:settlement:qq:c2c:platform:message_id:...`（她自己发出消息的投递结算），
`confidence_bp` 6800。

即：**她对"自己说过的话被送到了"这件事产生了评估**。设计意图 S10（自己说了重话后的后悔/坚持）
原标记为 [disconnected]（"她自己说出的话不回流"），生产数据显示这条通道现在有信号了——
应是 H6 world_stimulus 通道的成果。建议复核 S10 的状态标记。

## 7. 关系深度目前有多立体

用户的期望是：陌生期她不该总想起用户；关系深了以后，她因为脑子里常想着这个人才去联系——
这应当是"深层立体而可变的设计"。对照实现：

**已有的一维：consideration band 随 stage 变化**（`social_initiative.py:209-230`）。

| Stage | 自发考虑间隔 |
| --- | --- |
| close_friend / lover | 1–2 小时 |
| friend / ambiguous | 2–4 小时 |
| acquaintance | 3–6 小时 |
| **stranger** | **6–8 小时** |

stage 由 `trust_bp` / `closeness_bp` / `mutuality_bp` 驱动，带迟滞（2 次确认 + 24 小时 dwell，
`relationship_reducers.py:37-50`），演进本身是稳的。

**但立体性目前只有这一维，而且有一个明显的漏口：**

1. **`situation_change` 完全不受关系深度调节。** 它用固定的
   `_SITUATION_DELAY_CANDIDATES = (120, 900, 2700)`（`social_initiative.py:57`），即 2 / 15 / 45 分钟。
   生活事件与情绪变化走的正是这条路。**也就是说，陌生人状态下她仍可能在一次情绪波动后 2 分钟就被问
   "要不要联系他"**——这与"陌生人不该总想起用户"直接冲突。生产上观察到的三次主动联系里，
   两次的 trigger 正是 `event:affect-mutation:...`，走的就是这条快速路径。
2. **关系深度不是"是否想起"的开关，只是"多久问一次"的旋钮。** 系统层没有任何
   "closeness 不够就不开机会"的门控，只有"间隔更长"和"模型自己选 silent"。
3. **"脑子里常想着他"没有对应物进入她的内心材料。** relationship slice 会进 Inner Life Snapshot
   供她读（`snapshot_compiler.py:637-640`），但那是关系的**状态**（stage、几个 bp 值），
   不是"我最近总想起他"这种**惦记的强度**。真正接近用户设想的是 Thread / commitment 路径
   （她自己开一个 thread，到期系统再问她）——这条是"内心意图 → 到期再问"，方向完全正确，
   但需要她先主动开 thread，陌生期几乎不会发生。

**架构现状是：**

```
[外部时机：空闲 / 时钟 / 生活事件] → RandomDraw(只选延迟) → [机会到期] → 问模型 now/later/silent
```

**而不是：**

```
[内心 Thread / 惦记 / Affect 达到"想起"强度] → 自然长出联系冲动 → 问她怎么表达
```

需要说明的是，前者**并不违反** ADR-0010 与 AGENTS.md——随机只选时机、语义决定权仍在模型、
silent 会被尊重且 durable 落账（`proactive_action.py:1635-1640`），technical failure 才会退避重试，
语义 silent 不会被反复追问。所以这是**设计取舍**问题，不是越线问题：
当前实现选择了"系统按节奏创造思考窗口，她自由决定说不说"，用户想要的是"惦记到了才产生窗口"。

要往用户设想的方向走，最小的一步是把 `situation_change` 的快速路径也纳入关系深度调节
（陌生期不该 2 分钟就问），更进一步才是让"惦记强度"成为一个她能读到、也能驱动机会生成的内心量。
这两步都需要用户裁决后进入执行计划。

## 8. 建议的下一步（须经用户裁决后进入执行计划）

按"挡住的人数"排序：

1. **拆开后台车道与交互车道的 deadline**（§2）。这是唯一一个"她想说话但说不出口"的问题，
   其余都是"她还没有话可说"。修法不是调大数字，而是让受约束重选进入它本该进入的恢复窗口。
2. **修 H13e 的 source binding**（§6.6）。要么给过期期待一个新的 `source_kind` 并在
   `proactive_action` 加对应校验分支，要么让它绑回 observation 形态。同时补一个走到
   `advance_due_once` 的端到端红测——这个包的教训是红测不能停在编译器边界。
3. **重启生产，让 H12–H16 生效并回采数字**（§3.5、§5.2）。
4. **给生活生态点火**（§6.2）。三处断路：`LifeAvailabilitySnapshotRecorded` 无生产者是硬阻塞；
   life_development 恒 no_op 需要判断是模型的正当选择还是材料不足；**动态 Life Arc 腿的模型提议入口
   （`life_development_draft.py:600`）与采纳开关（`life_aftermath_runtime.py:1361`）各被钉死一处**——
   这才是 H15 欠的账。种子目录零调用是设计意图，不要接回去。
5. **修 interaction fact 的 provider_exception 重试链**（§3.3）。它堵死了事实与记忆的写入。
6. **缓存命中率回归**（§4）。H3 做过前缀改造但 inbound 反而降到 16.4%，需要定位回归点。
7. **决定 `situation_change` 是否纳入关系深度调节**（§7）。这一条是产品裁决，不是缺陷修复。

---

## 9. 接手施工单

写给接下来动手的 agent。**先完整读 §9.0，再挑任务。** 每个任务给的是「为什么改 / 改哪里 / 先写什么红测 / 怎么验收 / 已知的坑」，
不是让你照抄的补丁——具体实现仍需你自己判断，但不必重新调研。

### 9.0 环境事实（先读这一节，很多弯路来自这里）

**生产跑的就是这个 worktree 的代码。** 两个 launchd plist 的 `ProgramArguments` 直接指向
`.claude/worktrees/fix-cost-optimization/scripts/run_production_*.sh`，脚本里：

```bash
LIVE_ROOT="/Users/geoff/Projects/Girl-Agent"
WT="/Users/geoff/Projects/Girl-Agent/.claude/worktrees/fix-cost-optimization"
cd "$LIVE_ROOT"
export DATABASE_PATH="$LIVE_ROOT/data/companion.epoch2.sqlite"
export PYTHONPATH="$WT/src"
exec "$WT/.venv/bin/python" ...
```

所以：**代码来自 worktree，数据与 `.env`、`logs/` 来自主仓，cwd 是主仓。**
在这个 worktree 里改完代码，`kickstart` 一下就是生产生效——不需要合并或复制到主仓。

| 事项 | 值 |
| --- | --- |
| 生产库 | `/Users/geoff/Projects/Girl-Agent/data/companion.epoch2.sqlite` |
| 生产世界 id | `world:companion-v2:qq-c2c:geoff` |
| napcat（调度器 + QQ） | `127.0.0.1:8787`，label `com.girl-agent.napcat` |
| daemon（FastAPI） | `127.0.0.1:8765`，label `com.girl-agent.daemon` |
| 日志 | 主仓 `logs/napcat.err.log`、`logs/daemon.err.log` |
| venv | worktree 内 `.venv`（跑测试请用它） |

重启（改完代码必须做，否则进程内存里还是旧模块）：

```bash
launchctl kickstart -k gui/$(id -u)/com.girl-agent.napcat
launchctl kickstart -k gui/$(id -u)/com.girl-agent.daemon
```

**核查当时的部署状态**：两个进程都启动于 08-14 01:43:59，而 H12 的提交是 12:59、H13e/H14/H15/H16 是 13:55–13:58。
**进程比全部 H12–H16 代码早 11 小时以上**，所以文档里所有"H12–H16 的生产证据为空"都是这个原因，
不是代码有问题。你接手后第一次 kickstart，就是这些包的首次真实上线——请把 kickstart 前后的数字都记下来。

另有一个独立的小问题：`com.girl-agent.local-embedding` 的 plist 是 `RunAtLoad=false` + `KeepAlive=false`，
所以它没在跑，`napcat.err.log` 里的 `Connection refused` 与 "semantic recall degraded to local index" 都源于此。
要起它：`launchctl kickstart -k gui/$(id -u)/com.girl-agent.local-embedding`。
**这只影响召回质量，不影响本文档任何一条根因，别把它当主线。**

常用核查命令：

```bash
# 主动联系可靠性（T1/T2 的主验收口）
curl -s 127.0.0.1:8787/health | jq '.scheduler.initiative.reliability_24h'

# 事件类型分布
sqlite3 -readonly /Users/geoff/Projects/Girl-Agent/data/companion.epoch2.sqlite \
  "SELECT event_type, COUNT(*) FROM world_events
   WHERE world_id='world:companion-v2:qq-c2c:geoff' GROUP BY 1 ORDER BY 2 DESC;"

# 真实模型开销与取消（对照 audit 说的和实际花的）
sqlite3 -readonly /Users/geoff/Projects/Girl-Agent/data/companion.epoch2.sqlite \
  "SELECT substr(created_at,1,19), purpose, status, latency_ms, total_tokens, cost_cny
   FROM world_v2_model_usage ORDER BY created_at DESC LIMIT 40;"
```

### 9.1 T1 — 拆开后台车道与交互车道的 deadline（最高优先）

**为什么**：§2。这是唯一一个"她想说话但被系统夹住"的缺陷，其余都是"她还没有话可说"。
24 小时 3 次主动联系，3 次都在第 11.0 秒被取消，钱花了、话生成了、账本里什么都没留下。

**改哪里**：`production_turn_application.py:3837` 把 `config.interactive_turn_budget_policy`
原样传给了 `ProactiveActionRuntime`。同一个 policy 还流向 `chat_deliberation`（3603–3604，`lane_id="chat_reply"`）、
`expression_retry_budget_policy`（3858）、`WorldTurnRuntime`（4189）。
**只有后台车道该改，交互车道必须原样保留**——用户在等的那条路 11 秒是合理的。

建议方向：给 config 增加一个独立的后台预算（例如 `background_turn_budget_policy`），默认给足两段串行
（primary p99 8.5s + 重选 p99 8.5s + 余量），只喂给 proactive/后台驱动。

**坑**（这条最容易做错）：

- **不要只把 `total_seconds` 调大。** 它同时决定交互车道的取消上限，会让用户等更久。
- **真正的判定点是 `deliberation.py:517-521` 的 `fit()`**：`reselection_deadline or recovery_deadline or author_deadline`。
  只要 `begin_reselection()` / `begin_recovery()` 没被调用过，就退回 `author_deadline`。
  所以「让重选拿到自己的窗口」有两种修法——放宽后台的 `author_deadline`，或让后台重选真正走进
  `begin_validation_reselection_recovery`。后者更贴近设计原意（`hard_deadline` = 11+46+100 = 157 秒本就备好了），
  但那两个入口的 docstring 写明是 "visible chat source review" 专用，**跨车道复用前先确认语义是否成立，别硬接**。
- `audit_json` 里的 `primary_timeout` / `budget_exhausted` **命名是误导的**：不是 provider 超时，也不是钱不够。
  修完顺手考虑改名，否则下一个人还会被骗一次。

**红测先写**：一个后台 proactive 的 deliberation，primary 耗时 8 秒且返回不合法结果、重选耗时 6 秒才成功，
断言整体成功并落 `ProposalRecorded`。当前实现下它必须先红（14 秒 > 11 秒窗口）。

**验收**：kickstart 后观察 24 小时，`reliability_24h` 里 `technical_failure_codes` 不再有 `primary_timeout`，
且 `authorized_count` / `delivered_count` 至少出现一次非零。
另外核对 `world_v2_model_usage` 中 `caller_cancelled` 不再与 succeeded 成对出现。

### 9.2 T2 — 修 H13e 的 source binding（我引入的缺陷）

**为什么**：§6.6。`social_initiative.py:1139-1147` mint 出的机会永远过不了
`proactive_action.py:1056-1070` 的校验，落 `source-binding-invalid` → `failed_safe`，
而失败会把 process 写成 terminal，`_expired_expectation_contact` 开头的
`if existing is not None and existing.state == "terminal": return None` **会让这次机会永久作废**。
净效果：她永远不会因为"盼的回应没等到"而追问。

**两个方案，建议 A**：

**方案 A（推荐）——新增 `source_kind="expired_expectation"`**，语义正确：这次机会的源确实是她自己那条消息的回执，
不是用户的消息。需要同步改的点（已替你找齐）：

| 位置 | 改什么 |
| --- | --- |
| `social_initiative.py:172-177` | `SocialInitiativeOpportunity.source_kind` 的 `Literal` 加值 |
| `social_initiative.py:333-335` | `social_initiative_consideration_id()` 的 `source_kind` 参数 `Literal` 加值 |
| `social_initiative.py:496-500` | retry 分支的 source_kind 集合，判断是否要纳入 |
| `proactive_action.py:868` | `ProactiveOpportunity.source_kind` 的 `Literal` 加值 |
| `proactive_action.py:987-1149` | 加校验分支：绑 `ExecutionReceiptRecorded`、`source_id == plan_id`、回执 hash/revision 一致、期待仍在宽限期内 |
| `proactive_action.py:1170-1193` | candidate_ref / opportunity_context 的分支 |
| `proactive_action.py:2341-2379` | 排序与分类里的 source_kind 分支 |

**方案 B——绑回 observation 形态**：改动小，但语义扭曲（把"她的回执"伪装成"用户的消息"），
且 `projection.message_observations[-1] == message` 要求源必须是最新消息，期待落空期间不一定成立。**不推荐。**

**坑**：

- 失败会写 terminal 并永久作废机会。**修的时候顺便判断：source binding 失败到底该不该 terminal？**
  技术性失败（绑定形状不对）和语义性失败（源真的失效了）混在同一个出口里，是这个 bug 后果被放大的原因。
- 修完 binding，她仍然**读不到**自己为什么被叫醒——`cadence_reason_codes`（含 `expectation:expired_unanswered`）
  只进 dashboard，不进模型 Context。要真正实现"心里觉得奇怪 → 追问"，得让这条语义进 proactive 的 opportunity_context。
  **注意边界**：进 Context 的应该是"你上次盼着 X，到现在没等到"这样的事实陈述，
  **不能**是"所以你应该追问"——那就替她做决定了，违反 AGENTS.md。
- slim 默认 `expires_after_seconds = 86400`（`present_prompt.py:276-280`），24 小时。
  用户想要"较短时间内主动发问"，但**这个值按宗旨应该由她声明，系统只给默认**。
  改默认可以，改成硬规则不行。

**红测先写**（这是本任务最重要的部分）：现有 `tests/world_v2/test_interior_continuity_h13e.py`
只验证 `SocialInitiativeCompiler.next_opportunity()` 会 mint 出机会，**止步于编译器边界**，
所以没抓到宿主契约不匹配。新测试必须走到
`ProactiveActionRuntime.advance_due_once()`，断言 `status != "failed_safe"`、
`reason_code != "proactive.source_binding_invalid"`，并最终落到一条可投递的 action。
**这个教训适用于所有 mint→consume 的机制：红测不能停在生产者那一侧。**

### 9.3 T3 — 首次让 H12–H16 真正上线并回采

**为什么**：§9.0。这五个包一次都没在生产跑过，执行计划里五条"生产证据"全是"进程未重启"。

**做法**：先记录 kickstart 前的基线（下面几个数字），kickstart，再等 24 小时回采同样的数字。

基线口径：`ExperienceRecorded`、`FactCommittedV2`、`PrivateImpressionAccepted`、`ActivityStarted`、
`ResponseExpectationAssessed` 的计数（当前**全部为 0**），以及带 `response_expectation` 的
`ExpressionPlanAccepted` 比例（当前 0/2）。

**坑**：H16 的 `world_v2_occasion_spends` 表在当前库里**不存在**，因为建表发生在带 H16 的进程首次启动时。
kickstart 后先确认它被建出来了，否则 Occasion 去重没有真正生效。

### 9.4 T4 — 给生活生态点火

**为什么**：§6.2。三处断路，**任意一处不通，实习和日常生活都不会发生**。建议按顺序处理，别并行。

1. **`LifeAvailabilitySnapshotRecorded` 无生产者**（硬阻塞，最该先修）。带 `location_ref` 的 plan
   要求 evidence 绑定该事件才能 start（`life_ecology_activity.py:440-460`），但 `src/` 内无人写入它，
   只有 `legacy_life_author_events.py` 定义了 payload 与 reducer。先判断：这个检查是**该保留并补生产者**，
   还是**本身就是遗留物、该让 open-life plan 走 `location_capability.authority_refs`**？这决定了工作量级别。
2. **life_development 恒 `no_op`**（4/4）。这是模型的合法终态，**不能用规则强迫它选题**。
   要做的是判断它为什么不选：是材料太薄（世界里确实没东西可写），还是 prompt/capability manifest 没给够。
   先读一次真实的 `model_content_json` 再下判断，别猜。
3. **打通动态 Life Arc 腿**（这是 H15 真正欠的账，见 §6.2 第 3 点的对照表）。

**路线已定，不要走回头路**：用户 2026-08-14 撤回了剧情库方案（执行计划 §17：
「用户要求撤回本条两边」），权威路线是**模型自由生成事件 + 配置事件影响**。
所以 `ReviewedLifeSeedCatalog.candidates_at()` 零调用是**正确状态**，**不要为了让实习跑起来去接种子目录**——
那是往回走。「配置」该配的是**影响的形状与生效规则**（Life Arc 的种类、时长上限、能改哪些 context tag、
需要什么证据才能开），**内容与意义由她产出**。这正是 `day_skeleton.py` / `weighted_table.py` 里
那句判据的意思：**表决定机会，她决定内容与意义；反过来就是剧情库。**

两处硬堵点，都要开：

```python
# life_development_draft.py:600
dynamic_life_direction: None = None      # 类型钉死，模型无法声明
```

```python
# life_aftermath_runtime.py:1361
adopt_proposed_life_direction=False,     # 角色自选 outcome 路径上硬编码不采纳
```

好消息是**下游整条链全通，而且已经是为"事件机自己配置事件影响"设计的**。用户要的形态是：
生成一个大事件（实习）→ 事件机同时声明这个事件如何影响接下来的生活 →
后续几个月的大小事件、NPC 都围绕它展开 → 角色仍可参与事件中的选择。
逐段核对如下（✓ 表示已就位）：

```
事件机生成事件，同时声明 dynamic_life_arc_context
  （context_tags / supersedes_context_tag_prefixes / duration_days / 自由文本摘要）
        ↓  ✗ 堵点 1：life_development_draft.py:600  dynamic_life_direction: None = None
outcome candidate 携带 dynamic_life_arc_context                     ✓ schemas.py:3487
        ↓  ✗ 堵点 2：life_aftermath_runtime.py:1361  adopt_proposed_life_direction=False
WorldOccurrenceSettled(adopt_proposed_life_direction=True)
        ↓  ✓ reducers.py:12759-12783 收进 pending_biographical_settlements
        ↓  ✓ biographical_lifecycle_runtime.py:203-206
              effect = settlement.life_arc_effect or settlement.dynamic_life_arc_context
LifeArcStarted → projection.life_arcs                              ✓
        ↓  ✓ biographical_lifecycle.py:326-360
BiographicalContext.context_tags                                   ✓
        ↓  ✓ life_development_capability.py:165-171 → :308 biographical_context_tags
事件生成模型的 capability manifest                                  ✓
        ↓
此后所有生活事件都在 role:intern / workplace:publishing 的语境里生成
```

**最关键的一段已经验证过**：`biographical_lifecycle.py:326-360` 折算 active arc 的 context_tags 时
用的是**鸭子类型**（`getattr(arc, "context_tags", ())`、`getattr(arc, "supersedes_context_tag_prefixes", ())`），
**完全不查目录，也不区分 reviewed 与 dynamic**。所以动态 arc 只要能开出来，
它的标签就会自动进入后续所有事件生成的语境——**"接下来几个月都和工作相关"不需要另外造机制**。
它甚至已经处理好了 residence 语义（一个 active arc 只能声明一个住处，最新的覆盖旧的，短期 arc 结束后自动回落），
搬家类 arc 开箱即用。

`DynamicLifeArcContextDescriptor`（`schemas.py:3238-3252`）的形状也正是为此准备的：

| 字段 | 承载什么 |
| --- | --- |
| `context_tags`（≤16） | 事件对后续生活的影响面（`role:intern`、`workplace:publishing`） |
| `supersedes_context_tag_prefixes`（≤8） | 取代旧身份（实习开始后 `role:student` 让位） |
| `duration_days`（1–730） | 影响持续多久（"接下来几个月"） |
| `narrative_tags`（≤16） | 叙事线索 |
| `summary_content_ref` | 自由文本，她自己的说法 |

与目录腿的 `FrozenLifeArcEffectDescriptor` 相比，动态腿**没有 `arc_kind` 枚举，也不绑 `catalog_version`/`catalog_hash`**——
正好满足总纲要求的「新增方向不应要求开发者先把它加入类型枚举或剧情库」
（`world-v2-major-biographical-transition-gap.md:113`）。**schema 层早就站在开放路线这边了。**

**NPC 与事件绑定同样已就位**：`provisional_npc_introductions` 与动态 arc 并列挂在同一条
pending settlement 上，由 `biographical_lifecycle_runtime.py:195-202` 在开 arc 的同一批次里引入。
事件机声明"这次实习会遇到一个新同事"时，NPC 与 arc 是原子落账的。

**角色参与已有三层**，不需要新增机制：

1. **选结果**——outcome selection，`causal_authority="character_choice"`；
2. **声明自己的方向**——`character_life_direction`（`direction.*` 命名空间，主观方向，
   `character_outcome_contract.py:38-46` 守住命名空间不被客观迁移侵占）；
3. **是否采纳这条人生方向**——`adopt_proposed_life_direction`，即堵点 2。

**所以整件事的结论是：把两处打开，用户要的形态就成立了，不需要新建机制。**

**注意 `adopt_proposed_life_direction` 不能简单改成 `True`。** 它是"她是否采纳自己提出的人生方向"这个
语义决定，按宗旨**必须由角色模型给出**，不能由确定性代码替她选。
`life_aftermath_runtime.py:917-919` 那条路径已经是从 `proposal_payload` 读的，说明正确形状已有先例——
1361 那条（character-owned outcome selection）需要同样从她的结果里读，而不是写死。

**先做探针再动手**：H15 已经吃过一次亏（懒求值质地探针证明不可行，见执行计划 §17）。
建议先写一个探针测试确认 `DynamicLifeArcContextDescriptor` 能否在当前 reducer bundle 下真的落账
（注意 `reducers.py:2077-2086` 里 `life_arc_effect` 对 `world-v2-reducers.41/42` 有版本分支），
再决定是否需要 bump bundle。

**顺带清理**：H15c 给 `world_seed.yaml` 加的那条 `life_arc_effect`（`reviewed-life.14`）走的是目录腿，
在当前路线下是无效功。动态腿打通后建议评估是否回退该行，避免留下"两条腿都在用"的误导。

### 9.5 T5 — interaction fact 的 provider_exception 重试链

**为什么**：§3.3。`InteractionFactTechnicalFailureRecorded` 持续累积，`FactCommittedV2` 为 0。
事实写不进去，记忆与长期连续性就没有载体——H12 修好的通道也没水可流。

**验收**：`FactCommittedV2` 首次出现非零。

### 9.6 T6 — 缓存命中率回归

**为什么**：§4。H3 做过前缀稳定化改造，但 inbound 命中率反而是 16.4%。
两段式主动联系每次约 1.1 万 tokens，命中率直接决定成本。修 T1 之后 token 量还会上升，
所以这条最好在 T1 之后做。

**做法**：找 H3 的提交，对比改造前后 inbound 请求前缀的实际字节，定位是哪一段在每次请求间抖动
（时间戳、随机序、集合无序化都是常见原因）。

### 9.7 T7 — `situation_change` 是否纳入关系深度调节（**需用户裁决，勿擅自开工**）

**为什么**：§7。`_SITUATION_DELAY_CANDIDATES = (120, 900, 2700)`（`social_initiative.py:57`）
是写死的 2/15/45 分钟，不随 stage 变化。陌生人状态下她仍可能在一次情绪波动后 2 分钟就被问"要不要联系他"。
生产上三次主动联系里两次的 trigger 正是 `event:affect-mutation:...`，走的就是这条。

用户明确表达过"陌生人不应该总是想起用户，关系深了才会因为惦记而联系"。但改这个等于改变角色行为节奏，
**属于产品裁决，必须先问**。

### 9.8 交付纪律（这一包踩过的坑，别再踩）

1. **红测不能停在编译器/生产者边界。** H13e 就是这么漏的：机会 mint 出来了，测试就绿了，
   但消费侧的契约根本不匹配。凡是"A 产出机会、B 消费"的机制，红测必须跨过 A/B 边界。
2. **"进程未重启"不能算生产证据。** 连续五个包写了同一句"数字不会变"，同期生产 100% 失败无人发现。
   验收要么拿到真实数字，要么明写"本包无生产验收"，不要用"待重启"糊过去。
3. **区分"她的决定"和"系统的硬边界"**（AGENTS.md）。本文档里 T1/T2/T4/T5 是硬边界缺陷，放心修；
   只有 T7 会改变她的行为节奏，**必须先问用户**。
   特别注意 T4-3 里的 `adopt_proposed_life_direction`：打开它是修硬边界，
   但**它的值必须来自她的结果，不能由代码写死成 `True`**——那就从"钉死不许"变成"替她同意"，同样越线。
4. **别把已撤回的方案当作可选项重新提出来。** 剧情库（reviewed seed catalog）方案已于 2026-08-14 撤回，
   权威路线是模型生成事件 + 配置事件影响。看到"某某目录零调用"时先查它是不是退役路径，
   再判断是缺陷还是设计意图——本文档初稿就在这里判断反了一次。
5. **命名要诚实。** `primary_timeout` 不是超时、`budget_exhausted` 不是没钱，这两个名字让这次诊断多花了很久。
6. 改完 World V2 记得跑 `assert_bounded_vertical_coverage` 相关的启动门与 `tests/world_v2/`，
   用 worktree 里的 `.venv`。

---

## 10. 撤回加权表的成本账：贵的不是"让她自己生活"，是审查腿和频率

§12.9 第 8 条（加权表）当初就是为省钱装的。撤回它、让世界作者重新工作，成本会回来——
这个担心是对的，但**账要算准**：真正贵的地方不是"模型生成事件"，而是三个可以独立修掉的工程问题。

### 10.1 实测成本结构（归档库 `companion.epoch1.sqlite`，加权表之前）

life_development 只在 **08-12 与 08-13 两天**真实跑过，随后就被加权表短路了：

| 腿 | 模型 | 次数 | 成本 | 单价 | 缓存命中 |
| --- | --- | ---: | ---: | ---: | ---: |
| `life_development_draft`（**生成内容**） | deepseek-v4-flash | 18 | ¥0.538 | ¥0.030 | 4.8% |
| `life_development_source_closure_review` | openai/gpt-4o-mini | 14 | ¥0.537 | ¥0.038 | **0** |
| `life_development_source_closure_review` | gpt-4.1-mini | 17 | ¥0.276 | ¥0.016 | **0** |
| `life_development_novel_origin_review` | gpt-4.1-mini | 9 | ¥0.810 | **¥0.090** | **0** |
| `life_development_novel_origin_review` | openai/gpt-4o-mini | 1 | ¥0.089 | ¥0.089 | **0** |
| **合计** | | **59** | **¥2.25** | | |

**结论一：审查腿吃掉 76% 的成本（¥1.71 / ¥2.25），生成腿只占 24%。**
她"自己想出一件事"很便宜（¥0.03），"证明这件事没有凭空捏造事实"很贵（¥0.095）。

**结论二：每次 draft 平均拖着 2.3 次审查**（31 次 source closure + 10 次 novel origin ÷ 18 次 draft）。

**结论三：审查腿全部跑在 OpenAI 上且缓存命中为 0**，而生成腿跑 deepseek 且有 context caching。
`novel_origin_review` 单次 ¥0.090，是 draft 的 3 倍——它做的只是"这是不是真的新东西"这一个判断。

对照：`world_v2_character_interior` 809 次 ¥10.372，单价 ¥0.0128，缓存命中 23%。
**一个生活事件（¥0.125）≈ 10 次她的内心活动。**

### 10.2 预算现状：已经在超

`.env`：`MONTHLY_BUDGET_CNY=80`、`DAILY_BUDGET_CNY=3`、`SOFT_DAILY_BUDGET_CNY=2`。

epoch1 实际日花费：

| 日期 | 调用数 | 花费 | 对日预算 ¥3 |
| --- | ---: | ---: | --- |
| 08-07 | 144 | ¥1.647 | 内 |
| 08-08 | 256 | ¥4.164 | **超 1.4×** |
| 08-12 | 448 | ¥6.684 | **超 2.2×** |
| 08-13 | 87 | ¥1.347 | 内 |

08-12 那天 life_development 跑了 30 次（约 9 个事件）——**真人一天不会发生 9 件值得写进人生的事**。
所以那天的超支首先是**频率失控**，其次才是单价。

### 10.2a 重要更正：审查腿在**当前代码**里根本没装

§10.1、§10.2b 的账单全部来自 epoch1（08-12/08-13），那是**旧代码**。核查当前代码后发现：

```python
# semantic_chat_composition.py（修复前）
del source_closure_model, life_source_closure_model, _unused
...
life_source_closure_model=None,
```

传进来的 Life 审查模型被**直接丢弃**，返回值里硬编码 `None`。
而 `config.py` 对这条车道的注释写着：

```
# Life Ecology source closure is a hard dependency of life_development
# (fail-closed when the reviewer is absent)
```

**两件事合起来就是**：审查腿当前成本确实是 0，但代价是 life_development 缺少硬依赖。
它现在没有 fail-closed 报错，只是因为加权表短路（§6.2 第 1 点）让它根本走不到那一步。
**撤掉那条短路之前必须先把 reviewer 装回来，否则事件机会直接 fail-closed。**

**2026-08-14 用户裁决：特批自我审查。** 据此新增显式开关
`WORLD_V2_LIFE_SELF_REVIEW_ALLOWED`（`config.py`，默认 `False`）：

- 关闭（默认）：行为不变，只有显式注入的独立审查者才会装；
- 开启：Life 审查腿由 **World Author 自己**（background deepseek 车道）担任，
  `life_source_authority_health` 里的 `life_source_runtime_isolation` 报
  `self_review_operator_approved`，与 `independent` 明确区分。

生产 `.env` 已显式开启并注明理由。选择"显式开关 + 默认关闭 + health 可见"而不是删掉检查，
是因为这削弱的是一条事实边界，必须在配置和健康检查里都留下痕迹。

**成本含义**：审查腿回来了，但跑在 deepseek 而不是 OpenAI，单位 token 成本降到约 1/9。
真正的降本仍然靠 §10.3 ③ 的频率控制。

### 10.2b 审查腿在 epoch1 从未拒绝过任何一次

统计归档库全部 life-development 事件里两条审查腿的 `decision` 字段（`supported` / `unsupported`）：

| 审查腿 | supported | **unsupported** |
| --- | ---: | ---: |
| `source_closure_review` | 43 | **0** |
| `novel_origin_review` | 43 | **0** |

**86 次判决，零拒绝。** 用量表里那些 `status='failed'` 的记录 tokens 与 cost 都是 0，
是 provider 技术失败重试，**不是审查拦下了内容**。

调用序列也印证 draft 质量不差。早期（08:38–09:19）有重试噪声，
但 17:22 之后稳定为 **1 draft + 1 source_closure + 1 novel_origin**：

```
17:22:13  draft                  succeeded  31944 tok  ¥0.0311
17:22:17  source_closure_review  succeeded   3192 tok  ¥0.0287
17:22:21  novel_origin_review    succeeded  10388 tok  ¥0.0909
```

**这三行还暴露了成本的真正来源**：

| 腿 | tokens | 成本 | 每 1k tokens |
| --- | ---: | ---: | ---: |
| draft | 31,944 | ¥0.0311 | ¥0.00097 |
| source_closure | 3,192 | ¥0.0287 | ¥0.0090（**9.3×**） |
| novel_origin | 10,388 | ¥0.0909 | ¥0.0087（**9.0×**） |

**审查腿贵不是因为读得多，而是因为单位价格是生成腿的 9 倍**（OpenAI vs deepseek-v4-flash）。
`source_closure` 只读了 draft 十分之一的 token，花的钱却差不多。

**另有一个已存在的先例**：交互路径早就退役了同类审查，`.env` 注释写得很直白：

```
WORLD_V2_SOURCE_REVIEW_REDUNDANCY_ENABLED=false
# Source review is retired from the interactive path (2026-08-07): local
# misses every entailment gap) and cloud review cost 10-18s/turn.
```

即：本地审查**抓不到任何蕴含缺口**，云端审查每轮要 10–18 秒。
交互路径据此判定性价比不成立并退役，**life_development 路径没有跟进**。

### 10.3 四条降本路径（都不需要退回剧情库）

按性价比排序。前两条是纯工程优化，不碰任何语义。

**① 在非-deepseek 车道里选更便宜的审查模型 —— 预计省约 58%（source closure 腿）**

> **2026-08-14 二次更正（以本条为准）**：用户已**特批自我审查**，见 §10.2a。
> 现在 Life 审查腿由 World Author 自己担任（deepseek 车道），单位 token 成本约为 OpenAI 的 1/9。
> 下面这段关于"不能换 deepseek"的分析仍然记录在此，因为它解释了这条边界原本为什么存在，
> 以及为什么放宽它要走显式开关而不是删检查。
>
> 另需记录一个事实核查结果：`life_development_reviewer_is_independent` 的两个结果
> （`_source_closure_reviewer_is_independent`、`_novel_origin_critic_is_independent`）
> 在 `life_development_runtime.py` 里**只被赋值，从未被读取**——这条"硬边界"在代码层面
> 一直没有真正强制过。所以本次改动不是绕过一道有效的闸门，而是把一个隐性状态
> 变成了显式、可审计、默认关闭的配置。
>
> ---
>
> **原分析（2026-08-14 初次更正）**：本节初稿建议"审查腿换回 deepseek"，当时判断那是错的。
> 依据是跨 provider 独立性看起来是**硬边界**。`life_development_model_adapter.py:77-90`：
>
> ```python
> def life_development_reviewer_is_independent(*, author, reviewer) -> bool:
>     """Return whether every possible review winner excludes the author.
>     ...  A hard-boundary reviewer is independent only when none of its
>     possible winners overlaps any provider that could have authored the
>     candidate."""
>     return provider_lane_sets_are_independent(author, reviewer)
> ```
>
> 作者跑 deepseek-v4-flash，所以审查腿**永远不能**用任何 deepseek 车道，
> 否则就是让同一个模型给自己的产出背书。降本必须在这个约束内做。

约束内仍有明显空间。同样是 `source_closure_review`，两条车道的实际单价差 2.4 倍：

| 车道 | 次数 | 成本 | 单价 |
| --- | ---: | ---: | ---: |
| `openai/gpt-4o-mini` | 14 | ¥0.537 | ¥0.038 |
| `gpt-4.1-mini` | 17 | ¥0.276 | **¥0.016** |

把 `.env` 的 `WORLD_V2_SOURCE_REVIEW_SECONDARY_MODEL` 从 `openai/gpt-4o-mini` 切到更便宜的那条
（两者都非-deepseek，不破坏独立性），source closure 腿可省约 58%。

`novel_origin_review` 的贵法不同：它单次 10,388 token，是 source closure 的 3 倍，
所以它的账要靠**减少证据切片**或**降频**来降，换模型帮助有限。

**顺带记录一个不一致**：交互路径早就退役了同类审查
（`WORLD_V2_SOURCE_REVIEW_REDUNDANCY_ENABLED=false`，注释写"cloud review cost 10-18s/turn"），
life_development 路径没跟进。这个差异是有理由的（见 §10.6：一句错话 vs 会被继承的世界事实），
但值得在决定 ③ 的频率时一并考虑。

**② 让审查腿吃上缓存 —— 预计再省 20–30%**

审查腿 `cache_hit_tokens` 与 `cache_miss_tokens` 全是 0，等于完全没有缓存参与。
而审查的 prompt 前缀（review contract + output schema + 硬边界说明）是全系统**最稳定**的一段，
天生适合前缀缓存。换到 deepseek 会自动获得 context caching；留在 OpenAI 则要确认 prompt caching 是否生效。
顺带：draft 腿命中率只有 4.8%，而 character_interior 是 23%，这条与 §9.6 的 T6 是同一个病根。

**③ 用加权表决定"有没有事"，让模型决定"是什么事" —— 预计省 50–80%，且不牺牲任何不可知性**

这正是被批准的判据：**表决定机会，她决定内容与意义。**
加权表本身没有错，§12.9 第 8 条错在**让目录连"是什么事"也定了**，那才变成剧情库。

正确形状是把那次短路从"直接返回 no_op"改成"先抽一次机会"：

```
weighted_table 抽「今天/这次醒来有没有值得写的事」   ← 0 模型调用
        ↓ 命中才继续
world author 决定「是什么事、什么后果、怎么影响以后」  ← 付费，但频率大幅下降
```

频率控制点**已经存在**，不用新建：`life_ecology_runtime.py:250`

```python
development_due = due_at is None or logical_time >= due_at
```

`due_at` 取自 `trigger_store.next_consideration_at()` 或 `projection.life_ecology_schedule.next_consideration_at`。
调这个节奏 + 加一层加权机会抽取，就能把"一天 9 个人生事件"压到"一天 0–2 个"。

**④ 大小事件分档：大事件付费，小事件搭车 —— 小事件成本归零**

- **大事件**（会开 Life Arc 的，如实习）：值得付全套 draft + 审查，但**几个月一次**；
- **小事件**（回家路上看见一只猫）：搭 `day_open` 与 inbound 已付费调用的车，**0 新增调用**。

H15b 已经做了一半（slim 说明允许她把当日注意到的写进 `felt`/`stuck_with_me`），
但**没有做分档路由**——目前所有生活发展都走同一条全套审查的重路径。
补上分档后，"小事件丰富度"和"大事件有分量"可以同时成立，而且便宜的那一档不花钱。

### 10.4 修完之后的账

假设目标节奏是：小事件每天 1–2 个（搭车，¥0）+ 大事件每月 2–3 个。

| 项 | 修前 | 修后 |
| --- | ---: | ---: |
| 单个大事件 | ¥0.125 | ≈¥0.05（① + ②） |
| 大事件频率 | 每天 ~9 个 | 每月 2–3 个（③） |
| 小事件 | 走全套重路径 | 搭车 ¥0（④） |
| **生活生态月成本** | **~¥35** | **≈¥0.15** |

对照月预算 ¥80，修完后生活生态占 **0.2%**。
**所以"让她真的自己生活"在经济上完全可行；当前的贵是工程问题，不是路线问题。**

### 10.5 施工顺序建议

1. 先做 ①②（纯工程，不碰语义，先拿到便宜的审查腿）——但**① 要先确认 provider 独立性是不是硬要求**；
2. 再撤 `life_development_runtime.py:4297` 的写死短路，同时按 ③ 装上加权机会抽取——
   **这两件事必须同批**，否则一撤短路就是每次 wake 全速调用，日预算当天就爆；
3. 然后做 ④ 的分档路由；
4. 最后才打通 §9.4 的动态 Life Arc 腿——那时候大事件已经稀疏且便宜，开 arc 才划算。

**观测口径**：每天核对 `world_v2_model_usage` 里 `purpose LIKE 'life_development%'` 的日成本，
目标是稳定低于 ¥0.1/天；同时核对 `SOFT_DAILY_BUDGET_CNY=2` 没有被触发。

### 10.6 能不能干脆不要审查？

用户提问（2026-08-14）：既然一次成功率接近 100%，能不能直接去掉审查腿。
数据支持这个直觉（86 次判决零拒绝，§10.2b），而且交互路径已有退役先例。
但"去掉"有三档，成本与风险差别很大，**不要一步跳到第三档**。

**先做一个必要的区分**：审查腿现在混着做两件事，只有第二件真的需要模型。

| 检查类型 | 例子 | 谁能做 | 成本 |
| --- | --- | --- | --- |
| **结构性** | 声称的 ref 是否存在；是否早于 pinned cursor；权限够不够；声明为 novel 的条目 `source_refs` 是否真为空；typed location 是否和语义 Plan 冲突 | **确定性代码** | 0 |
| **语义蕴含** | 「林晚邀请她去诗歌朗诵会」这句话，是否真被它引用的那条来源蕴含 | 只能模型 | 贵 |

`life_development_draft.py` 已经要求模型把事实声明结构化
（`source_refs`、`novel_world_generation` 且 `source_refs` 为空），
所以第一类检查**本来就可以不花钱**。动手前应先盘点现有确定性校验覆盖了多少，
再决定语义腿还剩多少不可替代的价值。

**第一档：在非-deepseek 车道里换更便宜的审查模型（零语义风险，建议立刻做）**

见 §10.3 ① 的更正：**不能换 deepseek**（那会破坏 hard-boundary 的审查独立性）。
但同为非-deepseek 的两条车道单价差 2.4 倍，切到便宜的那条即可让 source closure 腿省约 58%，
**安全性一点不减**。`novel_origin` 腿贵在 token 量，要靠 ③④ 降频和分档。

**第二档：自适应抽检（再省一半，保留观测能力，推荐）**

不是每次都审，而是抽 10–20% 审；**连续 N 次 supported 就降低抽检率，一旦出现 unsupported 立刻恢复全审**。
这样既把成本压到接近零，又保留了发现质量退化的能力。

**抽检的价值有一半不在拦截，而在观测。** 这是第三档最大的代价：
完全去掉之后，**如果哪天她开始编造事实，系统里没有任何人会发现**——
不会有告警，不会有指标，只会在某次对话里突然说出一件没发生过的事。

**第三档：完全去掉语义审查（最激进，须满足前置条件）**

可以做，但**不能是"直接删掉调用"**，必须同时满足：

1. **结构性检查补齐并有测试**：ref 存在性、cursor 边界、权限、novel 声明的 `source_refs` 为空、
   typed location 与语义 Plan 一致。这些原本部分依赖模型顺带发现，去掉模型后必须由代码显式保证。
2. **保留一条极低频的审计车道**（例如每天 1 次或每 20 个事件 1 次），只为观测，不阻塞落账。
   否则等于放弃了对"她开始编事实"的任何感知能力。
3. **明确记录这是一次风险决策**，写进执行计划，而不是默默改配置。

**为什么不建议直接跳到第三档：**

- **0/43 不等于 0/∞。** 样本是 43 次判决、单一 prompt 版本、单一模型、且世界当时几乎是空的
  （没有 Life Arc、没有活跃 NPC 关系网）。按经验法则，零观测失败在 n=43 下，
  真实失败率的 95% 置信上界仍有约 **7%**。世界变复杂、事实基底变厚之后，
  幻觉的机会只会变多不会变少——**恰恰是打通动态 Life Arc 之后风险最高**。
- **AGENTS.md 把"事实引用与事件权限"明确列为确定性代码必须守的硬边界**。
  硬边界可以用更便宜的方式守（第一档、第二档都是），但不能不守。
- **交互路径的退役先例不完全可比**：那条路径的产物是**一句话**，说错了用户当场能看出来、
  也能立刻反驳；life_development 的产物是**会落进世界事实、被后续所有事件继承**的 occurrence。
  同样一次幻觉，前者是一句错话，后者会长期污染因果基底。

**建议顺序**：先第一档（立刻，无需决策）→ 观察若干天 → 再上第二档抽检 →
只有当抽检在足够长的窗口里仍是零拒绝，才考虑第三档，且必须保留观测车道。
