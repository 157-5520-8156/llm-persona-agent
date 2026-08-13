# Harness 收缩执行计划：一条模型主路径与连续存在

状态：立项（2026-08-13）。本文是**当前唯一的施工顺序、验证门槛与交接契约**。

唯一业务与架构基线：[`girl-agent-design-intent.md`](./girl-agent-design-intent.md)（含 2026-08-13 的 §12 产品裁决）。

## 0. 这份文档是什么，和其他文档的关系

- **本文取代** [`root-causes-and-long-coupling-luna-plan.md`](./root-causes-and-long-coupling-luna-plan.md)
  成为当前执行计划。那份文档降级为 **L0–L3 期的历史施工记录**：其执行记录与证据仍然可查、可引用，
  但**不再产生任务**，其 L4–L15 的剩余排期被本文的 H1–H11 取代。
- 交接包仍然只有两份：设计总纲 + 本文。业务信息只补入设计总纲，施工证据只补入本文。
  不新增第三份并列计划。
- `AGENTS.md`（受控高随机宗旨）、`CONTEXT.md`（术语）、
  [`ADR 0010`](../adr/0010-controlled-high-variance-character-agency.md) 照常适用，冲突时以宗旨优先。
- 本文自含交付模板（§14）与人工门（§15），实施者不需要回去读历史计划。

## 1. 立项证据（生产账本只读实测，截至 2026-08-13）

| 曾经写明的约束 | 生产实测 | 证据来源 |
| --- | --- | --- |
| 无新事实的 poll 不写语义事件 | life-ecology 1,340 次完成里 1,050 次是 `cooldown`，空转仍写 TriggerProcess 四件套；`AffectEpisodeDecayed` 8,635 条（占 34,809 事件的 24.8%）；TriggerProcess 四件套约 10,741 条（30.9%）；`ClockAdvanced` 2,641 条 | `world_v2_events` 全量分组 |
| 首 Beat 前只允许一个角色模型请求 | 8/12：5 条 `ObservationRecorded` 对应 372 次 `world_v2_character_interior` 调用 ¥4.503；8/8：2 条对应 247 次 ¥4.033；私人印象累计 3,034 次调用 → 6 条 `PrivateImpressionAccepted` | `world_v2_model_usage`、`ModelResultRecorded.attempt_id` 前缀 |
| 各 purpose 首次结构合法率 ≥99.9% | 按 lane 差异极大：private-impression **0.2%**（3,003 attempt / 7 validated）、proactive 17.9%、pinned-turn 38.0%、life-development 82.9%、other 91.3%。恢复机制成功率约 2%（`main_invalid_recovered` 35 对 `recovery_failed` 1,626） | ModelResult `audit_json` 按 attempt 前缀 × status 分组 |
| Fast Reply p50 1–2s、首 Beat ≤2s | `character_interior` succeeded n=695：p50 4,242ms、p90 6,155ms、p99 8,508ms、max 16,614ms。而 `hedge_after_seconds=2.0`，于是 backup 调用占 primary 的 21–37%，timeout 成为 pinned-turn/proactive 的最大失败码 | `world_v2_model_usage.latency_ms`；`interactive_turn_budget.py:54` |
| 每次消费先原子 reservation，health 看 burn | 用量记账最早一行是 2026-08-07T05:48Z；8/4 那日 2,769 次私人印象调用完全没有账单 | `world_v2_model_usage` 覆盖区间 |
| 设计总纲 §10.4：对话 ¥60 / 后台内心 ¥15 | 对话实际约 ¥13/月量级；后台内心按 8/12 单日折算约 ¥135/月 | 成本模型 + 实测调用数 |
| 上下文足以支撑连续存在 | 一次聊天平均输入 15,727 token、输出 471 token，她能看到的只有 4 条 utterance、2 条经历、2 条私人印象、1 条记忆、3 条事实；prompt 缓存命中仅 23.1%（DeepSeek 命中价是未命中价的 1/50） | `snapshot_compiler` 截断常量 + usage 表 token 与 cache 字段 |
| 生活轨迹可被用户看见 | 三周 `ActivityStarted`/`ActivityCompleted` 各 13 次、`WorldOccurrenceSettled` 38 次；`FactCommittedV2` 20 条止于 8/7；`ThreadOpened` 累计 1 次；`ExternalPerceptionRecorded` 2 次；`ProviderMediaGrantRecorded` 4 次 | 事件类型分组 |

**结论：真人感的第一瓶颈不是机制数量，而是编译给角色的"现在"太薄；同时后台把钱花在改不了状态的调用上。
这不是设计缺口，是缺强制门。**

成本模型（DeepSeek 公开价，7.2 CNY/USD，按实测 12 条消息/天 × 2.2 次调用 × 30 天 = 792 次/月）：

| 对话形状 | 模型 | 缓存 0% | 缓存 70% |
| --- | --- | ---: | ---: |
| 现状 15.7k in / 471 out | flash | ¥13.3 | ¥4.7 |
| 现状 15.7k in / 471 out | pro | ¥41.4 | ¥14.3 |
| 加厚 25k in / 400 out | flash | ¥20.6 | ¥6.9 |
| 加厚 25k in / 400 out | pro | ¥64.0 | ¥21.0 |

即：**加厚上下文不需要额外预算，对话额度一直没被用满。**

## 2. 用户裁决（2026-08-13，不得由实现者改写）

1. 接受"新纪元"：封存当前账本为只读归档，用编译出的连续性快照作为新账本 genesis。
2. 聊天主模型**暂不**换 `deepseek-v4-pro`（当日刚发布正式版，能力尚不稳定，可能低于 flash）。允许并要求做
   缓存前缀改造；换模型只能由真实自由对聊 A/B 证据决定。
3. 主观残留应当简化落账，但**必须保留连续、多变、会升级的情绪**：允许"越想越气"并据此采取进一步行为。
   允许为此使用模型，但要按 §6 的办法压成本。
4. 人生节拍的双模型审查**由"冻结"升级为"删除"**（见裁决 7 与 §12.10）；P3 私密媒体车道保持冻结。
   **外部感知与 NPC 不冻结**，按 §8 改成由主路径承载。
5. 主动联系次数**由角色自己根据内心与心情决定**，不做表面去重或次数配额（要允许正常的"心心念念"），
   但必须从根因上消除"总在想同一件事"。
6. 记忆召回先保证**不依赖 embedding** 可用；embedding 只作为后接的加分项。
7. **一次成功（one-shot）是模型调用的默认形态。** 目标是一次生成即可用：省成本、省时间，并且**减少幻觉**
   （多轮审查会引入审查模型自己的错误）。现存的模型审查车道基本是当年技术做不到 structured output
   时留下的历史包袱，应当删除而不是优化。判定边界见 §12.10——**删的是"再问一次模型"，
   留的是"确定性地核对一次"**。

## 3. 唯一 seam：Occasion → Present → Consider → Consequence

全系统只保留一条模型主路径。四层职责与出钱方式：

| 层 | 职责 | 模型 | 归属判据（AGENTS.md） |
| --- | --- | --- | --- |
| 世界步进 | 时钟、日程骨架、活动窗口、情绪强度、感知采集 | **0 次** | 系统硬边界：提供有来源的环境 |
| 机会（Occasion） | 决定"什么时候让她想一次"，合并/去重/过期 | 0 次 | 系统硬边界：时机与注意力可由系统和随机性决定 |
| 她（Consider） | 说不说、说什么、发几条、气不气、要不要做什么、要不要发照片 | **每个机会恰好 1 次** | 角色的决定，系统不得预判 |
| 后果（Consequence） | 落账、发送、回执、留下残留 | 0 次 | 系统硬边界：effect-once、CAS、隐私、来源 |

硬规则：

- **一个 Occasion 恰好一次 `consider()`**。没有 Occasion 就没有模型调用。禁止任何 worker 自行发起模型调用。
- **一次模型调用必须留下将来的她还能读到的状态**：一段经历、一个坐标、一条情绪残留、一件未完成的事，
  或一张有来源的照片。产出不进入未来 Present 的调用不得存在于主路径。
- 来源闭包、隐私分级、Action 授权、effect-once 由**她之后的确定性代码**检查，不再要求她自己在输出里证明。
  检查失败时把精确原因回给同一角色模型，只回一次；仍失败记技术故障，不得本地模板冒充她说话。

Occasion 类型固定五种，新增需用户批准：`user_message`、`quiet_gap`、`unsettled_feeling`、`life_beat`、
`day_open`。每种必须声明：上游 accepted 事件或时间来源、合并键、过期时刻、以及"没有新事实时不生成"。

**重要：这五种机会大部分是把现有 producer 合并改造，不是新建。** 对应关系见 §13 代码坐标表。

## 4. 强制门（必须可在 CI 与 health 上跑，缺一不算完成）

| 门 | 内容 | 失败即 |
| --- | --- | --- |
| G1 账本写入准入 | 语义事件写入前检查"是否带来新事实"。no-op poll、cooldown 空转、claim/lease/retry、心跳一律不得进不可变账本 | 架构测试红 |
| G2 单次考虑上限 | 同一 Occasion 身份下第二次 `consider()` 直接拒绝；health 暴露"每条用户消息的模型调用数"，>3 告警 | 架构测试红 + health 告警 |
| G3 记账前置 | 未完成 purpose/actor/provider/estimated CNY 原子 reservation 的调用不许发起；`purpose` 不得再统一记成 `world_v2_character_interior` | 调用直接拒绝 |
| G4 契约面积上限 | 角色主输出工具 schema 的必填字段数与嵌套深度设上限（初始：必填 ≤3、总字段 ≤8、深度 ≤2）；超限需用户批准。初始值是估计，必须用真实首次合法率验证后再定 | 架构测试红 |
| G5 Present 预算与前缀顺序 | 编译结果按"稳定→易变"排序，稳定段字节序逐轮一致；输入 token 预算与实测 cache hit 进 health，命中率 <50% 告警 | 架构测试红 + health 告警 |
| G6 无孤儿产出 | 每个 model-bearing purpose 必须能指出"它的产出出现在哪个 Present 段落"；指不出的 purpose 从主路径删除 | 架构测试红 |
| G7 机会派生有界 | 机会只能从本步新接受的事件派生；禁止扫描随历史增长的投影集合（如逐条遍历 `appraisals` 派生机会）。每个机会带 expiry，重启后过期的丢弃而不是补做 | 架构测试红 |
| G8 花钱前先判定 | 每次模型调用前必须有确定性谓词判定"存在可能改变结果的新材料"；`no_change` 类结论必须算出来而不是问出来。无效成本率（重试/修复 ÷ 总成本）进 health，>10% 告警 | 调用直接拒绝 + health 告警 |

## 5. Present 契约（替代当前 8-facet 硬截断）

### 5.1 已核实的当前形状（纠正三处此前的误判）

1. **散文人设根本没进 prompt。** `configs/character.yaml` 的 `base_prompt`、`appearance`、`background`、
   `daily_life`、`first_message` 在 World V2 主调用里**没有任何消费者**（`base_prompt` 只被 `character.py`
   加载和测试引用）。进入 prompt 的只有 `CompanionIdentityFrame`：`canonical_facts`、`personality`、
   `speech`、`style_rules`、`boundaries`、`values`，而且是 system 段里的一段 **JSON**，外面还跟一份
   scope→`identity-frame:sha256:...` 的 ref 清单。`character_core` / `stable_self` 是离线结构化出来的
   **trait 数值轴**，不是散文。**没有任何文风示例。** 她不像 `character.yaml` 里那个人，首先是因为她没读过。
2. **facet 只传 key，但 materials 传全文**——此前"她读不到内容"的判断是错的。
   `InnerLifeSnapshot.model_view()` 把完整语义内容放在顶层 `materials` 传给模型，`faculties` 才只有
   `material_keys`。真正的问题是 `materials` 是一堆并列、带 `source_ref` 的 JSON 对象，**没有时间线、
   没有叙事顺序**，读起来像合规查阅面板，而不是一个正在过日子的人。
3. **对话历史比"4 轮"更薄：是 4 条 utterance（气泡级，约 2 个来回）。** Capsule 预算 6 条，快照再裁到
   `[-4:]`；而且一旦有 `inner_life_snapshot`，Capsule 的 `slices` 会被主动从 provider 视图剥掉，
   别处也不再有更长历史。

配套实测：system 段约 20k 字符**几乎全是英文机制契约**，user 段约 24k 字符里 `expression_hard_boundaries`
独占约 6.5k；机制话语占输入的 **55–65%**，生活与对话材料只占 35–45%。**加厚"现在"的钱从契约面积里省出来
就够了。**

### 5.2 分段与预算

一次编译，自然语言为主，按稳定→易变排序，让 DeepSeek 自动前缀缓存吃到最大段。

| 序 | 段 | 内容 | 预算 | 变化频率 |
| --- | --- | --- | ---: | --- |
| 1 | 她是谁 | `character.yaml` 的人设、外貌、成长背景、日常习惯、说话风格、价值与边界，自然语言整段 + 文风样例 | ~1.5k | 版本级 |
| 2 | 契约与能力 | 她能用什么能力、输出什么形状、什么是硬边界 | ~0.8k | 版本级 |
| 3 | 长期事实 | 她的传记事实、用户档案（他是谁、在忙什么、承诺过什么） | ~1.5k | 日级 |
| 4 | 最近 7 天日记 | 按天可读，每天 1–3 行，来自已落账的日程与经历 | ~1.5k | 日级 |
| 5 | 关系与未完成的事 | 关系状态、open threads、她欠他的和他欠她的 | ~0.8k | 小时级 |
| 6 | 召回的记忆 | 3–8 条，按相关度；不依赖 embedding 可用 | ~1.2k | 每轮 |
| 7 | 情绪与未平的事 | 每个未平 episode：起因原文、当时的感受、她已经想过几次、上次想到哪 | ~1k | 每轮 |
| 8 | 她最近发出的消息 | 时间、原文、**是否被回应**；这是反重复的根因材料（§7） | ~0.8k | 每轮 |
| 9 | 对话史 | 尽可能长，目标 40 轮以上；超预算时最旧部分折叠为摘要，不得直接丢 | ~8k | 每轮 |
| 10 | 此刻的机会 | 这是什么机会、现在几点、她在做什么、身体与环境 | ~0.5k | 每轮 |

总预算 ~20k 输入。**允许为上下文加厚花钱，不允许用截断省钱。**

### 5.3 必须做的三件事

- **把散文人设接进第 1 段**，并补一小段她的文风样例。这是当前投入产出比最高的单点改动。
- **删掉固定条数截断，改成叙事顺序 + token 预算。** 第 4、7、9 段必须按时间排序、可当叙事读；
  `source_ref` 保留但不占据阅读主位。
- **契约面积让位给生活材料。** `expression_hard_boundaries` 与 system 段的双契约拼接是加厚上下文的资金来源；
  来源闭包检查移到她之后（§3），manifest 不需要整表进 prompt。

### 5.3.1 前缀顺序是硬要求（G5），有两个已定位的缺陷必须修

见 §12.3 第 5 条。H3 必须做到：

- **system 段不得按逐轮状态分叉。** `recall_available` 目前决定 system 段第一句
  （`inbound_author.py:3164-3178`），必须改为一句同时覆盖两种情况的固定措辞，把"这轮能不能召回"
  移到段 10 里说。
- **user 段按稳定→易变重排。** 当前第一个键是 `current_trigger_message`
  （`inbound_wire.py:11735-11740`），必须移到最后。段序即 §5.2 的 1→10。
- **对话史按最旧→最新追加。** 只能在尾部增长，不得每轮重排或整体重写摘要；折叠摘要一旦生成就冻结，
  下一次折叠只追加新的摘要块。这条决定了缓存命中率能不能随上下文加厚而上升。
- **稳定段字节序逐轮一致**：JSON `sort_keys=True`、浮点与时间戳格式固定、不得混入 uuid/时间戳类
  逐轮量（`world_revision`、`ledger_sequence`、`trigger_ref` 只能出现在段 10）。

红测：连续两轮编译，断言两次 prompt 的公共前缀长度 ≥ 稳定段总长；断言 system 段两轮字节相同。

### 5.4 不需要新建的

节奏能力已经在契约里：`timing_choice`（now/later/silent）、`cadence`（rapid/conversational/hesitant/
escalating）、`turn_posture`（yield/continue/interject/supersede）、`delay_seconds`、`response_expectation`、
`beats` 最多 8。已读不回、打断、连发都已可表达，生产实测 28% 的回合确实是多气泡（106 个 plan 中 28 个两条、
2 个三条）。这一块的工作不是加字段，而是让她有足够上下文用好它们。

另注：`_is_lossless_minimal_reply_draft` 只用于 quick recovery，**不是**生产分流路径；`CLAUDE.md` 里
"fast reply 分流"的描述是错的，改动时不要照它去找。

## 6. 情绪连续性与"越想越气"（用户明确要求，不得简化掉）

设计目标：情绪是连续、多变、会升级的，且升级能导致进一步行为；同时不产生自激反思农场。

- **强度改成时间的纯函数**，由起因强度、经过时间和她自己的残留决定。现状澄清：`AffectEpisodeDecayed`
  已经是"仅在强度变化时才写"，8,635 条来自多 episode × 多 component 的组合，不是无条件心跳。它被刻意物化
  是为了让 Capsule revision 与 replay hash 一致，因此**这一改动必须随新纪元的 reducer bundle 一起落地**
  （归入 H10），不能在旧账本上原地改。
- **机会复用现有 lane，不新建。** `unsettled_feeling` 落在已有的 `ReflectionScheduler` +
  `world_stimulus` 的 `life_reflection` 消费点上，把它改成读原始起因；阈值与次数上限按强度衰减重新定义，
  不再是固定 `MAX_REFLECTIONS_PER_APPRAISAL=2`。
- 生成条件：强度仍在阈值以上，且**发生了真实变化**——新输入到达，或距她上次想到这件事已超过一个逐轮增长的
  间隔，且她没有把它标记为"我这事完了"。间隔增长是机会节奏（系统职责），不是行为规则。
- 这一次 `consider()` 读的是**原始的伤**（他说了什么、她当时的感受）加上她自己历次残留，**不是**读一条由
  模型自己生成的反思链。升级因此来自真实累积，而不是自我放大。
- 她可以在任何一次里选择：说出来、做点什么、继续憋着、或者认为这事结束了。次数上限由强度衰减和她自己的
  终止决定，不设固定配额。
- **多变的来源是世界侧的确定性节律 + 机会侧的随机注意力**：作息、时段、天气、身体状态、日程压力是有来源的
  环境；随机性只决定哪些材料先浮现、机会落在什么时刻。禁止用随机数直接决定她的情绪或行为。
- 成本量级：一场争执大约多出 2–4 次调用（≈¥0.05）。这是本设计里允许的开销。

## 7. 反重复：只修根因，禁止表面去重

8/8 一小时内连发三条几乎同文的"雅思/雨"，根因是她**看不见自己刚说过什么、更看不见有没有被回应**。
代码层面的直接原因已定位：`RecentDialogueItem` 本来带 `delivery_state`，但在进快照时被**刻意丢弃**。

修法：

1. Present 第 8 段给她看最近发出的每一条消息、时间、以及**是否被回应**（把 `delivery_state` 接回来）。
   她知道自己问过、也知道没被回，自然不会再问第三遍。
2. 第 4/7 段的材料新鲜度来自世界真的动了（日程推进、结算的经历、变化的关系），而不是同一批切片反复上桌。
3. 她的残留可以记下"这件事我已经提过了""他没回我，我先不问了"，未来 Present 一定会读到。
4. 随机注意力改变材料浮现顺序，让同一情境下不同时刻的关注点不同。

**明确禁止**：主题黑名单、n-gram/相似度去重、固定话题冷却、按次数封顶主动联系。这些都是替角色作语义决定，
违反 AGENTS.md，也会消灭"心心念念"。

边界说明：现有 `social_initiative` 的 idle 1800s 与 contact_cooldown 900s 属于**机会节奏**（系统何时给她一次
考虑的时机），不是话题或次数规则，予以保留。判据是：它约束的是"什么时候让她想"，还是"她能不能想这件事、
能不能说"。前者是系统职责，后者一律不许。

## 8. 生活密度：确定性日程 + 每日一次开场

- **世界侧日程骨架（0 模型）**：每天本地零点由确定性规则落一份当日世界事实——课表/校历、通勤与作息窗口、
  天气、店铺开闭。这是环境，不是她的行为，因此不违反宗旨。
- **`day_open` 一次调用**：她读昨天与今天的世界日程，说出今天大致想怎么过（自由文本 + 0–3 个意图）。
  之后活动由**她说的意图** + 现有 timing 规则确定性开合，不再每个活动一次模型调用。
  1 次/天 ≈ ¥0.4/月，换来一条每天可读的日记，而且是她自己定的。
- **重节拍每周 3–5 次**：新的人、坏消息、机会这类会改变轨迹的事，单次作者调用 + 确定性来源检查。
  **删除双模型审查**（8/12 单日审查 ¥1.29，0% 进入用户视野）。
- **外部感知不再有独立作者链**：采集与聚类保持无模型；被采集的条目作为环境进入 Present 候选池，由随机
  注意力决定哪 1–2 条出现在她面前。她注意到什么、要不要提，在正常 `consider()` 里发生。
- **NPC 走同一模型**：NPC 动向作为重节拍的一部分被同一次作者调用产出，不再为每个 NPC 私有决策单独调模型。
- **日记确定性编译**：由已落账的日程、活动、结算经历和她的残留拼成，不额外调用模型。当前聊天侧没有
  "一周日记"专用聚合，`world_life` 靠 Capsule recency rank 挤（预算 3 条）；仪表盘反而有 24 小时
  `today_activities` 与 7 天 upcoming。**能力不对称要反过来：日记优先给她，不是优先给面板。**

### 8.1 现状与阻碍（代码盘点）

- **78% cooldown 的真正原因是唤醒频率高于 cadence，不是调度器坏了。** 生活只在 idle heartbeat 提交
  `ClockAdvanced` 时被唤醒（心跳默认 600s），而 `next_consideration_at` cadence 是 120–28800s；没到点的
  每次唤醒都要 claim→complete 一次 cooldown，reducer 还刻意不推进 schedule。
  **最小修法是把生活改成 due 驱动唤醒**，而不是每次心跳都 wake。这一条改完，约 1,050 次空转与随之而来的
  TriggerProcess 记账直接消失。
- **确定性日程的抓手已经有一半。** `configs/world_seed.yaml` 里有 `biographical_lifecycle`
  （`birth_date`、`academic.term_windows`）和 `materialize_current_schedule: true`，
  `BiographicalLifecycleCatalog` 与 `LifeEcologyScheduleProjection` 是现成的确定性层。
- **真正的阻碍是活动必须由模型从 catalog 选 `opening_token`**，reducer 用 catalog hash 校验。所以
  "确定性日程骨架"不能伪装成 activity：骨架落成**世界事实/日程窗口**，activity 仍由她在 `day_open` 里
  表达的意图驱动，再由规则开合。这条边界不能为了省调用而越过。
- **外部感知默认关闭**（`WORLD_V2_EXTERNAL_PERCEPTION_MODE=off`），需要 registry 与 sidecar，fail-closed。
  并入 Present 候选池时要显式开启并声明预算，不能默默常开。

## 9. 记忆：兜底其实已经存在，卡在配额和口径

代码盘点纠正了此前的判断：**无 embedding 的召回通道已经在跑。** `WORLD_V2_RECALL_SEMANTIC_ENABLED`
默认 `false`，生产默认用本地 `FeatureHashRecallEmbedding`，并保留精确 lexical / temporal / structured 通道；
provider 故障走 `RecallEmbeddingUnavailable` 降级而不是 fail-closed。所以"她失忆"不是没有召回器，而是：

1. **召回结果被配额掐死。** recall `limit` 只有 1–6，prefetch ≤6，快照再把 `remembered_material` 与
   `recalled_emotional_associations` 各裁到 **1 条**。召回了也只进 1 条。
2. **两条记忆源没有定主次。** `FactCommittedV2` 与 `MemoryCandidateAccepted`
   （`FactMemoryCandidateLifecycle` / `ExperienceMemoryCandidateLifecycle`）并行，谁是聊天召回的权威没有
   明确口径，导致两条都稀疏。
3. **写入频率低是上游稀疏的结果**，不是记忆模块坏了：fact 依赖 inbound 主路径是否产出 proposal，
   experience 依赖生活是否结算（三周 13 个活动）。

H9 的范围因此是：抬高召回配额到 Present 第 6 段的 3–8 条、确认 lexical lane 在生产 composition 里已装配、
明确以哪条为聊天召回权威、health 暴露最近一次记忆写入时间。**不新建打分器。** 语义 embedding 保持默认关闭。

## 10. 新纪元迁移（用户已批准）

1. 归档：当前 `data/companion.sqlite` 整库转为只读归档，保留完整可审计历史。
2. 编译连续性快照：角色事实、用户事实、已巩固记忆、关系状态、传记时间线、最近 30 天日记、未平情绪与
   未完成的事。快照是**她记得的东西**，不是流程事件。
3. 新库 genesis：沿用现有 `WorldStarted` + `BiographicalTimelineConfigured` 的 bootstrap 抓手，
   再追加一批携带快照内容的 genesis 事件。
4. 启动改为增量校验。**当前没有任何快启动开关**——`WORLD_V2_FAST_STARTUP` 只在一份研究笔记里被提议过，
   `config.py` 与代码中都不存在，不要当成现成能力。冷启动实际做的是：`_verify_cold_ledger_history` 先
   `_project_locked()` 读 head，再 `_replay_locked(target_cursor=None)` 从 genesis 逐条 reduce 并比对，
   然后对每个 commit 做 `_verify_cold_commit_locked`（envelope hash / request_hash / result 绑定），
   最后 `_ensure_or_restore_prefix_proof_state` 再扫一遍写 MMR/locator/checkpoint。reducer bundle 升级时
   还可能**再 replay 一次**。
5. 情绪衰减改时间纯函数（§6 第一条）随本包的新 bundle 一起落地。
6. 迁移必须可回滚：归档在、快照可重算、新库可从快照重建。

### 10.1 已核实的硬阻碍（实施前必须逐条给方案）

| 阻碍 | 说明 |
| --- | --- |
| `committed_world_event_refs` 与 `world_revision` 是 1:1 | 每条 WORLD 事件 append 一条 ref，`world_revision = len(refs)+1`。压缩 genesis 与给 head 瘦身**是同一个问题**，必须一起解，且要新 bundle 版本 + migration 故事 |
| 无 snapshot-start API | `rebuild()` 只校验同库 head，不是截断历史；epoch 切换需要新写工具链 |
| idempotency 命名空间 | 新账若复用旧 observation idempotency key 会冲突；commit `request_hash` 绑定完整事件字节 |
| 启动门 | `assert_bounded_vertical_coverage()` 要求新 composition 注册全部 vertical |
| `WorldStarted` 不变量 | 有 state 而无 `WorldStarted` 直接失败 |
| prefix proof 不是可选摆设 | 表缺失会触发 O(N) 重建，partial 直接 `LedgerIntegrityError`；运行时 `observation_events_at` 依赖它（Fact 证据、Memory 原文、感知触发都走这条） |
| TriggerProcess 是 lease/effect-once 权威 | 移出账本必须落到**可清理的 durable sidecar**，不能是进程内内存表，否则崩溃后丢失 lease 归属与 attempt 序号 |
| head `state_json` 里无界增长的不止一个字段 | 除 `committed_world_event_refs` 外，还有 `trigger_processes`（terminal 不删）、`completed_trigger_ids`、`clock_transition_history`、`appraisals`/`affect_episodes`（历史条目保留）、`world_occurrences`/`experiences`/`plans`、`model_result_audits`/`proposal_audits`。9.3MB head 是它们共同的结果，逐个都要给出有界保留窗口或改 SQL 查询 |

顺带清理项：旧 v1 `world_snapshots` 表 131MB，`ledger_maintenance.py` 已有清理入口。

## 11. 媒体：卡点是配置不是设计

`ProviderMediaGrantRecorded` 只有 4 次，原因不在生成器。必经链路是 PhotoCandidate（确定性）→ 角色选片（模型）
→ acceptance（grant + 预算 + 关系）→ planning（模型）→ render（OpenAI）→ inspection（模型）→ auto delivery。
两个默认关闭的开关 `WORLD_V2_MEDIA_PREVIEW_ENABLED=false` 与 `ALLOW_AUTO_IMAGE_GENERATION=false` 挡在最前面，
另外需要 provisioning 出 planning/render/inspection 三个 grant id。

H11 的范围：开这两个开关、跑 grant provisioning、确认生活侧真的产生 PhotoCandidate（依赖 H8 的生活密度）、
把 planning 与 inspection 合成一次调用或改成确定性检查、确认 auto delivery 的默认 2 张/天与 2 小时间隔
是否合适。月预算 ¥25，目标 15–25 张。**照片只能给已经发生过的生活出图。**

## 12. 成本与设计缺陷：结构、根因、措施、行业对照

### 12.1 成本的形状：92.5% 是未命中的输入 token

按实测均值拆解一次聊天调用（flash，15,727 in / 471 out / 缓存命中 23.1%，单价 ¥0.0132）：

| 成分 | 金额 | 占比 |
| --- | ---: | ---: |
| 未命中输入（12,110 tok × ¥1.008/M） | ¥0.01221 | **92.5%** |
| 输出（471 tok × ¥2.016/M） | ¥0.00095 | 7.2% |
| 命中输入（3,617 tok × ¥0.02016/M） | ¥0.00007 | 0.55% |

所以整个成本问题可以写成一个乘积：

> **月成本 ≈ 调用次数 × 每次未命中的输入 token × ¥1.008/M**

三个因子当前同时处在最坏状态：调用次数是应有的 20 倍，输入里 55–65% 是机制话语，未命中率 77%。
优化任何单一因子都只是线性收益，三个一起改才是数量级收益。**注意第二个因子的方向：本计划要把输入从
15.7k 加厚到约 20k**，这是有意增加的支出，由另外两个因子买单。

### 12.2 每个产出实际花了多少钱

用实测单价（¥0.0132/次）折算三周的 `ModelResultRecorded` 调用数（记账 8/7 才上线，历史调用未全部入账，
故为折算值而非实测账单）：

| lane | 机会数 | provider 调用 | 真实产出 | 折算成本 | 单位产出成本 |
| --- | ---: | ---: | ---: | ---: | --- |
| 私人印象 | 1,505 | 2,991（1,498 主 + 1,493 纠正） | 7 条被接受 | ≈¥45 | **¥6.4 / 条** |
| 主动联系 | 115 | 680（453 主 + 95 对冲 + 132 纠正） | 28 条发出 | ≈¥9 | ¥0.32 / 条（**24 次调用换 1 条**） |
| 人生发展 | 211 | 318 | 38 次结算 | ≈¥4.2 | ¥0.11 / 次 |
| 聊天主链（pinned + expression） | 316 | 568 | 106 个 plan | ≈¥7.5 | ¥0.071 / 次回复（5.4 次换 1 次） |

**连最健康的聊天主链都是 5.4 次调用换一次回复。** 目标形状是每个产出 1.2 次调用。另外 39 次非 DeepSeek
调用（gpt-4.1-mini / gpt-4o-mini / qwen-plus，全部是人生节拍的审查）占调用数的 4.5%，却占成本的
**13.5%**——审查用更贵的模型，产出 0% 进入用户视野。

### 12.3 根因

**先说一个必须纠正的判断。** 早先我以为后台的钱花在"模型说 `no_change`、系统照样计费"。**这是错的。**
按 lane 拆 `ModelResultRecorded` 的审计状态后，真实情况是：

| lane | attempt 数 | 产出 validated | 成功率 | 主导失败码 |
| --- | ---: | ---: | ---: | --- |
| private-impression | 3,003 | 7 | **0.2%** | `main_invalid_output` 1,498 → `corrective_invalid` 1,493 |
| proactive | 195 | 35 | 17.9% | 四类 timeout 合计 238；`proactive_claim_binding_invalid` 56 |
| pinned-turn | 187 | 71 | 38.0% | `main_timeout` 63、`quick_timeout` 45 |
| life-development | 211 | 175 | 82.9% | — |
| other | 412 | 376 | 91.3% | — |

（attempt 数是 attempt 级审计记录数；private-impression 的 3,003 对应 1,505 个不同机会，因为主调用与
纠正调用各记一条。）

`no_change` 在代码里**是合法结论**（`private_impression_producer.py:205-209` 直接返回 None），所以
99.8% 的失败不是"她没话说"，**是她说了但形状不合格**。更糟的是连"没什么可补充的"这个答案本身也有
严苛形状要求——`:206-208` 规定 no-change 对象必须**有且仅有 `decision` 一个键**，多一个解释字段就非法。
**于是这条车道连"退出"都退不出去。**

而 `life-development` 和 `other` 有 83–91% 的成功率，说明**契约不是全面崩坏，是特定几条车道的契约设计
有缺陷**。这把根因从"运行时浪费"改写成了"设计缺陷"。

#### A 类：设计缺陷（不改设计，改代码没用）

0. **最根本的一条：把"连续存在"实现成了"连续计算"。** 用户要的是她像一个一直活着的人——这要求的是
   **状态连续**，不是**计算连续**。当前设计把每一个时刻都物化成一次模型调用：每 30 秒醒一次，
   对每个 appraisal 想一遍，对每个活动问一遍。但"越想越气"不需要 N 次模型调用去逐次加深，
   它只需要①起因原文留着、②强度是时间的纯函数、③她真要说话时**读一次**这份累积。
   **连续性应当懒求值：把它编译进她下次开口时看到的"现在"，而不是提前算好。**
   A1–A6 与 B7–B8 都是这条的具体表现形式。

1. **契约用"精确集合相等"验收，导致实际不可满足。** `private_impression_producer.py:246-284` 是
   约 14 个条件的合取，其中 `set(value) != expected_fields` 要求字段集合**不多不少**，而字段集合本身
   还依赖 `decision` 取值：`predecessor_refs` 只在 `consolidate`/`supersede` 时属于合法集合——
   也就是说 **`{"decision":"retain","predecessor_refs":[]}` 是非法的，必须整个键都不出现**。
   再叠加 `source_refs` 必须与 anchor appraisal 的 ref 有交集、`confidence` 必须是万分比整数、
   `expiry_condition` 必须落在闭枚举里。**全有或全无，没有字段级降级。**
   代价：1,505 个机会 → 2,991 次 provider 调用 → 7 条被接受。
2. **修复机制不修复，而且比原调用更贵。** `inbound_wire.py:1404-1419` 的 corrective 是
   `[*messages]`（原提示全量）+ 模型自己的无效输出 + 一句指令 + **tool contract 再塞一份**，
   所以单次成本 >1x 原调用。全库 5,442 条 ModelResult 里 **1,748 条是 corrective（32%）**，
   而恢复成功率：`main_invalid_recovered` 35 对 `recovery_failed` 1,626 ≈ **2%**。
   **用同一份契约、同一个模型、只多一句"你错了"去重试，是无效设计——它只是把失败的成本翻倍。**
3. **契约面积随机制数量增长，对每一次调用永久征税。** `expression_hard_boundaries` 单项约 6.5k 字符；
   `inbound_author.py:3184-3187` 把 appraisal 和 expression **两份完整契约整体串进同一个 system 段**。
   于是**每加一个机制，所有调用永久涨价**，与该机制是否被使用无关。
4. **统一快照让廉价车道付主路径的上下文价。** "8-facet Inner Life Snapshot 是唯一上下文"是好的深模块
   原则，但它意味着一次私人印象也要带 15.7k token。**上下文粒度没有随调用价值分级。**
5. **让模型自证来源闭包，所以 ref 清单必须进 prompt。** 认识论验证被放在模型侧（她要自己引用 ref），
   代价是每次都运一份可复制的 ref 全表。放到她说完之后做确定性核对，同样安全且免费。
6. **机会派生是 O(历史) 的。** 私人印象对**每个** active appraisal 派生机会，而 `appraisals` 在 head 里
   累积不清。待办随历史线性增长，30 秒调度器无限磨；8/4 单日 2,769 次就是"积压 × 快调度"。
   **这意味着关系越久日账单越高，与产出无关。**

> 第 3 条和第 6 条是两条**增长曲线**：成本随机制数量增长、随关系长度增长，两者都与用户感知无关。
> 只要这两条还在，任何一次性的省钱都会被时间抵消。

#### B 类：参数按目标值设定，从未按实测校准

7. **对冲阈值 2.0s vs 实测 p50 4.2s。** `interactive_turn_budget.py:54` `hedge_after_seconds=2.0`，
   而 `character_interior` 实测 p50 4,242ms、p90 6,155ms、p99 8,508ms。于是 backup 调用占 primary 的
   **21–37%**（pinned-turn 16/60、proactive 95/453、expression-episode 66/202、other 70/191）。
   超时也是最大失败码：pinned-turn 108 次 timeout、proactive 238 次。
   **这些常量来自"Fast Reply p50 1–2s"的设计目标，而这个目标从来没有达成过；而且超时不会让 provider
   停止计费——token 已经生成了。**
8. **一次语义产出被拆成多次调用。** 人生节拍 = draft + novel_origin_review + source_closure_review，
   两次审查还用更贵的模型（39 次非 DeepSeek 调用占成本 13.5%）且零可见产出。
   proactive 更极端：115 个 attempt 用掉 453 primary + 95 backup + 132 corrective = 680 次 provider 调用。

#### C 类：治理缺失（不产生成本，但让上面所有问题不可见）

9. **调用由时钟驱动，"值不值得做"在花完钱之后才知道。** 调度器每 30 秒弹一个后台单位，
   前置没有任何确定性判据。
10. **记账晚于花钱。** `world_v2_model_usage` 最早一行是 8/7，8/4 的风暴没有账单；
    而且 868 行里 760 行记成同一个 `world_v2_character_interior`。**成本不可归因 = 成本不可治理。**
11. **停机恢复把"错过的思考"当成待办。** 8/9–8/11 停机，8/12 重启当天 ¥6.68、只有 5 条用户消息。
    reflection 有过期跳过，proactive 只有失败 backoff、没有过期判定。
12. **缓存从来没被当成设计约束，而且有两个已定位的位置错误。** 92.5% 的钱付给未命中输入。前缀缓存按
   position 0 起的字节精确匹配，当前两段的开头都是本轮最易变的东西：
   - **system 段第一句就按 `recall_available` 分叉**（`inbound_author.py:3164-3178`）。该布尔量来自
     `_recall_available()`（`:2342-2354`），依赖 `world_revision` / `deliberation_revision` /
     `ledger_sequence` / `trigger_ref`，**每轮都可能翻转**。一翻转，整个 prompt 命中率归零。
   - **user 段 JSON 的第一个键是 `current_trigger_message`**（`inbound_wire.py:11735-11740`），
     即用户这一轮说的话——**保证每轮不同**；紧接着 `request` 里又是 revision/sequence 之类的逐轮量。
     稳定的 `expression_hard_boundaries` 和 `inner_life_snapshot` 反而排在它们后面。

   这正好解释实测的 23.1%：只有 system 段在分叉点之前的部分能命中（约 3.6k token），user 段从第 0 字节起
   全部未命中。**顺序完全是反的。**

### 12.4 措施（编号对应 §12.3 的根因）

A0 没有单独的措施行，因为它就是本文档整体的方案：§3 的 Occasion seam 决定"什么时候才值得算一次"，
§5 的 Present 决定"她开口时读到多少累积"，§6 把情绪强度改成时间纯函数。下表是其余各条的具体措施。

| # | 措施 | 治 | 预期效果 |
| --- | --- | :---: | --- |
| 1 | **契约改"必填最小 + 可选降级"**：形状校验不得用集合相等；未知键忽略，可选键缺失取默认；`decision=retain` 带空 `predecessor_refs` 必须合法 | A1 | 私人印象类车道首次合法率从 0.2% 回到 80%+ |
| 2 | **删除同契约重试**：非法输出直接记技术失败并丢弃该机会，等下一个机会。要保留修复就必须换更小的契约重问，且只问缺失字段 | A2 | 砍掉 1,748 次 corrective 调用（占全部 model 调用的 32%），每次成本还 >1x 原调用 |
| 3 | **契约面积上限 G4 + 双契约合一**：主输出降到 messages/felt/stuck_with_me/wants/photo；`expression_hard_boundaries` 不整表进 prompt | A3 | 切断"每加机制全体涨价"的曲线 |
| 4 | **上下文按调用价值分级**：后台廉价机会用裁剪版 Present（段 1–5 + 触发材料），不带完整对话史 | A4 | 后台单次输入从 15.7k 降到 ~6k |
| 5 | **来源闭包移到她之后**：她自由说，系统事后确定性核对 | A5 | 直接省掉 6.5k/次的 ref 清单 |
| 6 | **禁止 O(历史) 的机会生成（G7）**：机会只能从本步新接受的事件派生 | A6 | 让成本与关系长度脱钩；阻止 8/4 形状复现 |
| 7 | **超时与对冲按实测重标定**：`hedge_after_seconds` 取实测 p90（约 6.5s）而非 2.0s；所有延迟常量必须标注"依据哪次实测" | B7 | 去掉 21–37% 的对冲重复计费和大部分 timeout 重试 |
| 8 | **一次产出一次调用**：删除审查型模型调用，改确定性来源检查 | B8 | 移除全部高价非 DeepSeek 调用（占成本 13.5%） |
| 9 | **闭式前置判据（G8）**：调用前必须有确定性谓词判定"有无可能改变结果的新材料" | C9 | 空转机会在花钱前就被丢弃 |
| 10 | **记账前置 + purpose 归因（G3）** | C10 | 让上面每一条的效果可被证明 |
| 11 | **过期一律丢弃**：每个机会带 expiry，重启后不补做 | C11 | 消除重启当天的成本尖峰 |
| 12 | **缓存前缀硬门（G5）**：修 §5.3.1 两个已定位缺陷 | C12 | 稳态单轮 ¥0.0132 → ¥0.0016，且是唯一同时改善质量的措施 |

措施 1、2、7 是本次新增的发现，**它们合起来解释了后台绝大部分开销，而且都不需要改架构**——
分别是一处校验写法、一处重试策略、一个常量。措施 3、4、5、6 才是要动设计的部分。

**降成本的唯一合法手段是减少"不改变她的调用"，不是减少"她能看见的东西"。** 削上下文、削记忆、
让她失声、把技术故障记成沉默，都不算降本。

#### 加厚上下文为什么反而更便宜

把 prompt 改成"稳定在前、追加在后"之后，第 N+1 轮的前缀包含第 N 轮的全部内容——人设、契约、长期事实、
日记、以及**除最后一轮之外的整段对话史**。这些都走命中价（未命中价的 1/50）。于是：

- 加厚的段落只在**第一次**出现时付全价，之后一直吃缓存；
- 段 9 对话史越长，能命中的绝对 token 越多，**命中率随上下文加厚而上升，不是下降**；
- 真正每轮全价的只有末尾几百 token（本轮消息 + 此刻的机会）。

所以"加厚到 20k"的稳态边际成本是 "0.4k 全价 ¥0.00040 + 19.6k 命中价 ¥0.00040 + 400 输出 ¥0.00081"
= **¥0.0016/轮**，比现在 15.7k 的 ¥0.0132 低约 8 倍。注意此时**输出反而成了最大的一项（50%）**，
所以稳态之后再压成本要看输出长度，不是输入。

**这是本计划里唯一一个同时改善质量和成本的改动**；除 H1 记账（否则无法证明效果）外，它应排在最前。

### 12.5 改后的预算信封与预期实际

| 桶 | 月上限 | 预期实际 | 规则 |
| --- | ---: | ---: | --- |
| 可见对话 | ¥30 | ¥5–12 | 一次合法调用出话；上下文加厚优先于省钱 |
| 机会型内心（安静窗口 / 未平的气 / 每日开场） | ¥12 | ¥1–3 | 一个机会一次 consider；产出必须落成将来能读到的残留 |
| 人生节拍 | ¥8 | ¥0.5–2 | 单次作者调用 + 确定性检查；无审查模型 |
| 媒体 | ¥25 | ¥8–12 | 15–25 张；一次修复上限 |
| 记忆与召回 | ¥5 | ¥0 | 默认本地 lane，不开云端 embedding |
| 余量（故障、资格化、实验） | ¥20 | — | — |
| **合计** | **¥100** | **¥15–29** | — |

推导：目标调用量约 570 次/月（用户消息 12/天 × 1.2 + 安静窗口 ~2/天 + 未平的气 ~1/天 + 每日开场 1/天 +
生活节拍 ~0.5/天）。20k 输入 / 400 输出下，命中 60% 时 ¥0.0091/次 → ¥5.2/月；**即使缓存完全不命中**
也只有 ¥0.021/次 → ¥12/月。

**所以 H 阶段之后预算不再是约束，反而空出约 ¥60–80/月。** 这笔钱的合法用途按优先级是：继续加厚上下文、
用真实自由对聊 A/B 试更强的模型、增加照片数量。**不得用来恢复后台空转。**

一个必须说清的保留意见：缓存命中率的上限受流量限制。日均 12 条消息、时间分散，DeepSeek 的前缀缓存可能
在两次对话之间过期，所以 70% 是目标不是承诺。上表的"预期实际"已经按 0% 命中也能达标来取值，不依赖缓存
兑现。

### 12.6 降载阶梯

月末预测达到 ¥70 / ¥80 / ¥90 时逐级：停实验与资格化 → 停低价值渲染与低显著度机会 → 只保留用户消息机会。
任何级别都**不得**削减可见对话的上下文、不得让她失声、不得把预算压力伪装成她选择沉默。预测或实际超过
¥100 时继续保障可见聊天，告警并输出 purpose/provider 归因。

### 12.7 验证口径（每个包交付时必须跑并附结果）

```sql
-- 1. 按 purpose 的日成本与调用数（G3 生效后不应再有 unclassified 或单一 purpose 垄断）
SELECT substr(recorded_at,1,10) d, purpose, COUNT(*), ROUND(SUM(cost_cny),3)
FROM world_v2_model_usage GROUP BY d, purpose ORDER BY d;

-- 2. 每条用户消息背后的模型调用数（目标 ≤3）
SELECT substr(recorded_at,1,10) d, COUNT(*) calls
FROM world_v2_model_usage GROUP BY d;
SELECT substr(json_extract(event_json,'$.created_at'),1,10) d, COUNT(*) msgs
FROM world_v2_events
WHERE json_extract(event_json,'$.event_type')='ObservationRecorded' GROUP BY d;

-- 3. 缓存命中率（G5，目标 ≥50%）
SELECT purpose, SUM(prompt_tokens), SUM(cache_hit_tokens),
       ROUND(100.0*SUM(cache_hit_tokens)/NULLIF(SUM(prompt_tokens),0),1)
FROM world_v2_model_usage GROUP BY purpose;

-- 4. 账本空转（G1，cooldown/衰减/TriggerProcess 应大幅下降）
SELECT json_extract(event_json,'$.event_type') t, COUNT(*)
FROM world_v2_events GROUP BY t ORDER BY COUNT(*) DESC LIMIT 15;

-- 5. 无效成本率（≤10%）
SELECT status, COUNT(*), ROUND(SUM(cost_cny),3)
FROM world_v2_model_usage GROUP BY status;

-- 6. 按 lane 的首次合法率（H1b 后 private-impression 应 ≥80%）
WITH a AS (
  SELECT json_extract(json_extract(event_json,'$.payload_json'),'$.audit_json') aj
  FROM world_v2_events
  WHERE json_extract(event_json,'$.event_type')='ModelResultRecorded'
)
SELECT substr(json_extract(aj,'$.attempt_id'), 9, 20) lane,
       SUM(json_extract(aj,'$.status')='proposal_validated') ok, COUNT(*) n
FROM a WHERE json_extract(aj,'$.slot') IS NULL GROUP BY lane ORDER BY n DESC;

-- 7. 对冲与纠正的重复计费（H1b 后 backup/primary ≤5%，corrective 趋零）
WITH a AS (
  SELECT json_extract(json_extract(event_json,'$.payload_json'),'$.audit_json') aj
  FROM world_v2_events
  WHERE json_extract(event_json,'$.event_type')='ModelResultRecorded'
)
SELECT json_extract(aj,'$.slot') slot, COUNT(*) n
FROM a WHERE json_extract(aj,'$.slot') IS NOT NULL GROUP BY slot;
```

### 12.8 补遗：交接前最后一轮扫描的新发现

**A7. 最后一公里丢 13%——这是最贵的一种浪费。**

交付漏斗（三周全量）：`ExpressionBeatAuthorized` 138 → `ActionAuthorized` 138 → `ActionScheduled` 137 →
`ActionClaimed` 134 → `ActionDispatchStarted` 134 → `ActionProviderAccepted` 133 →
**`ActionDelivered` 120**。另有 `ActionUnknown` 14、`ActionCancelled` 4、`ActionReclaimed` 1。

也就是说 **13% 的话她说了、系统付了全款、语义价值也生成并落账了，但用户没收到**；其中 10%
停在 `unknown`——**系统连有没有送到都不知道**。这是成本与感知之间最直接的漏点，比任何后台空转都严重，
因为这部分钱本来是花在用户能看见的地方的。

还有连带损害：§7 的反重复依赖 `delivery_state`，而 14 条处在 `unknown` 的消息意味着**她自己也判断不了
上次那句到底有没有说出去**，这会直接制造重复或错误的沉默。

另外 `ExpressionPlanAccepted` 106 里只有 90 个 `ExpressionPlanCompleted`，`ExpressionPlanTerminated` 16
（15%）——计划被接受后中途终止的比例也偏高。

**A8. 模型动物园：至少 9 个 model_id 在跑，价格表只有 2 行。**

审计里出现的 `model_id`：`deepseek-v4-flash` 3,394、`deepseek-v4-flash->gpt-5.6-luna` 643、
`gpt-4.1-mini` 185、`source-review-authority:gpt-4.1-mini|qwen/qwen-plus` 126、`qwen/qwen-plus` 118、
`gpt-5.4-mini` 62、`openai/gpt-5.4-nano` 50、`openai/gpt-4o-mini` 13、`gpt-5.6-luna` 3。

而 `usage_metrics.py:50-53` 的 `MODEL_PRICES` 只有 flash 与 pro 两行，其余全部落到
`UNPRICED_MODEL_CONSERVATIVE_PRICE`（$1.20/M 输入、$2.40/M 输出）。

兜底设计本身是对的（新模型不会静默变免费），但后果是：**所有非 DeepSeek 的成本数字都是惩罚性估算，
不是真实账单**。我在 §12.2 写的"非 DeepSeek 占成本 13.5%"因此是上界而非实测。两个问题：真实成本不可知；
预算门可能因为虚高数字提前降载。措施：每个投产模型在价格表里必须有一行；
`source-review-authority:A|B` 这种复合 id 要拆成两次计费。

**A9. 审查是 100% 覆盖而且用双模型。** `config.py:557-574` 配了 secondary（`qwen/qwen-plus`）+
fallback（`gpt-4.1-mini`）两套审查模型，审计里能看到复合权威 id。每条人生节拍都要过一遍。

**A10. 模型在写世界，而不只是演角色。** 三周产出：`WorldOccurrenceSettled` 39、`NpcRegistered` 7、
`NpcStateChanged` **2**。而 `npc_ecology.py` 有 1,708 行，life_development 每条要 3 次模型调用。
**用模型做世界模拟是本项目性价比最低的一处投入。**

### 12.9 行业对照：应当采用的标准做法

用户已明确允许不拘泥当前设计。以下每条对应上文一个缺陷，并给出该问题在业内的成熟解法。

1. **形状用受约束解码保证，不要 validate-and-retry。** 现在的 propose → validate → reject → retry
   是 2023 年的做法。当前标准是 structured outputs / JSON Schema strict / grammar-constrained decoding：
   **形状违规在解码期就不可能发生**，代码里只保留语义校验。这一条同时消灭 A1 和 A2，
   1,748 次 corrective（32% 的调用）直接归零。注意 DeepSeek 的 JSON 模式严格性弱于 OpenAI strict，
   所以必须配合下一条。
2. **对模型输出遵循 Postel 定律：宽进严出。** 忽略未知键、缺失可选键取默认、只对必填字段报错。
   **永远不要用集合相等做形状校验**（A1 的直接死因）。
3. **对冲阈值按 p95 设，不按目标设**（Dean & Barroso, *The Tail at Scale*）。经验法则是 hedge 取 p95、
   额外负载约 5%；当前 2.0s 对 p50 4.2s，额外负载 21–37%。配套用 tied request：一方开始执行即取消另一方。
4. **投递用 outbox + 幂等键 + 对账器。** `unknown` 不能是终态，必须由对账循环去 provider 侧查证，
   收敛到 delivered 或 failed。这是 A7 的标准解法。
5. **成本 SLI 用「每个用户可感知产出的成本」，不是每次调用的成本。** health 现在看调用数和金额，
   应该看 ¥/条送达消息、¥/张送达照片、¥/条被她真正引用的记忆。这个口径会自动暴露 A7 和私人印象。
6. **每条车道独立配额（bulkhead）。** 一条车道最多花掉自己的月度桶，花完即停，不得挤占对话。
   这在设计层面杜绝 8/4 那种单日 2,769 次。
7. **记忆改成「写时低频压缩 + 读时三因子检索」**（Park et al., *Generative Agents*：
   score = recency × importance × relevance）。现在是每个 appraisal 都要模型想一遍（写时昂贵），
   而检索侧被裁到 1 条（读时贫瘠）——**方向反了**。标准做法是观察廉价累积、每天一次批量压缩成 reflection、
   检索时按三因子取 3–8 条。项目里 recency 打分已经有了
   （`ledger_context_resolver.py:596-602` 的七日线性窗），缺 importance 与批量压缩。
8. **世界用确定性模拟，模型只演角色。** 这是游戏 AI 的标准分工——The Sims、CK3 不用任何 LLM
   也能让玩家感到角色有完整人生。日程、NPC 例程、天气、随机事件从加权表里出，**0 模型**；
   模型只负责她怎么说、怎么感觉、要不要主动联系。三周 39 个 occurrence、2 次 NPC 状态变化的产出，
   用一张事件表加权重就能做到，而且**密度可以调高十倍仍然是 0 成本**。
9. **贵的钱花在可见处。** 现状是审查用 gpt-4.1-mini/qwen-plus、她说话用 flash——**这是反的**。
   正确顺序：她的声音用最好的模型；世界模拟不用模型；硬约束用确定性检查；LLM 审查只做 5–10% 抽样监控。
10. **连续性靠回指，不靠计算。** 让用户觉得"她真的活着"的是她三周后突然提起某件小事，
    这依赖检索质量和上下文厚度，不依赖后台想过多少次。**预算应当从"想"挪到"记得住、找得回、说得出"。**

> 如果实施者只来得及做三件事：**受约束解码（1+2）、对冲阈值改 p95（3）、投递对账（4）**。
> 这三件不改架构，合起来能去掉约 40% 的调用量和 13% 的可见产出损失。

### 12.10 一次成功（one-shot）：审查车道清单与删除边界

用户裁决 7 要求模型调用以"一次生成即可用"为默认形态。**这一节的唯一目的是让实施者不会删错东西。**

#### 判定规则

> **删的是"再问一次模型"，留的是"确定性地核对一次"。**

模型审查要删，因为它贵、慢，而且**审查模型自己也会产生幻觉**——用一个会错的东西去检查另一个会错的东西，
不增加保证。确定性检查要留，因为它免费、必然正确，而且是 `AGENTS.md` 规定的硬边界
（事实引用与事件权限、隐私与同意、安全与法律、外部 Action 授权、effect-once、CAS、回执、可重放性）。

一次成功不是"放弃校验"，而是把顺序换掉：

```
旧：给她一堆契约 → 她自证来源 → 模型审查她的自证 → 不过就重问模型
新：给她干净的材料 → 她一次说完 → 确定性核对她引用的事实 → 不过就记技术失败，等下一个机会
```

#### 实测的模型审查车道（全部删除）

只有三个模块真的在调模型做审查，合计 **3,139 行**：

| 模块 | 行数 | 模型调用点 | 处置 |
| --- | ---: | ---: | --- |
| `world_v2/structured_source_review_model.py` | 1,882 | 4 | 删除 |
| `world_v2/source_review_authority.py` | 989 | 15 | 删除（双模型权威 `A\|B` 就在这里） |
| `world_v2/visible_source_review_model.py` | 268 | 7 | 删除 |

连带删除：`world_v2/life_review_identity.py`（153 行，只为上面三个注册身份）；
配置 `config.py:557-574` 四项（`WORLD_V2_SOURCE_REVIEW_SECONDARY_MODEL` / `_FALLBACK_MODEL` /
`_RECOVERY_MODEL` / `_RECOVERY_FALLBACK_MODEL`，后两项自己的 description 已写明是
"Retired visible-chat recovery reviewer compatibility value"）；
purpose `life_development_source_closure_review`、`life_development_novel_origin_review`、
`visible_source_closure_proof_v1`。

**另外两条也属于"再问一次模型"，一并删：**

- **纠正/重选车道**：删的是 `character_interior/inbound_wire.py:1400-1500` 的 validation reselection
  **调用路径**——就是 1,748 次调用（占全部 32%）、成功率 2% 的那条。

  > **注意，不要连 `world_v2/structured_expression_reselection_model.py`（1,048 行）一起删。**
  > 名字里有 `model`，但它**不调用模型**，它是 provider 严格 schema 的构造器：内联 `$ref`、
  > 强制 strict object 的字段显式化（`:186`）、算 `provider_schema_sha256` 让 schema 序列化稳定
  > 从而对 provider 缓存友好（`:523`、`:805`）。**这正是 §12.9 第 1 条要用的受约束解码工具，
  > 是解决方案的一部分，不是问题的一部分。** 正确处置：删掉重选调用方，把这里的严格 schema 构造器
  > 复用到主调用上；同时解开它对 `structured_source_review_model` 的 import（`:24`）。
- **来源清单探针**：`config.py:583-589` 的 `WORLD_V2_SOURCE_INVENTORY_MODEL`（`openai/gpt-5.4-nano`）
  与 fallback（`openai/gpt-5.4-mini`），审计里 50 + 62 次。清单是系统自己就知道的东西，
  **不需要问模型**，改成确定性枚举。

#### 明确保留（这些不是模型审查，删了会破坏硬边界）

| 模块 | 行数 | 为什么留 |
| --- | ---: | --- |
| `world_v2/life_development_source_closure.py` | 1,751 | 确定性来源闭包计算，0 模型调用 |
| `world_v2/isolated_source_closure_trace.py` | 995 | 确定性闭包追踪，0 模型调用 |
| `world_v2/private_self_expression_audit.py` | 1,751 | 确定性隐私审计，0 模型调用 |
| `world_v2/proposal_audit.py` | 742 | ModelResult 审计记录，replay 依赖它 |
| `batch_invariants.validate_commit_batch` | — | 提交批次不变量，CAS 与 effect-once 的守卫 |

#### 怎么做到一次成功（删了审查之后靠什么）

1. **受约束解码**：形状违规在解码期就不可能发生（§12.9 第 1 条）。
2. **契约面积压到最小**（G4）：必填 ≤3、总字段 ≤8、深度 ≤2。字段越少，一次说对的概率越高。
3. **不让她自证来源**：把可引用的材料**作为内容**给她读（Present 第 3–9 段），而不是把 ref 清单
   作为**义务**要求她回填。她自然地说，系统事后确定性核对她提到的事实有没有来源。
   这同时省掉每次 6.5k 的 manifest。
4. **宽进严出**（Postel）：忽略未知键、可选键取默认。
5. **失败即丢弃**：一次不成就记技术失败、丢掉这个机会，等下一个。**绝不能用本地模板冒充她说话**
   （`AGENTS.md` 明令禁止），也不再重问模型。

#### 一处需要用户单独决定

**媒体的图片审查（`OpenAIMediaInspector` + ≤1 次修复）不在本节自动删除范围内。** 理由：文本一次说错
用户大多看不出来，但图片崩坏是极其可见的，而且图片单张成本远高于一次审查调用。
建议保留但改成**只做确定性可发布性检查 + 失败即放弃该张**，不做模型修复重生成。
**请用户明确批复后再动。**

## 13. 工作包与代码坐标

顺序执行，每个独立可回滚，diff 目标 ≤300 行。

| 包 | 内容 | 红测（先写） | 验收门 |
| --- | --- | --- | --- |
| H1 | 记账与归因：purpose 拆细、reservation 前置、health 暴露每条用户消息的调用数、cache hit 与无效成本率 | 未 reservation 的调用能发起；purpose 统一记成 interior | G3 绿；G2 告警可见；§12.7 五条查询全部能跑出分桶结果 |
| H1b | **三处低成本高收益修正**（§12.4 措施 1/2/7 + §12.9 第 1–3 条，不改架构）：① 形状优先用受约束解码保证，代码侧改必填最小 + 忽略未知键（Postel）；② 删除同契约重试，非法即记技术失败并丢弃机会；③ `hedge_after_seconds` 等延迟常量按实测 **p95** 重标定并标注依据 | `{"decision":"retain","predecessor_refs":[]}` 被判非法；corrective 用原契约重试；hedge 阈值 2.0s 对 p50 4.2s | 私人印象类首次合法率 ≥80%；corrective 调用数归零或 ≤5%；backup/primary ≤5% |
| H1c | **投递对账与价格表补全**（§12.8 A7/A8 + §12.9 第 4–5 条）：`unknown` 不再是终态，加 outbox 对账循环去 provider 侧查证收敛；每个投产模型在 `MODEL_PRICES` 补一行，复合 `source-review-authority:A\|B` 拆成两次计费；health 增加 ¥/条送达消息 | 14 条 `ActionUnknown` 永远停在未知；9 个 model_id 只有 2 个有价格行 | 送达率 ≥97%；`unknown` 终态归零；无模型落到 `UNPRICED_MODEL_CONSERVATIVE_PRICE` |
| H1d | **删除全部模型审查车道**（裁决 7 + §12.10）：删 `structured_source_review_model.py`、`source_review_authority.py`、`visible_source_review_model.py`、`life_review_identity.py`，删重选**调用路径**，删 `config.py` 六项审查/清单模型配置，来源清单改确定性枚举。**保留并复用** `structured_expression_reselection_model.py` 的严格 schema 构造器。**确定性闭包与隐私审计一行不动** | 存在 model-bearing 的 review purpose；来源清单要问模型 | 全库 0 个 review 类 model purpose；严格 schema 构造器接到主调用上；`life_development_source_closure.py` / `isolated_source_closure_trace.py` / `private_self_expression_audit.py` / `proposal_audit.py` 测试全绿 |
| H2 | 空转清零：生活改 due 驱动唤醒；claim/lease/retry 移到可清理 durable sidecar | cooldown 空转仍写 TriggerProcess 四件套 | G1 绿；cooldown 类事件归零 |
| H3 | Present 编译器：§5 十段、散文人设入 prompt、去硬截断、稳定→易变排序、修 §5.3.1 两个前缀缺陷 | 人设不在 prompt 里；对话史只有 4 条 utterance；system 段两轮字节不同；`current_trigger_message` 在 user 段开头 | G5 绿；cache hit ≥50%（目标 70%）；两轮公共前缀 ≥ 稳定段总长 |
| H4 | 契约瘦身：主输出降到 messages/felt/stuck_with_me/wants/photo；来源闭包移到她之后 | 必填字段超限；要求她自证来源 | G4 绿；首次合法率显著回升 |
| H5 | Occasion 队列：五种机会合并现有 producer；删除 worker 自行调模型的路径；机会只从新接受事件派生并带 expiry | worker 直接调模型；停机后补做过期机会；机会派生扫描 `appraisals` 全表 | G2、G6、G7、G8 绿；私人印象独立调用链删除；重启当天成本无尖峰 |
| H6 | 情绪连续性：`unsettled_feeling` 改读原始起因；阈值按强度衰减重定义 | 反思读反思；固定 2 次上限 | 一场争执 ≤4 次调用且能观察到升级 |
| H7 | 反重复根因：`delivery_state` 接回 Present 第 8 段；残留可记"已经提过" | 她看不到自己发过什么、有没有被回 | 同主题连发消失，且无表面去重规则 |
| H8 | 生活密度：日程骨架 0 模型、`day_open` 一次调用、删双审查、感知与 NPC 并入重节拍 | 每活动一次调用；双审查链存在 | 每天有可读日记；生活相关月成本 ≤¥8 |
| H9 | 记忆配额与口径：召回 3–8 条进 Present；按 §12.9 第 7 条改 recency×importance×relevance 打分（recency 已有，补 importance）；写时压缩改每天一次批量；定主记忆源；health 暴露最近写入时间 | 召回被裁到 1 条；写时每 appraisal 一次模型调用 | 召回稳定进 Present 第 6 段；写时模型调用降到每天 1 次 |
| H10 | 新纪元迁移 + 启动秒级 + 归档只读 + 衰减改纯函数 | 冷启动全量重放 | 启动 ≤10s；快照可重建；归档完整 |
| H11 | 媒体接通（依赖 H8） | 开关关闭、grant 未 provision | 月内真实发出 ≥10 张有来源的生活照片 |

### 13.1 代码坐标（行号以 2026-08-13 的 HEAD 为准，改动前必须先确认）

| 包 | 文件与位置 |
| --- | --- |
| H1 | `world_v2/model_usage_budget.py`；`usage_metrics.py`（价格表 flash/pro）；`app.py` health；调用点分布见 `world_v2_model_usage.purpose` |
| H1b | ① 形状校验 `world_v2/private_impression_producer.py:203-284`（`set(value) != expected_fields` 在 `:207` 与 `:256`；条件字段 `:246-254`；anchor 交集 `:261`；万分比 confidence `:281-283`；闭枚举 `:284`）。② 同契约重试 `character_interior/inbound_wire.py:1404-1419`（`corrective = [*messages]` + 无效输出 + 指令 + tool contract 重复）；槽位申领 `:1402`。③ 延迟常量 `world_v2/interactive_turn_budget.py:53-71`（`total_seconds=12.0`、**`hedge_after_seconds=2.0`**、`validation_recovery_seconds=46.0`、`validation_reselection_seconds=100.0`）；覆盖点 `production_turn_application.py:3584-3585`；`deliberation.py:1816-1817` 默认 6.0/2.5。实测对照：`world_v2_model_usage.latency_ms` p50 4,242 / p90 6,155 / p99 8,508 |
| H1d | 删除：`world_v2/structured_source_review_model.py`(1,882)、`world_v2/source_review_authority.py`(989)、`world_v2/visible_source_review_model.py`(268)、`world_v2/life_review_identity.py`(153)；重选调用路径 `character_interior/inbound_wire.py:1400-1500`；配置 `config.py:557-574`（四项审查模型）、`:583-589`（两项清单模型）；调用点 `life_development_runtime.py`、`character_interior/production.py`、`proactive_action.py`、`deliberation.py`。**保留并复用**：`world_v2/structured_expression_reselection_model.py`(1,048，严格 schema 构造器，`:186`/`:523`/`:805`；需解开 `:24` 对 review 模块的 import)。**一行不动**：`life_development_source_closure.py`(1,751)、`isolated_source_closure_trace.py`(995)、`private_self_expression_audit.py`(1,751)、`proposal_audit.py`(742)、`batch_invariants.py` |
| H1c | 交付状态机 `world_v2/reducers.py:14968-14980`（`ActionFailed`/`ActionUnknown`/`ActionCancelled`）；终态集合 `production_turn_application.py:427-433`、`:1280`、`:1576-1582`、`sqlite_ledger.py:3665`；回执入口 `production_turn_application.py:1224-1248` `settle`；宿主判定 `qq_c2c_host.py:1543-1550`；action pump `world_v2/action_pump.py`；价格表 `usage_metrics.py:22-53`（补行）与 `:56-80`（reserve 估算）；审查模型配置 `config.py:557-574` |
| H2 | 唤醒：`world_v2/qq_c2c_host.py:2355-2441`、`:1973-1991`；心跳 `config.py:185-188`；cadence `life_ecology_trigger_store.py:48-59`；cooldown `life_ecology_runtime.py:384-388,721-722` + `reducers.py:10426-10436`；TriggerProcess 四件套 `life_ecology_trigger_store.py:185-201,215-246,391-407`、`runtime.py:1277-1338`、`reducers.py:10861-10917`；批次校验 `batch_invariants.validate_commit_batch` |
| H3 | 截断 `character_interior/snapshot_compiler.py:670-734`；facet `:789-798`；`model_view()` `character_interior/contracts.py:727-757`；注入 `character_interior/inbound_turn.py:562-567`；Capsule 上限 `world_v2/model_facing_context.py:15-19`（`_CHAT_ITEM_LIMITS`）、`:209`、`:280-282`、默认 lane 上限 `:403`；Capsule 输入上限 `world_v2/context_capsule.py:79`（`MAX_INPUT_ITEMS_PER_SLICE=256`）；view 内 ref 上限 `snapshot_compiler.py:509`（64/视图）；slices 剥离 `character_interior/inbound_wire.py:11699-11727`；user payload `:11735-11751`；identity 段 `:11776-11816`；identity frame 构造 `world_v2/semantic_chat_composition.py:1072-1084`；未消费人设 `configs/character.yaml` + `character.py`；缓存字段 `llm.py:1333-1334,1527-1528` |
| H4 | 工具 schema `character_interior/inbound_tool_contract.py:863-874`（compact gate）、`:987-993`（full）；expression 字段 `world_v2/expression_draft.py:315-351`；hard boundary manifest `:1400-1489`；shape contract `:748-771`；appraisal 字段 `character_interior/inbound_appraisal_wire.py:205-224`、system `:517-521`；双契约拼接 `character_interior/inbound_author.py:3160-3204`、compact gate `:3278-3306`；corrective slot `:142-145`、prompt `:2462-2515` 与 `inbound_wire.py:1402-1507`（温度 0.0） |
| H5 | 调度循环 `qq_c2c_onebot_app.py:456-459`、预算 `:677-687`（`background_units_per_pass=1`）；`qq_c2c_host.py:2072-2470` `scheduler_once`；间隔 `config.py:180-183`（30s）；worker drain 注册 `world_v2/runtime.py:594-694`；装配 `character_interior/production.py:798-914`；私人印象 `private_impression_producer.py:513-548`（机会派生）、`:101-107`（4 次上限）、`:203-279`（draft 校验）、`:1034-1067`（`no_change` 不接受）；proactive `proactive_action.py:1461-1513`、`social_initiative.py:147-149`；silence `production_turn_application.py:783-904`。**注意：proactive 只有失败 backoff（`proactive_action.py:1477-1492`），没有 reflection 那样的过期跳过**，这是停机后仍会发出陈旧主动消息的原因，Occasion 队列必须给它补上过期判定 |
| H6 | `world_v2/reflection_scheduler.py:27-29,84-100,116`；消费点 `character_interior/world_stimulus.py:2490`；衰减 `world_v2/runtime.py:4401-4479,4561-4565`；reducer `reducers.py:13554-13564` |
| H7 | `world_v2/recent_dialogue.py:41-75`（`RecentDialogueItem` 带 `delivery_state`）；**丢弃点 `snapshot_compiler.py:688-690`**；出站事件 `MessagePayloadStored` + Action 回执 |
| H8 | lane 顺序 `world_v2/life_ecology_runtime.py:223-559`、`development_due` `:242-243`、各 lane `:289-559`；`opening_token` 校验 `reducers.py:12478-12495`；`activity_timing.py`；`configs/world_seed.yaml`；双审查 purpose `life_development_source_closure_review` / `life_development_novel_origin_review`；外部感知 `config.py:354-356` + `qq_c2c_host.py:2195-2215`；NPC `world_v2/npc_ecology.py`；日记聚合 `world_life_context.py:314-326` 与 `production_turn_application.py:2585-2671` |
| H9 | `config.py:408-411`；`world_v2/recall_embedding.py:36-42,829-835`；默认 index `production_turn_application.py:3392`；recall limit `inbound_wire.py:11657`；快照裁剪 `snapshot_compiler.py:670-673`；编译 `ledger_context_resolver.py:1373-1386`；写入 `interaction_fact_trigger_runtime.py:1733`、`fact_v2_acceptance_runtime.py`、`reducers.py:13977`；非 embedding 关联 `conversation_continuity.py:49-50` |
| H10 | `world_v2/sqlite_ledger.py:680-713`（启动序）、`:1973-2076`（冷校验）、`:6060-6207`（replay）、`:3121-3160`（bundle migration）、`:6045-6057`（rebuild）、`:2139-2243`（identity/idempotency）、`:2346-2347`（prefix partial）；ref 累积 `reducers.py:15407-15426`；启动门 `production_turn_application.py:3364`；bootstrap `:4453-4495`；清理入口 `ledger_maintenance.py:1454`；运行时依赖 `memory_retrieval.py:170` |
| H11 | 开关 `config.py:723`（`ALLOW_AUTO_IMAGE_GENERATION`）、`:731-732`（`WORLD_V2_MEDIA_PREVIEW_ENABLED`）；部署检查 `world_v2/qq_media_deployment.py:293-311`、auto delivery `:433-438`；选片 `media_selection_worker.py:75-79`；provisioning `scripts/provision_world_v2_media_authority.py`；链路 `event_ecology_media.py` → `media_selection_acceptance_runtime.py` → `event_media.MediaPlanner` → `image_generation.OpenAIImageGenerator` → `OpenAIMediaInspector` → `media_auto_delivery.py` |

## 14. 每个工作包的交付模板

每个包必须留下，缺一不算完成：

1. **红测**：先写一个能复现真实失败的测试，说明它对应哪条用户可观察的问题。
2. **最小实现**：只做让红测转绿所需的改动；列出删除了哪些旧路径。
3. **相关测试 + 全量测试**：`uv run python scripts/test_fast.py --tier character` 日常，提交前跑完整套件。
4. **静态检查**：ruff、`git diff --check`、架构门。
5. **生产证据**：真实 daemon + 真实 provider 的 trace 或账本查询；不能只给测试绿。
6. **成本与延迟**：改动前后的 token/call、cache hit、p50/p90、账本写入量。
7. **剩余缺口**：明确说还有什么没闭合，不得四舍五入成"完成"。
8. **精确 commit**。

## 15. 必须交回用户决定的事项

出现以下任一情况，停下来并留下可恢复 checkpoint 与至少两个备选方案：

- 需要删除或重写不可变历史（H10 之外的任何情况）。
- 需要新增 authority、改变角色语义归属、放宽事实/隐私/Action 边界。
- 需要新增第六种 Occasion 或新的 model-bearing purpose。
- G4 的字段上限需要放宽。
- 成本预测显著超出 ¥100/月信封。
- 连续两轮同类补丁没有改善。
- 准备做生产替换或真实 QQ 上线。
- **§12.9 第 8 条（世界改确定性模拟、模型只演角色）需要用户单独批准后才能动。** 它会把 `npc_ecology.py`
  与 life_development 的世界作者从模型改成加权事件表，是本文档里唯一一处改变"谁在写世界"的提案。
  它不违反 ADR-0010（宗旨约束的是**角色的行为决定权**，不是世界事实的生成方式），而且能让生活密度
  提高一个量级且成本为零；但它推翻了 npc_ecology 的现有设计，属于大改。**在用户批准前，H8 仍按
  §8 的"日程骨架 0 模型 + `day_open` 一次调用"执行，不要顺手把世界作者删掉。**

## 16. 明确不允许

- 用固定话术、模板、关键词或随机 `act/hold` 替她作语义决定。
- 用主题黑名单、相似度去重或次数配额压制"心心念念"。
- 为省钱削上下文、削记忆、让她失声，或把技术故障记成她选择沉默。
- 在 H 门未全绿前新增任何 model-bearing purpose。
- **新增任何"再问一次模型"的车道**：审查、自证、复核、重选、清单探针一律不允许（裁决 7）。
  需要更强保证时只能加确定性检查。
- **借"一次成功"之名删掉确定性检查**：§12.10 保留清单里的模块一行不能动。
- 把"测试绿"当作生产完成。

## 17. 执行记录

### 2026-08-13 H1 记账与归因

- **红测**：`tests/world_v2/test_model_usage_budget.py` — 未 reservation / `unclassified` / `world_v2_character_interior` 的调用能打到 provider；purpose 无法分桶；health 没有每条用户消息调用数、cache hit、无效成本率。
- **改动**：`WorldV2UsageStore.admit_provider_call` 在 provider I/O 前原子写入 purpose/actor/provider/estimated CNY；`DeepSeekChatModel` 在 `usage_observer` 绑到该 store 时强制准入；`structured_role` 改记 `request.purpose`；`inbound_author` 主调用记 `inbound_turn`、召回后续记 `recall_followup`；`/health` 与 QQ `usage_budget_health` 暴露 G2/G5/G8 告警字段。
- **测试**：`uv run python scripts/test_fast.py --tier character` 672 passed；`tests/world_v2/test_model_usage_budget.py` + health 相关 244 passed；ruff 绿。
- **生产证据**：
  - 改动前基线（`data/companion.sqlite` 只读）：8/12 的 448 次调用里 372 次是 `world_v2_character_interior`、41 次 `unclassified`，对 5 条 `ObservationRecorded`（89.6 次/消息）；cache hit 23.1%。8/13 仍是 14 次 `world_v2_character_interior` + 3 次 `unclassified`，当日 cache 23.2%。G3 生效后新行不应再出现这两个 generic purpose。
  - 真实 provider 探针（`.env` 的 `DEEPSEEK_API_KEY` → `api.deepseek.com`）：无 scope 的调用抛 `ModelUsageAdmissionError` 且 HTTP=0；`inbound_turn` 先写入 reservation（actor=`agent:companion`，estimated ¥0.0006）再发出 1 次 HTTP。当前密钥被 DeepSeek 返回 401，usage 记 `inbound_turn/failed/cost=0/latency=207ms`，reservation 已 settled。G3 在真实 HTTP 边界上成立；succeeded 账单要等有效密钥或生产 QQ 部署后的新行。
- **成本与延迟**：本包不改 prompt，不减调用次数。准入是一次 SQLite INSERT；本次 live 探针在 401 前 207ms，其中含准入。分桶与告警从本包部署后的新行开始可证。
- **剩余缺口**：HTTP capture 仍未挂 `usage_observer`，该路径 G3 不生效（生产在 QQ）；第二次 `consider()` 仍未拒绝（G2 拒绝是 H5）；无效成本率按 purpose 标记（reselection/corrective/recovery/retry）与 `attempt>1`，尚未对接 ModelResult 审计状态；现网 cache hit 23.1%，health 会立即 `cache_hit_rate` 告警，这是 G5 的可见性，修复在 H3。`.env` 里的 DeepSeek 密钥当前 401，不能当作生产 daemon 已部署。
- **commit**：`966b0659`

### 2026-08-13 H1b 形状宽进、禁止同契约重试、对冲改 p95

- **红测**：`{"decision":"retain","predecessor_refs":[]}` 及带未知键的 `no_change` 被集合相等判非法；`complete_bounded_validation_reselection` 仍用原契约打 provider；默认 `hedge_after_seconds=2.0` 低于实测 p50 4.2s。
- **改动**：私人印象 `_materialize_draft` 改为 Postel（忽略未知键，retain 允许空 `predecessor_refs`）；同契约重选调用直接 `SameContractRetryForbidden`，非法输出记技术失败、机会丢弃；`hedge_after_seconds=6.5`（依据 2026-08-13 character_interior succeeded n=695：p50 4242 / p90 6155 / p99 8508ms，取 p95）。
- **测试**：私人印象 / hedge / inbound_tool_contract 红测转绿；`test_character_interior_inbound_author.py` 107 passed（原依赖二次纠正的用例改为一次调用 + `ValidationTechnicalFailure`）；ruff 绿。
- **生产证据**：本包不改 prompt。对冲从 2.0s 提到 6.5s 后，backup 不应再在 p50 前开火；corrective purpose 新行应趋零。部署前数字仍是改动前基线。
- **成本与延迟**：去掉 21–37% 的对冲重复计费和 32% 的 corrective 调用是目标；需部署后 §12.7 查询 6/7 验证。首 Beat 取消窗口随 hedge 后移，成功路径不再被 2s backup 抢跑。
- **剩余缺口**：expression 侧常见松散形状（字符串数组 beats）不再靠重选救回，首次合法率要等 H4 契约瘦身/宽进；审查模型车道仍在（H1d）；价格表与 ActionUnknown 对账是 H1c；G2 第二次 consider 拒绝是 H5。
- **commit**：`8d0917ed`

### 2026-08-13 H1c 投递对账与价格表补全

- **红测**：`ActionUnknown` 之后同幂等键的 `delivered` 回执被当成终态冲突丢掉；9 个投产 model_id 只有 flash/pro 有价格行，复合 `source-review-authority:A|B` 整行落到惩罚价；health 没有 ¥/条送达消息。
- **改动**：`unknown → delivered/failed` 合法；ActionPump 在未知态只做 `verify_delivery`/`get_msg` 对账、不重发；已结算预算用 `BudgetAdjusted` 补差；`MODEL_PRICES` 补齐审计里的 OpenAI/Qwen 行，复合权威拆两次计费，`A->B` 对冲按更贵一侧计价；health 增加 `cny_per_delivered_message`。
- **测试**：`test_usage_metrics.py` 生产 model_id 不再落到 UNPRICED；lifecycle/pump 对账转绿；资格测试改为晚到回执收敛到 delivered；character-tier 672 passed；ruff 绿。
- **生产证据**：本包不改 prompt。14 条历史 `ActionUnknown` 需部署后由 scheduler drain 查 `get_msg` 才能收敛；新账单行不应再出现 UNPRICED version。
- **成本与延迟**：对账是只读 `get_msg`，不增加模型调用。价格表让非 DeepSeek 行从惩罚估算改成真价，预算门不再被虚高提前打满。
- **剩余缺口**：审查双模型车道仍在（H1d 删除）；未知态的 expression plan 仍按当时的 unknown 终止，不回写 completed；¥/张照片与 ¥/条被引用记忆未做。
- **commit**：`0784738e`

### 2026-08-13 H1d 删除模型审查车道（裁决 7 / §12.10）

- **红测**：`tests/world_v2/test_one_shot_review_lanes_removed.py` 要求生产 haystack 不再出现 review purpose 子串、三个 LLM 审查模块 `ModuleNotFoundError`、`semantic_chat_composition` 不构造 `SourceReviewAuthority(`、`life_review_identity.py` 仍在。
- **改动**：删除 `structured_source_review_model.py` / `source_review_authority.py` / `visible_source_review_model.py`。可见聊天与主动联系不再安装第二模型审查；来源清单改已知 capsule refs 的确定性枚举。Life Development 停掉 `complete_json_object` 的审查 purpose，改走 `life_development_deterministic_closure.py`（冻结解析器只用于历史 ModelResult replay）。`structured_expression_reselection_model.py` 解开对审查模块的继承，只留严格 schema 构造器。`config.py` 六项审查/清单模型 Field 删除，不再构造客户端。用户覆盖：`life_review_identity.py` **保留**（`batch_invariants` / `life_development_source_closure` 依赖）。OpenAIMediaInspector 未动。
- **测试**：one-shot 4 passed；character-tier 498 passed；`test_life_development_runtime.py` + `test_qq_c2c_host_migration.py` + one-shot 共 178 passed（host 资格改为 fail-closed / `one_shot.model_review_lanes_removed`，不再断言独立 reviewer ready）；冻结模块测试 `test_isolated_source_closure_trace.py` / `test_private_self_expression_audit.py` / `test_proposal_audit.py` 全绿（生产模块一行未改）；ruff 绿。
- **生产证据**：本包不改 prompt。部署后 `world_v2_model_usage` 不应再出现 `life_development_source_closure_review` / `life_development_novel_origin_review` / `visible_source_closure_proof_v1` / `candidate_external_proposition_inventory` 新行；历史账本仍可 replay。
- **成本与延迟**：每个可见回合与每次 life beat 少 1–2 次审查/清单模型调用；确定性核对在角色之后、不另开 provider。
- **剩余缺口**：inbound_wire 里仍有无生产调用方的旧 inventory/coverage 辅助函数（H4 契约瘦身时可清）；isolated daemon / host 资格测试若仍断言旧 reviewer health，需随宿主包跟；H4 可见文本来源闭包尚未上；OpenAIMediaInspector 待用户批准。
- **commit**：`e694bfdb`

### 2026-08-13 H2 空转清零：due 唤醒与 sidecar claim

- **红测**：`tests/world_v2/test_life_ecology_empty_loop_g1.py` — cooldown/idle 仍写 TriggerProcess 四件套；QQ 调度在生活未到期时仍 heartbeat `ClockAdvanced`。
- **改动**：claim/lease/retry 进入可清理 sidecar（同库 SQLite 表 / 内存共享）。silent 结局（idle/cooldown/author_idle/author_no_opening/life_development_no_op）不再追加账本 TriggerProcess 或 RandomDraw；due 时刻写 overlay。语义结局仍一次提交 Opened+Claimed+Completed。QQ 调度把 `life_ecology_next_due` 当成 exact due，取消无事心跳。活动提案绑定改为 trigger 身份（sidecar 时代投影里可以没有 process）。`batch_invariants` 未改。
- **测试**：G1 红测 5 passed；trigger store / life ecology / activity / life-development production / QQ host migration 相关 112 passed；ruff 绿。
- **生产证据**：本包不改 prompt。部署后 cooldown 空转不应再写 TriggerProcess 四件套；无 due 的 scheduler pass 不应再写 `ClockAdvanced`。
- **成本与延迟**：去掉生活空转的账本写入与无事心跳；不增加模型调用。
- **剩余缺口**：技术失败 backoff 仍走账本 TriggerProcess；Affect 衰减仍靠 logical time，idle 时钟变少后衰减会拖到下一次 due/inbound（H10 改纯函数）；H5 Occasion 队列尚未上。
- **commit**：`193d07d5`

### 2026-08-13 H3 Present 编译器：稳定前缀、散文人设、加厚现在

- **红测**：`tests/world_v2/test_present_prefix_and_identity.py` — system 段随 `recall_available` 分叉；user JSON 以 `current_trigger_message` 开头；`character.yaml` 散文人设不进 prompt；对话史被快照裁到 4 条。
- **改动**：`present_prompt.py` 固定 system 召回措辞并把「这轮能不能召回」放到 user 段末尾的 occasion。`character.yaml` 的 `base_prompt`/外貌/背景/日常/文风样例进入 identity 散文（不计入 `identity-frame:sha256`）。user payload 按稳定→易变排序，`current_trigger_message` 最后。对话/记忆/事实/印象配额抬到 Present 预算（对话 80、记忆 8）；`delivery_state` 回到对话材料（H7 根因）。Capsule `hard_max_characters` 100k。工具 schema 的 `recall` 枚举仍按本轮可用性分叉（未改 tools JSON）。
- **测试**：Present 红测 6 passed；character-tier 498 passed；model-facing / capsule / continuity / production / recall 相关 235 passed；ruff 绿。
- **生产证据**：部署后同一会话连续两轮的 system 段应字节相同；user 段公共前缀应覆盖人设/契约/稳定 materials；对话史应按最旧→最新追加。cache hit 目标 ≥50%，需真实流量。
- **成本与延迟**：加厚输入、稳态后靠前缀缓存降未命中价；不增加模型调用次数。
- **剩余缺口**：工具 schema 仍按 `recall_allowed` 分叉（可能打穿 tools 前缀）；`expression_hard_boundaries` 仍整表进 prompt（H4）；H9 打分与每日压缩尚未改；H5 Occasion 队列尚未上。
- **commit**：`058ac343`

### 2026-08-13 H4 契约瘦身：Present 边界 stub、G4、宽进 beats

- **红测**：`tests/world_v2/test_contract_area_g4.py` — compact gate 必填/总字段/深度超 G4；user 段仍塞 6.5k `expression-hard-boundaries.8` 机制论文；字符串 `beats` 与 slim `messages/felt/stuck_with_me/wants/photo` 首次非法。
- **改动**：prompt 里的 hard boundaries 改成 `expression-hard-boundaries.present.1`（只留可复制 tokens，`authority=checked_after_expression`）；完整 manifest 仍给表达后确定性校验。compact gate 外层 schema 已满足必填 ≤3 / 总字段 ≤8 / 深度 ≤2。Postel 接受字符串 beats、`messages` 字符串数组，以及五字段 slim 对象并编译成双草稿/事件信封；旧 dual envelope 仍合法。不要求她在输出里自证 `world_claims`。§12.10 冻结模块未改。
- **测试**：G4/slim 红测绿；character-tier 498 passed；ruff 绿。
- **生产证据**：本包改 prompt 面积。部署后 user 段不应再出现 `single_report_epistemic_scope` 等机制论文；来源闭包失败应仍走表达后确定性检查。首次合法率需真实流量。
- **成本与延迟**：每次调用少约 6.5k 机制字符（未命中输入）；不增加模型调用。slim 形状降低结构失败，目标是少掉 H1b 之后的技术失败丢弃。
- **剩余缺口**：工具 schema 仍按 `recall_allowed` 分叉；appraisal/expression 双契约拼接仍在 system 段（compact gate 外层已瘦）；G2 第二次 `consider()` 拒绝与 Occasion 过期是 H5；H8 不停 world-author；H9 打分与每日压缩尚未改。
- **commit**：`a6f16595`

### 2026-08-13 H5 Occasion 队列：G2/G7、过期丢弃、禁止历史表扫描

- **红测**：`tests/world_v2/test_occasion_queue_g2_g7.py` — 同一 Occasion 可二次 `consider()`；quiet_gap 过期后仍用旧观察补做 ambient；私人印象 opener 遍历全部 `appraisals` 派生机会。
- **改动**：新增 `occasion.py`（五种 kind + consider-once gate）。inbound `consider()` 在成功后标记 Occasion，同一 `opportunity_ref` 的第二次 live 调用拒绝。私人印象只从账本 head 的 `AppraisalAccepted` 派生（G7），不再扫历史表。`spontaneous_expiry` 到期的 quiet_gap 直接丢弃，不再把过期观察改写成 `ambient_presence` 补做。私人印象独立模型链仍可在 head 新 appraisal 上跑一次；历史积压不再开。§12.10 冻结模块未改；npc/life world-author 未删。
- **测试**：Occasion 红测绿；social / private-impression / proactive 相关转绿；character-tier 498 passed；ruff 绿。
- **生产证据**：部署后停机超过 `spontaneous_expiry_seconds` 不应再发出对着旧消息的主动联系；私人印象不应再对历史 appraisal 积压逐条调模型。
- **成本与延迟**：去掉过期补做与印象积压是后台成本的主项；不新增 model-bearing purpose。
- **剩余缺口**：五种 Occasion 尚未完全合并进单一队列对象（worker drain 仍按旧顺序跑）；G2 目前只拦 `inbound_turn`（quiet_gap 的 cadence epoch 仍走独立 trigger）；私人印象独立 faculty 未删除，只是不再扫表；H6 原始伤与次数上限、H8 日程骨架、H9 打分尚未做。
- **commit**：`04940f64`

### 2026-08-13 H6 情绪连续性：原始伤与增长间隔

- **红测**：`tests/world_v2/test_unsettled_feeling_original_wound.py` — 同一原始 `AppraisalAccepted` 在固定 2 次上限后不能再想；反思产物会作为新伤再开一轮；间隔未到仍立刻重开。
- **改动**：删掉 `MAX_REFLECTIONS_PER_APPRAISAL=2`。`unsettled_feeling` 仍绑定原始 `AppraisalAccepted`（`trigger_ref=reflection:{source}`）。强度用当前 `confidence_bp`；她标成非 active 即结束。再访间隔 1h→3h→12h→24h。由反思 lane 产出的 appraisal（`trigger_id` 以 `reflection:` 开头）不再开新伤。消费点仍读原始事件，不读反思链。Affect 衰减未改（H10）。
- **测试**：H6 红测绿；既有 reflection scheduler / source-bound stimulus / terminal recovery 绿；ruff 绿。
- **生产证据**：本包不改 prompt。部署后同一原始伤可在强度仍高时按增长间隔再想，不应再出现反思产出自我放大的农场。
- **成本与延迟**：一场争执大约多 2–4 次调用（≈¥0.05）是允许开销；用间隔而不是固定次数封顶。
- **剩余缺口**：首次开伤仍会看 `appraisals` 投影（G7 未把 first-visit 收成 head/due overlay）；五种 Occasion 仍未合成单一队列；衰减纯函数是 H10。
- **commit**：`7a3a98f4`

### 2026-08-13 H8 生活密度：日程骨架与每天一次 day_open

- **红测**：`tests/world_v2/test_life_density_h8.py`、`test_activity_lifecycle_runtime.py` — `world_seed.yaml` 的 `daily_schedule` 无消费者；同一当地日第二次活动 start 仍调模型。
- **改动**：新增 `day_skeleton.py`，把课表/作息/当日主题编成可读 `day_sheet` 进 Present（0 模型，不是 activity）。`day_open` 用 sidecar 记当地日；当天第一次活动 `consider()` 之后，同日再醒只对唯一 `complete` 走 timing 闭包，其余 no_op 不再问模型。npc/life world-author 未删；双审查已在 H1d 删除。
- **测试**：H8 红测绿；activity lifecycle 相关绿；character-tier 498 passed；ruff 绿。
- **生产证据**：本包把作息编进 Present。部署后她应能读到当天窗口；同日本地第二次活动 start 不应再打 provider。
- **成本与延迟**：活动车道从「每醒一次模型」降到「每天一次 day_open + 窗口结束的确定性 complete」。
- **剩余缺口**：0–3 条意图没有新事件落账，只是把当天第一次活动选择当作 day_open；天气/店铺开闭不在 seed 里所以没进 sheet；感知未并入 Present 候选池（默认仍 off）；NPC 作者未改（§12.9 第 8 条待批）；日记聚合仍靠 world_life recency，day_sheet 是新增的当日可读层。
- **commit**：`023e713d`

### 2026-08-13 H9 记忆配额与读时三因子打分

- **红测**：`tests/world_v2/test_memory_quota_h9.py` — 召回侧没有 recency×importance×relevance 乘积；health 没有最近记忆写入时间；主记忆源口径未写明。
- **改动**：`memory_read_score_bp` 按三因子乘积给 `active_memory_candidates` 与 `relevant_facts` 排序（relevance 暂固定 10000，query 文本未穿到 resolver）。Present 记忆配额维持 3–8（H3 已抬到 8）。health 增加 `last_memory_write_at` 与 `chat_recall_authority=FactCommittedV2`（MemoryCandidate 仍是检索控制，不是聊天事实权威）。embedding 默认仍关。
- **测试**：H9 红测绿；ledger context 选择顺序随乘积更新；character-tier 498 passed；ruff 绿。
- **生产证据**：本包改检索排序与 health。部署后 Present 第 6 段应按强度×新近排序取 3–8 条；health 能看到最近一次 Fact/Memory 写入。
- **成本与延迟**：读时打分是纯函数，不增加模型调用。
- **剩余缺口**：写时仍是每个 fact 一次模型压缩，尚未改成每天一次批量（避免在没有 due overlay 时把后到的 fact 记成 no_change 永远丢掉）；relevance 还没有当前 trigger 的词法项。
- **commit**：


