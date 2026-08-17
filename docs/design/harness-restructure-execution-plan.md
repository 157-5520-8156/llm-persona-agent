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
4. 人生节拍的双模型审查**由"冻结"升级为"删除"**（见裁决 7 与 §12.10）；P3 私密媒体车道当时保持冻结，
   **已被 2026-08-15 裁决 12 覆盖为接通**。
   **外部感知与 NPC 不冻结**，按 §8 改成由主路径承载。
5. 主动联系次数**由角色自己根据内心与心情决定**，不做表面去重或次数配额（要允许正常的"心心念念"），
   但必须从根因上消除"总在想同一件事"。
6. 记忆召回先保证**不依赖 embedding** 可用；embedding 只作为后接的加分项。
7. **一次成功（one-shot）是模型调用的默认形态。** 目标是一次生成即可用：省成本、省时间，并且**减少幻觉**
   （多轮审查会引入审查模型自己的错误）。现存的模型审查车道基本是当年技术做不到 structured output
   时留下的历史包袱，应当删除而不是优化。判定边界见 §12.10——**删的是"再问一次模型"，
   留的是"确定性地核对一次"**。

### 2.1 用户裁决（2026-08-15，成本收缩，不得由实现者改写）

8. **连续存在 ≠ 连续计算。** 情绪连续靠账本，不是心跳喊模型。分层仍是 §3：世界步进 0 次、机会 0 次、她每个机会恰好 1 次、后果 0 次。
9. **本机小模型不得判「是否联系」。** 主模型本轮不换。
10. **没有新材料就不 `consider()`。** 私人印象独立车道保持停用；同契约非法即丢，不再用原契约重问。
11. **2026-08-15 用户关掉非紧急后台错峰。** 安静窗口、再访、生活节拍不因 DeepSeek 高峰价窗口推迟。
12. **2026-08-15：接通 P3 成人媒体车道。** 覆盖裁决 4 的冻结。普通生活照仍走 OpenAI GPT Image；成人只走 Civitai Krea2，禁止静默降级。缺 `CIVITAI_API_KEY` 或模板则该车道 fail-closed，不得关掉普通车道。关系阶段仍不得授权亲密强度；Adult Eligibility / 内容级双方同意仍是资格缺口，本裁决接通的是已过现有资格门的渲染链。
13. **2026-08-15：QQ 附件识图用阿里百炼 `qwen3-vl-flash`，思考关闭。** 像素不进 DeepSeek。缺 `QWEN_API_KEY` 或 perception grant 则整条感知车道 fail-closed，不得静默回到 OpenAI vision。
14. **2026-08-15：等待时长由角色当轮声明秒数。** 不再用 soon=15 分钟 / later≈12 小时冒充她的耐心。系统只守硬下限 30 秒。没填 wait 不定时叫醒，允许她结束话题。填了短 wait 则按声明密度叫醒，急了可以连着发。没回话后的叫醒按她声明的 wait（`not_before`）；`expires_at` 只结束盼头窗口。到期后再给她一次 `consider()`，问、换话题或继续沉默仍由她选。

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
| H12 | 补上已付费但没送到的连续性（0 模型）：修 `epoch_continuity` 经历日期过滤、逐出权重、`delivery_state=unknown`、删死路 slice | genesis `experiences=0`；期待 advisory 最先被逐出；unknown 的气泡她看不见 | 快照 experiences 非空；超限时期待仍在；调用数与账单不变 |
| H13 | 期待与沉默的语义（≈0 新增调用）：slim 加可选 `waiting_for`、沉默带上期待、回话时评估一次、实现只在注释里的抑制 | `response_expectation` 0/106；`ResponseExpectationAssessed` 全库 1 条；pending 期间双车道同时开火 | 期待非空的计划出现；评估事件出现；每条用户消息调用数不上升 |
| H14 | 残留改副产品，删私人印象独立车道（**负成本**）：`stuck_with_me`/`wants` 直接编成印象与开环 | 3,034 次 attempt / 7 条接受的独立车道仍在 | 该 purpose 调用归零；`PrivateImpressionAccepted` 显著上升 |
| H15 | 生活开门：节奏层保持 0 模型，内容由她在已付费调用里产出，补 `life_arc_effect` 生产者 | 目录是 `legacy_replay_and_fixture`；`life_arc_effect` 0 条；generative LD 恒 `no_op` | 出现非目录来源的生活细节且下轮可读；出现 ≥1 条已结算结果开出的 Life Arc |
| H16 | Occasion 队列做实（承重墙，H13e/H15 的前置）：五种 kind 都成为真的 `OccasionIdentity`，带 expiry | 只有 `user_message` 是真的；`life_beat` 只有枚举；gate 是进程内 set | G2/G7 在五种上全部成立 |
| H17 | 成本收缩（§2.1）：initial 工具 schema 召回分支不再按本轮可用性分叉；私人印象同契约一次即丢。错峰已按 2026-08-15 用户裁决关掉 | tools JSON 随 `recall_allowed` 变；印象车道 4 次同契约重试 | 两轮 tools 前缀相同；非法印象一次丢弃 |
| H18 | 分享余波当场追问（0 新增 Occasion）：wait 由她声明；`still_pending` 同轮结案；入站 advisory 只给盼头 vs 是否已回话，不指令追问 | wait 系统中位 12h；`still_pending` 继续挡 quiet_gap；敷衍后只能等到期 | slim 可选 `wait`；still_pending 后可 mint quiet_gap；入站可 now 追问且不强制追 |
| H19 | 接通 P3 成人媒体车道：部署安装 Civitai Krea2 + 第一人称私密 prompt 作者；缺凭证 fail-closed，不降级 OpenAI，不关普通车道 | 部署未安装专用生成器/作者；高档计划 `specialized_*_unavailable` | 凭证齐全时 `adult_suggestive`/`adult_explicit` 与 author 装上；缺 key/模板时普通车道仍开；无来源高档计划仍 `unsourced_event` |
| H20 | QQ 附件识图改阿里百炼 `qwen3-vl-flash`（思考关闭）：工厂要 `QWEN_API_KEY`，不再要 OpenAI 才能开感知 | 感知工厂绑 `OPENAI_API_KEY` + `gpt-4o-mini` | 工厂 `dashscope:vision` + `qwen3-vl-flash`；请求带 `enable_thinking: false`；缺 Qwen key fail-closed |
| H21 | 等待时长改角色声明秒数（0 新增 Occasion）：slim `wait` 编成秒；没填不定时叫醒；H13e 到 `not_before` 叫醒；expiry 只结盼头 | soon=15min / 未声明=12h；叫醒看 `expires_at` | 她写 45 就是 45 秒；没填 wait 不编译盼头；wait 已过、expiry 未到也会叫醒 |

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
| H12–H16 | 坐标全部写在 [`interior-continuity-implementation-spec.md`](./interior-continuity-implementation-spec.md) 各包内（含核实日期 2026-08-14）。主要落点：`world_v2/epoch_continuity.py:120-128,164-168`；`world_v2/context_capsule.py:170-190,2119-2134,2174-2187`；`world_v2/recent_dialogue.py:60,372-374`；`character_interior/snapshot_compiler.py:717-719,730-748`；`world_v2/present_prompt.py:234-244,286-349,352-412`（`:385/402` 硬写 `None`）；`world_v2/expression_draft.py:264-277`；`world_v2/response_expectation_view.py:71-183,240-290`；`world_v2/runtime.py:802-878`；`world_v2/social_initiative.py:1102-1216`（`:1168-1170` 空注释）；`world_v2/silence_appraisal_trigger.py:54-117`；`world_v2/private_impression_producer.py:203-284,513-548`；`world_v2/biographical_lifecycle_runtime.py:190-233`；`world_v2/world_life_context.py:266-308`；`world_v2/occasion.py:15-28,50-68,79-80` |
| H18 | `present_prompt.py` slim `wait`/`waiting_for`；`response_expectation_view.py` `_TERMINAL_ASSESSMENT_STATES` 含 `still_pending`、advisory `counterpart_replied`；`pinned_turn.py` 入站 advisory；`inbound_wire.py` 同轮追问说明 |
| H19 | `qq_media_deployment.py` `_compose_high_private_lane`；`config.py` `CIVITAI_KREA2_ENABLED` 默认 True；普通仍 `OpenAIImageGenerator` |
| H20 | `config.py` `VISION_MODEL` 默认 `qwen3-vl-flash`、`QWEN_BASE_URL`；`qq_perception_deployment.py` 凭证改 `QWEN_API_KEY`；`perception_vision_transport.py` `dashscope:vision` + `enable_thinking: false` |
| H21 | `present_prompt.py` slim `wait` 解析秒；`response_expectation_view.py` 叫醒看 `not_before`；`social_initiative.py` `scheduled_for` 用 wait |
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
- **§12.9 第 8 条已于 `b060d961` 落地。** 2026-08-14 用户要求把 NPC actor+world 模型路径与
  life-development 世界作者**两边都撤回来**，但**先记下来、先别改代码**。详见 §17 同日暂存条。
  在用户再说一次动手之前，禁止 revert `b060d961`、禁止把加权表改厚当替代方案。

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
- **commit**：`1282f6db`

### 2026-08-13 H10 新纪元：归档、genesis、衰减纯函数、快启动

- **红测**：`tests/world_v2/test_epoch_continuity_h10.py` — 冷启动对完整前缀仍走 genesis reducer 重放；时钟推进仍写 `AffectEpisodeDecayed`；连续性快照会带上 TriggerProcess / observation 命名空间。
- **改动**：新增 `epoch_continuity.py` / `epoch_genesis.py` / `scripts/start_world_v2_epoch.py`。归档是 SQLite backup 后 chmod 0444，代码永不打开归档。新库第一条仍是 `WorldStarted`，连续性放在可选 `payload.continuity`（不新增事件类型）。导入的 fact/affect/appraisal 把 `committed_world_event` 证据和 `accepted_event_ref` 绑到新 genesis；operator 锚点不改所以指纹不变，ledger 锚点重算指纹。`AffectEpisodeDecayed` 不再由 clock tick 写出；`make_projection` 按 logical_time 读时物化强度，head 里的锚点不动。SQLite 在前缀证明完整且 bundle 一致时跳过 reducer 重放，但仍校验信封哈希与 commit 绑定；缺前缀走全量 replay，残缺前缀 `LedgerIntegrityError`。§10.1：`committed_world_event_refs` 本包不剪（太多 `len(refs)==world_revision` 调用点），新纪元从头变小；idempotency 靠新 sqlite 文件；TriggerProcess 已在 H2 sidecar，genesis 不拷进程表；vertical 组成不变；不 bump reducer bundle。
- **测试**：H10 红测绿；prefix-proof / sqlite ledger 篡改仍 fail-closed；appraisal 时钟后强度下降且 `entity_revision` 不再 +1；goal expiry 与衰减不再同批写 decay 事件；character-tier 498 passed；ruff 绿。
- **生产证据**：本 worktree 没有 `data/companion.sqlite`，脚本不会偷偷改生产库。真正切 epoch 需要停 daemon 后跑 `scripts/start_world_v2_epoch.py`，并把运行配置指到新库；归档可回滚。
- **成本与延迟**：去掉 clock tick 上的 `AffectEpisodeDecayed` 写入（生产曾占事件约 25%）。冷启动不再 reduce 全历史，仍要扫信封哈希；新纪元 head 从编译快照起步，不继承 9.3MB 流程表。
- **剩余缺口**：`committed_world_event_refs` 前进仍 1:1 增长；衰减仍跟 logical_time，idle 不写时钟时要等到下一次 due/inbound；thread/commitment/memory 的 `accepted_event_ref` 未 rebound；`assertion_binding.source_ref` 仍可能指向归档事件；未单独重放 `BiographicalTimelineConfigured`；旧 `world_snapshots` 131MB 清理未做；切生产库与启动 ≤10s 需真实账本验证。
- **commit**：`c6cde1bc`

### 2026-08-13 §12.9 第 8 条：世界用加权表模拟，模型只演角色

- **红测**：`tests/world_v2/test_weighted_world_item8.py` — 有 `ReviewedLifeSeedCatalog` 时 NPC ecology 仍打 actor/world 模型；加权抽取不可复现；day_sheet 没有天气。
- **改动**：新增 `weighted_table.py`（`sha256(seed)[:8] % total`，无 `RandomDraw` 事件）。`NpcEcology` 在 catalog 为 `ReviewedLifeSeedCatalog` 时用 `NpcInitiativeWeightPolicy` 抽 token：nothing 则 NPC no_op，命中则用已审 event 的 summary/location/duration/outcomes 编 actor+world 决定，0 次 `complete_json_object`。无 catalog / `SimpleNamespace` 仍走模型（现有 `test_npc_ecology.py` 不变）。Life development 在 compiler 暴露的 catalog 为已审目录时直接 `{"decision":"no_op"}`（开口已由 activity lifecycle 消费，本包不编 `LifeDevelopmentPossibilityDraft`）。`day_skeleton.compile_day_sheet` 按当地日哈希加一句天气。§12.10 冻结模块未改；不 bump reducer bundle；无新 model-bearing purpose。
- **测试**：item8 红测绿；NPC 无 catalog 仍调模型；生产 open-life 与 public host 不再依赖世界作者发明情节；character-tier 498 passed；life_development_runtime 117 passed；ruff 绿。
- **生产证据**：生产 `NpcEcology(..., catalog=life_seed_catalog)` 与 `ProjectionLifeCapabilityManifestCompiler` 都会走表。NPC 例程密度由 `world_seed.yaml` 的 `base_chance_bp` 与 nothing 质量调节，加事件不加模型账单。
- **成本与延迟**：生产 quiet wake 上 NPC actor + world author 与 life world-author 的 provider 调用归零；NPC 决定仍落账本以便 CAS/replay。
- **剩余缺口**：life development 没有从 seed opening 编译 propose（随机环境事件若要写成 occurrence，还差确定性 draft 编译器）；host 资格里原先靠世界作者发明的 activity/aftermath 链改为断言 0 模型 + replay 稳定，角色拥有的 activity/aftermath 闭包仍由单元测试覆盖；天气是日哈希短句，不是店铺开闭；衰减/epoch 缺口仍见 H10。**2026-08-14 用户要求撤回本条两边（NPC 模型路径 + 世界作者），代码暂不改，见 §17 同日暂存。**
- **commit**：`b060d961`

### 2026-08-13 H11 接通有来源的生活照片

- **红测**：`tests/world_v2/test_sourced_life_media_h11.py` — 两个媒体开关默认关；工厂仍装 `OpenAIMediaInspector`；`/legacy/action` 与缺文件仍能过审查。
- **改动**：`ALLOW_AUTO_IMAGE_GENERATION` 与 `WORLD_V2_MEDIA_PREVIEW_ENABLED` 默认 True。`build_qq_media_preview_deployment` 仍在缺开关/密钥/恰好一个收件人/三个 grant 时整条车道 disable。生产审查换成 `SourcedLifeMediaInspector`（有 `event_id`、非 `/legacy/action` 的 `primary_evidence_ref`、证据值非空、文件存在才过）；`SourcedLifeMediaRenderer` 在无来源时 0 次出图。PhotoCandidate 仍只从已提交/已结算生活派生。`OpenAIMediaInspector` 模块保留给历史测试。不 bump reducer bundle；无新 model-bearing purpose。§12.10 冻结模块未改。
- **测试**：H11 红测绿；qq media deployment 仍 fail-closed；event ecology / provisioning / provider transport / isolated daemon 绿；character-tier 498 passed；ruff 绿。
- **生产证据**：本 worktree 没有 `data/companion.sqlite`，未跑 grant provisioning、未改生产库。真正出图需要：密钥齐全、停/启 daemon 读新默认（或 `.env` 未把开关写成 false）、对运行库执行 `scripts/provision_world_v2_media_authority.py`。隔离验收脚本仍显式关两个开关。
- **成本与延迟**：每张图少一次 gpt-4o 审查调用；规划仍走 DeepSeek `MediaPlanner`，渲染仍走 OpenAI Image。失败即放弃该张，无来源计划不再为审查去生成。
- **剩余缺口**：月内 ≥10 张有来源生活照片是生产验收，不是单测能声称的；缺 grant 时车道仍静默 disable（需 operator 跑 provisioning）；`world_v2_media_inspection_model` 配置闲置；inspection Action/grant 仍在（确定性检查也走同一 effect-once）；像素质量不再由视觉模型把关；规划仍是一次模型调用；H10 生产换库仍需人工。
- **commit**：`5dd68f94`

### 2026-08-14 暂存：撤回 §12.9 第 8 条（两边都回来，代码先不动）

- **用户原话**：两边都想回来，但是先记下来先别动。
- **要回来的两边**：
  1. NPC ecology 的 **actor + world 模型路径**（现在有 `ReviewedLifeSeedCatalog` 时走加权表，0 次 `complete_json_object`）。
  2. Life development 的 **世界作者**（现在 catalog 在时恒 `{"decision":"no_op"}`，`model_id="deterministic:weighted-table"`）。
- **现状仍在生产跑**：`b060d961`。NPC 表实际是 `configs/world_seed.yaml` 的 `npc_initiated_events` 8 行（范远 6、林晚 2），`nothing` 补到 10000 bp；父母沈岚/陈远不在这张表上。`inject_nothing_mass` 未接线。天气仍是 `day_skeleton.py` 日哈希五句之一。活动开口仍由她从 catalog 里选，不是加权世界模拟器。
- **为什么要撤（已对齐、未施工）**：加权表当世界模拟器内容不够厚；世界几乎不再长出新事件；NPC 变成道具表。设计意图与 ADR 0010 仍是 NPC 作为有模型决定权的 actor。Sims/CK3 类比假设了一张厚表，落地的是架构没有内容。
- **成本约束（2026-08-14 补记，撤回不得整包 revert）**：第 8 条就是因为成本改的。归档账本里 NPC ecology 61 次模型、**0 次 validated**（`main_invalid_output` 33 + `corrective_invalid` 27）；life-development 340 次里审查占 143。撤回时：
  - 例行世界（天气、课表、NPC 日常表）**保持 0 模型**。
  - NPC actor / 世界作者若回来，必须是 **Occasion 门控 + one-shot**：无审查、无同契约纠正；契约 Postel。禁止每个 ecology wake 打模型。
  - 月桶不变：NPC+社会 ¥8，人生节拍 ¥8。超了只减机会频率，不削她的上下文、不让她失声。
  - 旧 NPC 车道的失败形态证明：没有宽进形状就回来 = 再买一遍非法输出。
- **明确不做**：不改代码、不改测试、不改生产、不 revert `b060d961`、不把表加厚当成撤回。动手需要用户再说一次。
- **commit**：无

### 2026-08-14 暂存：连续存在的内心 — 贵的是计算，薄的是状态

- **用户原话**：还是要考虑成本；连续存在的角色内心成本似乎较高也不够连续。
- **判据（已有、未做完）**：§12.3 A0 — 连续存在是**状态连续**，不是**计算连续**。钱只能花在会进入下次 Present 的一次 `consider()` 上。
- **归档账本（`companion.sqlite`，用量 8/7–8/13）**：账单 ¥13.94。私人印象 3,034 次 attempt / 7 条接受；主动联系 897 次；NPC 61 次 / 0 合法。8/12 重启日 ¥6.68、5 条用户消息。缓存命中 22.9%。
- **新纪元（`companion.epoch2.sqlite`，切库后约 9 小时）**：账单 ¥0.072。`inbound_turn` 2 成功 ¥0.026（真回复）；`proactive_contact` 3 成功计费 ¥0.029、**0 条送达**（审计 `primary_timeout` / `budget_exhausted`，provider 仍出账）；`world_stimulus_appraisal` 1 次 24,547 token ¥0.017。缓存命中仍 **23.1%**（H3 未在稀疏流量上兑现）。对冲 6.5s 后 backup 的 `caller_cancelled` 行为 0 token，但主调用已计费的超时仍丢表达。
- **为什么觉得不连续（状态侧，不是调用次数）**：genesis 快照 `experiences=0`（归档有 41 条 `ExperienceCommitted`；`_updated_at` 读不到 experience 的日期时会被 30 日窗滤掉）。连续性快照没有对话史。新纪元 0 次活动 / 0 次 occurrence / 0 条新事实 / 0 条新印象。她下次开口主要看见评价和记忆候选，看不见经历和聊过的话。
- **该花 / 不该花（暂不施工）**：
  - 该花：入站一次 consider；H6 间隔上的未平情绪（写出她能重读的残留）；到期且真发出或留下残留的安静窗口；每天一次 `day_open`。
  - 不该花：扫历史 appraisal 的私人印象农场；每个 wake 的 NPC/世界作者；计费成功但预算耗尽所以没送达的主动联系；入站之后再付一次 24k token 的 stimulus（应并入同一次 consider，或懒求值进 Present）。
  - 连续性补丁优先于加调用：把经历和对话编进 Present；修 genesis 日期过滤；缓存前缀仍受 tools 分叉与跨小时 TTL 限制。
- **明确不做**：本条不改代码。与上条撤回约束一起等用户动手。
- **commit**：无

### 2026-08-14 立项：H12–H16 施工说明书（交接给他人实施）

- **用户决定**：不由本会话施工，出一份详细 md 交给别的实施者做。
- **产物**：[`docs/design/interior-continuity-implementation-spec.md`](./interior-continuity-implementation-spec.md)。
  它是**本文的下级工单，不是第三份并列路线图**；冲突时以本文与设计总纲为准。
- **总方向**：不再为「想」单独付钱，改成在她已经付费的每一次开口上多收一样东西；世界只决定机会，
  内容由她顺手产出，偶尔硬化成一条改写规则表的弧。**总账是减少调用**（H14 为负成本）。
- **包**：H12 补泄漏（0 模型）；H13 期待与沉默语义（≈0 新增调用）；H14 删私人印象独立车道（负成本）；
  H15 生活开门（含未验风险）；H16 Occasion 队列做实（承重墙）。顺序与依赖见说明书 §9。
- **新查明的四处**（都在说明书里给了坐标）：
  1. `epoch_continuity._updated_at` 找不到 `ExperienceProjection` 的日期 → `datetime.min` → 30 日窗滤光。
  2. `context_capsule.RANK_DOMAIN_IMPORTANCE_BP` 里 advisories 5,000 最低，期待走 advisory 载体，
     全局超限时第一个被逐出；`recent_experiences` 7,000 还低于 `active_memory_candidates` 7,500。
  3. `present_prompt.compile_slim_interior_envelope:385/402` 把 `response_expectation` 与
     `response_expectation_assessment` 硬写成 `None` —— 整条期待机制齐备但从未被喂过（0/106）。
  4. `social_initiative.py:1168-1170` 那句抑制只存在于注释，代码没做。
- **必须先验的风险**：懒求值的 outcome 质地 vs replay 确定性（`ExperienceProjection.semantic_fingerprint`
  由 values 计算，summary 事后才有时指纹怎么算）。探针结论出来前不得动 aftermath 提交路径。
- **ADR 时效性审计（用户质疑后补做，2026-08-14）**：`docs/adr/` 整个目录 **2026-08-12 之后未再改动**，
  而 H1–H11 全在之后。逐条对代码核实：
  - **ADR 0017「携带证明的选择性来源审查」自称是「唯一生产可见聊天路线」，但它依赖的三个模块
    （`structured_source_review_model.py` / `source_review_authority.py` / `visible_source_review_model.py`）
    已被裁决 7 全部删除 —— 该 ADR 完全作废，且是最容易误导后续实施者的一条。**
  - ADR 0012「模型撰写的开放式人生发展」被 `b060d961` 改成恒 `no_op`，事实上推翻但无 supersede 记录。
  - ADR 0001 状态是 `proposed`，从未 accepted，不得当决定引用。
  - ADR 0008 的「唤醒方式」一节已被 H2 的 due 驱动取代；模块边界仍有效。
  - 仍有效：0010（宗旨）、0004、0011（机制在但 0 生产者）、0014、0016。0015 未复核。
  - **结论：除 0010 外，ADR 一律当历史证据读。现行事实以代码 > `configs/mechanism_closure.yaml`
    （仍在维护的机器可读证据索引）> 本文与设计总纲 > ADR 为序。**
  - 顺带查明：H1d 说要删的 `world_v2/life_review_identity.py` **实际保留了**（仍被
    `life_development_runtime.py` / `life_development_source_closure.py` / `batch_invariants.py` 引用）。
    这是计划文本与落地的偏差，不是待办。
- **顺带查明（改小了 H13 的工作量）**：`conversation.expectation_expiry` **已经注册**
  （`vertical_registry.py:585`、`delayed_trigger_owner_registry.py:294-310`），
  `trigger_mode="derived_formula"`、`runtime_owner=SituationCompiler.compile`、
  due 字段就是 `ResponseExpectationAuthority.not_before` / `expires_at`。
  到期判定是每次 inbound/tick 顺带算出来的，**0 成本**。整套期待设施都为这个场景造好了，
  唯一缺的就是她从来没有机会声明期待（`present_prompt.py:385/402` 硬写 `None`）。
- **运维前提**：修好 H12a 也不会让当前生产库追溯拿回 41 条历史经历（`rebuild()` fail-closed），
  要么再切一次纪元（需用户批准），要么从修复后开始累积。**实施者不得自行切库。**
- **commit**：无（仅文档）

### 2026-08-14 H12 补上已付费但没送到的连续性（0 模型）

- **红测**：`tests/world_v2/test_interior_continuity_h12.py`
  - 窗内经历被 `_updated_at=datetime.min` 滤光
  - 超限时 `response_expectation` advisory 先于 optional advisory 被逐出
  - `recent_experiences` domain rank 低于记忆候选
  - Action 停在 unknown 时对话史没有她的气泡
  - 死路 slice `recalled_emotional_associations` 仍被编进快照
- **改动**：
  1. `_updated_at` 回退读 `occurred_to` / `occurred_from`；facts/memories 仍优先 `updated_at`/`committed_at`，排序锁测未变。
  2. `RANK_DOMAIN_IMPORTANCE_BP["recent_experiences"]=7750`（高于记忆 7500，低于开环 8000）。`response_expectation` 与 `proactive_opportunity` 一样排到 advisory 头、last-tier 保底。未新增 SliceName。
  3. `RecentDialogueItem.delivery_state` 增加 `unknown`；unknown 回执进入对话材料，状态如实。`_typed_recent_dialogue_proof` 仍只认 `delivered`。
  4. 删除 snapshot compiler 对 `recalled_emotional_associations` 的读取及该 facet key。recall corpus 内部同名 source_slice 未动。
- **测试**：H12 红测绿；context capsule / recent dialogue watermark / model-facing / epoch continuity h10 共 71 passed；character-tier 498 passed；ruff 绿。
- **生产证据**：只读打开 `data/companion.epoch1.sqlite` 的 `world_v2_head_state_items.experiences`（41 条）。用修复后的编译器、logical_time=2026-08-14T00:00Z 得到 **24** 条（上限 `_EXPERIENCE_LIMIT`），不再是 0。未写归档、未切 epoch2。超限 capsule：两条同 rank advisory 时保留 `response_expectation`、丢掉 `appraisal_candidate`。
- **成本与延迟**：0 模型。不新增 purpose、不改账单形状。
- **剩余缺口**：当前生产 `epoch2` 不会追溯拿到这 41 条（用户已裁定丢掉，不切库）。她要等到修复后新写的经历才会进连续性快照。H13 仍未让她能声明 `waiting_for`。`present_prompt._MATERIAL_ORDER` 仍留着死路键名，无消费者。
- **commit**：`839c8819`

### 2026-08-14 H13a–d 期待与沉默的语义（≈0 新增调用）

- **红测**：`tests/world_v2/test_interior_continuity_h13.py`
  - slim `waiting_for` 进不了 `response_expectation`（信封硬写 `None`）
  - 问号不会被推断成期待（不变式）
  - 超长 `waiting_for` 不得吃掉整回合
  - 加键后 G4：slim schema 必填/总字段/深度
  - silence consider 材料读不到她当时声明的希望
  - slim `how_it_landed` 进不了同一次 inbound 的 assessment
  - pending expectation 时 idle 仍开 `spontaneous_contact`
- **改动**：
  1. slim 增加可选 `waiting_for` / `how_it_landed`。`waiting_for` 编成 `ResponseExpectationDraft`（hoped 裁到 128；pressure/importance 系统中位 5000；wait/expiry 复用 reselection 中位：43214 / 86400）。不问号推断。空/非法键跳过，不失败回合。
  2. `compile_slim_interior_envelope` 从 expression 拷贝这两项，不再硬写 `None`。compact gate 与 combined-turn 说明补上 slim 键。G4：slim `(1,7,2)`，compact `(2,2,2)`。
  3. `_LedgerCapsuleInteriorProjection` 用沉默锚点回执调用 `attach_pending_expectation_advisory`，材料形状与 inbound advisory 相同。
  4. `_spontaneous_contact` 在 cadence draw 前：有未过期 pending expectation 则不造 generic idle。idle 1800 / cooldown 900 未改。
- **测试**：H13 8 passed；social_initiative / contract G4 / expectation_feelings / aspirations / silence / assessment 53 passed；character-tier 498 passed；ruff 绿。未 format 大文件。
- **生产证据**：只读。归档 `companion.sqlite` 仍是 **0/106** `ExpressionPlanAccepted` 带期待、`ResponseExpectationAssessed` **1** 条。`epoch2` **0/2** 带期待、评估 **0**。UTC 今日 usage：`inbound_turn` 1 / `proactive_contact` 4。代码未重启，新声明要等进程拉起且她真的填 `waiting_for`。
- **成本与延迟**：0 新增模型调用。评估搭在已付费 inbound 上。不新增 purpose / Occasion。
- **剩余缺口**：H13e（到期仍无回应的一次机会）按说明书排在 H16 之后。系统默认 wait≈12h / expiry=1d 不是她的判断。生产数字在重启前不会动。
- **commit**：`2d8ec088`

### 2026-08-14 H16 Occasion 队列做实（承重墙）

- **红测**：`tests/world_v2/test_interior_continuity_h16.py`
  - 五种 kind 没有 merge_key / expires_at
  - 允许第六种 kind
  - 进程内 gate 重启后失忆，同一 occasion_id 可再 admit
  - 过期 quiet_gap 仍会打到角色模型
  - 同一 Occasion 可 consider 两次
- **改动**：
  1. 五种 mint helper 一律带 `merge_key` + `expires_at`（TTL：user_message 7d、quiet_gap 43200s、unsettled_feeling/life_beat 24h、day_open 36h）。第六种 kind 抛 ValueError。
  2. 耐久 sidecar `world_v2_occasion_spends`；`OccasionConsiderGate(store=...)` 跨重启拒绝同一 `occasion_id`。
  3. `InteriorOpportunity.occasion`；consider 显式 occasion 优先，否则按 purpose mint（`merge_key=opportunity_ref`）。过期 → `occasion_expired`，不调模型、不 mark_spent。
  4. 接线：inbound `mint_user_message`、proactive `mint_quiet_gap`、activity `mint_day_open(merge_key=day_key)`。生产 bind 装 SQLite store。
- **测试**：H16 5 passed；character-tier 498 passed；ruff 绿。未 format 大文件。
- **生产证据**：只读 `companion.epoch2.sqlite`。尚无 `world_v2_occasion_spends` 表（进程未重启，bind 后才会建）。usage 仍是 inbound 2 成功 / proactive 3 成功 3 失败 / stimulus 1，无新 purpose。改 `src/` 后未重启则数字不会变。
- **成本与延迟**：0 新增模型调用。不新增 purpose / 第六种 Occasion。过期机会直接丢弃，少一次空转调用。
- **剩余缺口**：`unsettled_feeling` / `life_beat` 工厂已有，但 `experience()` 仍不走 consider gate。quiet_gap 的 `created_at` 是 consider 时的 now（弱 G7）；真正过期仍靠 H5 `spontaneous_expiry_seconds`。day_open 仍同时写 daily store。`test_character_interior.py` 里同一 `opportunity_ref` 不同 `source_refs` 的碰撞测不在 character-tier，未为它放松 G2。
- **commit**：`4f681b75`

### 2026-08-14 H13e 期待过期仍无回应（挂已有 Occasion）

- **红测**：`tests/world_v2/test_interior_continuity_h13e.py`
  - 到期仍无回应时 `next_opportunity` 不给机会
  - 超过 1h grace 仍 mint
  - 终态 process 后重 mint
  - 他已回话（更新 observation）仍当成未回应
- **改动**：
  1. `expired_unanswered_expectation`：到期且 `now < expires_at + 1h`、无终态评估、无更新用户 observation，才给出一条。
  2. `SocialInitiativeCompiler.next_opportunity` 在 retry 之后、cooldown 之前 mint；`source_kind="spontaneous_contact"`（不新增 kind），挂已有 quiet_gap。
  3. 已有 terminal 同 trigger 不再 mint；open/claimed 仍走 `_from_source` 恢复。
- **测试**：H13e 4 passed；H13 + social_initiative / G4 / expectation_feelings 108 相关测绿；character-tier 498 passed；ruff 绿。
- **生产证据**：只读。epoch2 `ExpressionPlanAccepted` **0/2** 带期待，`ResponseExpectationAssessed` **0**。她还没声明过 `waiting_for`，这条车道生产上仍无触发源。进程未重启。
- **成本与延迟**：稀有情形下多一次已有 `proactive_contact` 机会，不新增 purpose。cooldown 不会吃掉这一次。
- **剩余缺口**：系统默认 wait≈12h / expiry=1d 仍不是她的判断。生产要等重启且她真的填了 `waiting_for` 并到期。quiet_gap 的 consider 时钟仍是弱 G7（见 H16）。
- **commit**：`66145c5e`

### 2026-08-14 H14 删独立私人印象车道（负成本）

- **红测**：`tests/world_v2/test_interior_continuity_h14.py`
  - slim `stuck_with_me` 编不成私人印象 draft
  - 空/无来源仍开印象
  - 生产 drain 仍调 opener / advance（独立模型农场）
- **改动**：
  1. `compile_paid_private_impression_draft`：retain、confidence 5000、`until_counter_evidence`、sources 去重截到 8。
  2. 生产 `drain_private_impression_once` 直接 `return None`。独立农场停在生产 drain 上。
  3. `PrivateImpressionTriggerRuntime.drain_one` 未改，既有单测仍可跑。未动 `batch_invariants.py`。
- **测试**：H14 3 passed；`test_private_impression_producer.py` 绿；character-tier 498 passed；ruff 绿。
- **生产证据**：只读。epoch2 `world_v2_model_usage` **0** 条 private-impression purpose（切纪元后本就没有）。`PrivateImpressionAccepted` **0**。进程未重启前 drain 切断不生效。
- **成本与延迟**：负成本。该 purpose 生产调用归零。不新增模型调用。
- **剩余缺口**：slim `stuck_with_me` **没有**接到 `PrivateImpressionAccepted`。§12.10 冻结的 `_reject_new_private_impression_without_role_reflection` 要求 `source_model_result` + reflection 审计；inbound `consider` 的 `proposals: ()`，且不能把 `private_impression_transition` 混进 inbound 接受链。slim 仍 `appraise: False`。`wants` 已是 `impulse_summary`，未新开 `ThreadOpened`。验收「PrivateImpressionAccepted 显著上升」目前闭不上。
- **commit**：`d3ea2116`

### 2026-08-14 §7 探针 + H15 生活开门

- **红测**：`tests/world_v2/test_interior_continuity_h15_probe.py`、`tests/world_v2/test_interior_continuity_h15.py`
  - `WorldLifeContextItem(content=None)` 不可构造（探针：可选 excerpt 已成立）
  - 改 summary 不改指纹 / 经历可修订（探针：两者都不成立）
  - 生产 seed 0 条 `life_arc_effect`
- **探针结论**：懒求值质地**不可行**。`ExperienceValues.summary_ref` / `summary_payload_hash` 必填，指纹由 values+policy_refs 计算，事后改 summary 即改指纹；`ExperienceProjection.entity_revision` 是 `Literal[1]`，经历不可修订。按说明书 §12：**不改 aftermath 提交路径**。H15b 退到只在已付费的 day_open 与 inbound 产出质地。
- **改动**：
  1. H15a：`day_skeleton.py` / `weighted_table.py` 写明「表决定机会，她决定内容与意义」。日程骨架与加权逻辑未改。
  2. H15b：slim 说明允许把当日注意到的写进 `felt`/`stuck_with_me`；day_open context_note 同步。不新增字段（G4）。不改 activity 操作、不为微事件单开 provider。
  3. H15c：`world_seed.yaml` → `reviewed-life.14`，present opening `publishing-intern-interview` 的 offer outcome 带 `life_arc_effect`（employment / 30 天 / `role:intern`+`workplace:publishing`）。未 revert `b060d961`，未重开 world-author。
- **测试**：探针 3 + H15c 1 passed；life_author_production / biographical 28 passed；character-tier 498 passed；ruff 绿。
- **生产证据**：只读。epoch2 无 `LifeArcChanged` / `WorldOccurrenceSettled` / `ActivityStarted`。SQL D 全空。进程未重启；seed 变更要等进程拉起且她真的走完该 activity_kind 的结算。
- **成本与延迟**：0 新增模型调用。NPC/世界作者调用数不回到 `b060d961` 之前。
- **剩余缺口**：质地目前只在已付费开口的说明里，**不会**作为独立经历写进下一轮 Present（除非她自己在 `stuck_with_me` 里写下，且 H14 接受链仍未通）。`ReviewedLifeSeedCatalog.candidates_at` 没有生产调用方，单靠 seed 行不会自动开始实习面试；要等已有 activity 计划以该 `activity_kind` 完成，aftermath 才会冻 effect。generative LD 仍恒 `no_op`。`story_candidate_role` 仍是 `legacy_replay_and_fixture`。
- **commit**：`a0ae464c`

### 2026-08-15 H17 成本收缩：前缀、非法即丢（错峰已关）

- **红测**：initial tools JSON 随 `recall_allowed` 分叉；私人印象同契约最多 4 次。
- **改动**：
  1. `inbound_tool_contract.contract_for`：`phase=initial` 的 tools 始终带 recall 分支，运行时仍按 `recall_allowed` 拒绝。
  2. 私人印象 `_PRIVATE_IMPRESSION_MAX_ATTEMPTS=1`（生产 drain 仍是 H14 的 `return None`）。
  3. **同日用户关掉错峰**：删除 `consider_window.py` 及 social/proactive/reflection 的高峰推迟。安静窗口与再访按原 cadence 到期即给她。
- **剩余缺口**：H16 五种 Occasion 仍未合成单一队列；`life_beat` 没有独立 consider，生活事件只挂到下一次 quiet_gap 上。

### 2026-08-15 H18 分享余波：当场追问复用内心

- **红测**：未声明 `waiting_for` 不得从问号推断；`wait=soon` 不是 12h 默认；`still_pending`+`now` 消息是同轮追问；`still_pending` 不再挡 quiet_gap，也不再走到期未答车道；入站 advisory 写明他已回话且不含 chase/should。
- **改动**：
  1. slim 增加可选 `wait`（soon/today/later，及 短/当天/长 或前缀），编成她的 wait/expiry；省略则 later。
  2. `still_pending` 进入终态评估，同轮结掉旧盼头；还想等就另开 `waiting_for`。
  3. 入站 expectation advisory 区分「他已回话 / 他还没说话」，并写明只是证据、仍由她决定。
- **剩余缺口**：生产仍几乎没有 `waiting_for` 声明，H18 要等她真的填了才会在现网出现。未声明 wait 已由 H21 改成不定时叫醒。

### 2026-08-15 H19 接通 P3 成人媒体车道

- **红测**：无 Civitai key 时普通 bundle 仍在、`specialized_generators` 空、高档 `_generator_for` 为 None；模板缺失同样不关普通车道；凭证齐全时装上 `adult_suggestive`/`adult_explicit` 与 `FirstPersonPrivatePromptAuthor`，且不是 OpenAI 生成器；无来源高档计划仍 `unsourced_event`。
- **改动**：
  1. `build_qq_media_preview_deployment` 在 `CIVITAI_KREA2_ENABLED` + `CIVITAI_API_KEY` + 已审模板可加载时，给 `SourcedLifeMediaRenderer` 安装同一 Krea2 模板的两个 route，以及 `FirstPersonPrivatePromptAuthor`（有 OpenRouter 用 Hermes，否则用已要求的 DeepSeek）。
  2. 缺任一件只让高档 fail-closed，普通 OpenAI 生活照继续；禁止静默降级 GPT Image。
  3. `CIVITAI_KREA2_ENABLED` 默认 True。生产要出图仍需 `CIVITAI_API_KEY`。未新建关系阶段→强度规则，也未宣称 Adult Eligibility 已闭合。
- **剩余缺口**：现网 `.env` 仍无 `CIVITAI_API_KEY`，重启后高档会继续 fail-closed 直到补 key。资格层仍缺用户成人资格证明与内容级双方同意；未发出过 P3 生产照片前不把状态升成 active。

### 2026-08-15 H20 QQ 附件识图改阿里 `qwen3-vl-flash`

- **红测**：无 `QWEN_API_KEY` 工厂返回 None；有 key + grant 时 provider 是 `dashscope:vision`、模型 `qwen3-vl-flash`、思考关闭；caption HTTP JSON 含 `enable_thinking: false` 且仍发 data URL。
- **改动**：
  1. `VISION_MODEL` 默认 `qwen3-vl-flash`；新增 `QWEN_BASE_URL` 默认华北兼容端点。
  2. `build_qq_perception_deployment` 要 `QWEN_API_KEY`，不再要 OpenAI 才能开感知；不把 `openai_proxy_url` 传给百炼。
  3. transport provider 改为 `dashscope:vision`；caption 调用关思考。像素仍不进 DeepSeek。
- **剩余缺口**：生产 `.env` 当时无 `QWEN_API_KEY`；感知 grant 也尚未 provision。两件都补上并重启前，她仍然只能知道「他发来一张图」。未发出过真实识图前不把感知升成 fully active。

### 2026-08-15 H21 等待时长由她声明秒数

- **红测**：`wait=45` / `"2分钟"` 按她写的秒编译；未声明 / `soon` / 读不懂不编译盼头（不定时叫醒）；声明短于 30 秒仍垫到 30 秒；wait 已过、expiry 仍在未来也会 mint；wait 未到不 mint。
- **改动**：
  1. slim `wait` 解析整数或她写的时长（秒/分钟/小时）；`soon`/`today`/`later`/`短` 不再映射档位。没填或读不懂 wait 就不编译 `response_expectation`，允许结束话题。声明了才叫醒；短于 30 秒只垫下限。slim 未给 expiry 时，机会窗口是 wait+60 秒（调度硬边界，不是她的耐心）。
  2. H13e 叫醒改为 `logical_time >= not_before`；`expires_at + 1h grace` 仍是太晚才丢。`scheduled_for` 用 wait 而不是 expiry。主动联系 15 分钟冷却不挡这条车道，急了可以连着发。
  3. 完整契约补一句：wake 在 wait 之后一次，expiry 只结束盼头。问、换话题、沉默仍由她选。
- **剩余缺口**：生产要重启才吃到。已落账的旧盼头仍带着当时编进去的 wait/expiry（例如 1h/2h）；新回合才会按秒声明。

### 2026-08-17 H22 用生产账本反证：她的选择被清零在哪

先读 epoch2（08-13 → 08-17，3838 事件），不看文档结论。四个数字定了案：

| 现象 | 账本实测 | 结构原因 |
|---|---|---|
| 「仍把用户当陌生人」 | 关系类事件 **0**：0 次慢变量调整、0 次承诺、`relationship_states` 空 | `_SLIM_ZERO_RELATIONSHIP_DELTAS` 把六轴写死为 0，`relationship_adjustment_compiler` 对全零判 `no_change` |
| 「丝毫没有坏情绪」 | 8 个情绪成分：warmth×6、joy、sadness@3500、loneliness@1500；anger/hurt/resentment **0** | 人设三处系统性软化 + 提示词无对称许可 |
| 「越想越气」从未发生 | 44 次评价，**36 次恰好 5000**，最高 8200；reflection 门槛 8500 → 复燃 **0** 次 | slim 把 `confidence` 硬编码 5000；且默认 2h 过期短于 1h/3h/12h/24h 复燃阶梯，wound 在第二次想起前就死了 |
| 「文风不像真人」 | 95 条气泡：**66% 以句号结尾**、**0 表情**、**19% 以「不过」开头** | 系统提示 ~21k 字符里英文填表契约约 19k，她的中文口语语料约 190 字（<1%） |

- **红测**：她写的 `us_deltas` 落进 `relationship_signal.suggested_deltas`（含负数）；畸形/空/越界的 `us_deltas` 一律不动分（不猜）；reply_only 携带非零增量被接受、`interaction_act` 仍被拒；`matters_bp` ≥ 8500 时评价可复燃、省略时低于门槛；默认评价窗口长于整条复燃阶梯；人设不再含「说话软」「语气温一点」「过度暧昧」，语料 ≥12 条且几乎不打句号。
- **改动**：
  1. slim 新增 `us_deltas`：她自己写六轴的带符号移动，省略的轴为 0，全零/畸形不动分；compiler 的 ±500 上限仍是硬边界。`attach_hitchhiked_relationship_residue` 增加 `authored=` 入参，三处调用点（compact gate、两处 stream 解析、combined 解析）都传原始 slim 对象。
  2. slim 新增 `matters_bp`：她自己给这次阅读的分量，直接成为 appraisal 的 `confidence`。指令明确告诉她 8500 是「会被再叫醒想同一件事」的门槛——这是关于她自己官能的证据，不是剧本。
  3. `DEFAULT_APPRAISAL_WINDOW` 2h → 48h：修掉「复燃阶梯设计成 1h/3h/12h/24h，但 wound 2h 就过期」的内部矛盾。
  4. 删掉三处确定性劝阻：「about_us 不会改变 relationship scores」「几次礼貌交谈不必移动 stage」「ordinary closeness, not a romance script」。改为中立表述：宿主没有目标阶段也没有偏好方向，被吸引/困惑/厌倦/想远离都由她读。
  5. reply_only 能力描述从「排除 relationship 更新」改为「排除 interaction 协议更新」，与 `_REPLY_ONLY_FORBIDDEN_APPRAISAL_EFFECT_FIELDS` 实际行为一致（此前文案与代码互相矛盾）。
  6. `character.yaml`：人设去掉系统性软化，写明被敷衍/被冒犯会真的不舒服、可能冷一阵也可能过几天还记着；语料 5 条 → 16 条，`speech` 写明标点习惯（大部分不打句号）、碎句、可只回一个字、不必每条反问、偶尔用表情；`style_rules` 加三条反「每条都圆满」「隔一条就用不过其实往回收」。
  7. `mood_view._NOTICEABLE_BP` 2000 → 600（刚高于 residue 500 / 衰减地板 300）：她自己刻意开的低强度情绪不再对自己的生活车道隐形。
  8. 新增 `scripts/audit_lived_experience.py`：只读账本，直接报关系是否在动、有没有负面情绪、有没有复燃、有没有生活、以及文风四项指标。这是本项目第一条把验收闭合到「生产上真的发生了」的工具。
  9. 运维：`launchd` 两个 plist 与两个启动脚本此前指向 `.claude/worktrees/fix-cost-optimization`（该 worktree 已合并回主仓库并移除），`KeepAlive` 会在进程退出后永久起不来。全部改为单一根目录 `/Users/geoff/Projects/Girl-Agent`，已 bootout/bootstrap 重启并确认 health 正常。
- **基线**：冻结机制基线 `.70` → `.72`，manifest `fa3908f3…`，两个独立 120 例进程复核一致。逐例断言不变，移动的是 prompt/appraisal 身份。
- **接通后立刻暴露的第二道锁（同批修掉）**：重启后调度器报 `relationship state references an uninstalled policy`。这是第一次有非零增量的信号走到调整编译器，撞上一个此前无法被触发的潜在缺陷。epoch2 创世的 `continuity` 携带的是关系**状态**而不是原始事件，状态上盖的是 2026-08-11 提交 `930b6229` **之前**的摘要 `64d8b7ff…`；重放路径早有 `allow_legacy_relationship_policy_digest` 容忍旧事件，但 `preview_relationship_slow_variable_adjustment` 对**现存状态**重新断言当前摘要，于是任何新调整永久 fail-closed。用历史版本复算证明该摘要正是本仓库当时的产物，且 `_POLICY` 数值逐字节相同——摘要变化只因承诺转移表加入了哈希输入，累积变量是在数值相同的规则下长出来的。修法：新增 `RETIRED_RELATIONSHIP_POLICY_DIGESTS` 注册表，读现存状态时接受退役摘要，写入的 mutation 仍必须携带已安装摘要，因此第一次成功调整就把状态迁移到新戳（migration-by-write）。付载荷仍拒绝旧摘要（既有测试 `test_relationship_policy_digest_binds_commitment_transition_graph` 不变）；外来摘要仍拒绝。生产账本证据：`user:geoff` 停在 trust 100 / closeness 180 / respect 80 / mutuality 110，`last_adjusted_at` 是 **2026-08-08**——也就是说 epoch2 开始以来关系被两道独立的锁同时冻住。
- **剩余缺口**（下一波，按感知收益排序）：
  1. **英文契约压缩/中文化**：系统提示仍是 ~19k 英文填表说明对 ~1k 中文人设。这是文风的最大结构性负担，也是本轮唯一没动的根因。`slim_consider_instruction`（6.3k）与 `expression_draft_shape_contract`（5k）被大量测试逐字断言，需单独一包。
  2. **对话按真实气泡呈现**：历史仍是单条 user JSON 里的 `materials.recent_dialogue`，英文键名。改成真正的多轮或气泡文本块会动 replay 身份与 32k 预算，需单独评估。
  3. **`ambiguous`/`lover` 承诺协议未安装**：`relationship_reducers` 对这两个阶段 fail-closed，`_POLICY` 无 enter/exit，schema 只允许三档。她可以在聊天里暧昧，但账本永远升不到暧昧/恋人。改 `_POLICY` 会动 `RELATIONSHIP_POLICY_DIGEST`，属迁移级改动。
  4. **私人印象仍是 0**：`drain_private_impression_once` 恒 `return None`（成本决策），paid-inbound hitch 受冻结批次不变量阻挡。她在他不在时仍不会想起他。
  5. **生活生态仍近乎静止**：4 天 0 个活动开始/完成、1 个已结算事件。她没有生活可讲，这独立于本轮改动。
  6. **`_reply_only_fallback_appraisal` 仍在 wire 破损时替她写 `no_change`**：违反 AGENTS.md「不得由确定性代码替角色决定」，正解是受约束重选，需一次额外调用的成本裁决。

### 2026-08-17 H23 把世界还给它的作者（H22 剩余缺口全清）

H22 记录的六条剩余缺口本轮全部施工。核心判断：账本里"什么都没发生"的绝大部分不是模型不肯，而是**确定性代码在模型之前就替它答了**。

- **红测**：世界作者在 catalog 存在时被真正问到（no_op 只能由它自己答）；世界作者不可达时是 `deferred` 技术失败而不是安静的 no_op；落地的 plan 必须是作者写的且她接受；NPC 有 catalog 时仍走 actor 模型、propose 后到达世界裁决；她声明的 `we_are=ambiguous/lover` 能落账、阈值既不能派生也不能把她降级出去、协议外的阶段仍拒；wire 破损但她写了合法 affect lifecycle 时那份情绪存活、完全读不懂时才 no_change；只写 `stuck_with_me`+`keep_impression` 能产生 appraisal 锚点、不要求留存则不替她留；`since_he_last_spoke` 只从她已有的对话算、没有他的消息时不报；跨纪元退役摘要可读、外来摘要仍拒。
- **改动**：
  1. **生活生态点火**：删除 `life_development_runtime.py` 的加权表短路（此前 80% 概率直接返回 `{"decision":"no_op"}`、`model_id="deterministic:weighted-table"`，连 provider 都不调）。删除 `npc_ecology.py` 的 `_weighted_actor_decision` / `_weighted_world_decision` 短路。两处都落回既有的 one-shot + 一次受约束重选路径，Occasion cadence 仍是唯一的频率闸门——符合 2026-08-14「撤回第 8 条」记录里的成本约束（Occasion 门控、无审查、超支只减机会频率）。
  2. **安装 ambiguous / lover**：新增 `COMMITMENT_ONLY_RELATIONSHIP_STAGES`。这两个阶段**不在阈值梯子上**——`_derive_stage` 对它们原样返回，六轴继续在底下动但不能把她降级出她自己说过的话；只有另一次承诺能改。转移图加 `close_friend→ambiguous`、`ambiguous→{lover, close_friend}`、`lover→ambiguous`（她也能自己降回去）。放开 `schemas.py` / `inbound_appraisal_wire.py` / `proposal_envelope.py` / `relationship_proposal_compiler.py` / `_SLIM_ORDINARY_STAGES` 五处三档上限，并在 slim 指令里告诉她这两档只能靠她真正发出去的话建立。系统其余部分（媒体资格、视觉证据、embodiment）本来就认这两个阶段，缺的只是承诺协议这一段。
  3. **打通私人印象**：`keep_impression=true` + 非空 `stuck_with_me` 现在会产生 appraisal 锚点（此前只写 `stuck_with_me` 不写 `felt` → `appraise=false` → 已付费 hitch 找不到 active appraisal，直接 `return None`，这是 epoch2 `PrivateImpressionAccepted=0` 的直接原因）。另外表达技术失败或被后续 inbound 取代时不再连带丢弃印象——送不出去是传输问题，不该抹掉这一回合留在她心里的东西；`noticed`（世界主张）仍然只在回合真正完成时提交。**未动冻结批次不变量**：`_accept` 本来就是分批 commit。
  4. **不再替她决定"没有感觉"**：`_reply_only_fallback_appraisal` 收到破损 wire 时，若她写的 affect lifecycle（operation + 合法 components + rationale）本身可无损读出，就保留那份情绪；读不懂的部分仍然不猜。这是 H22 遗留的 AGENTS.md 违规，代价为零（无新增调用）。
  5. **时间感**：`snapshot_compiler` 新增派生材料 `since_he_last_spoke`，从她已有的 pinned 对话算距他上次说话的秒数。纯投影算术、0 模型调用；折叠段没有时间戳所以宁缺不猜。没有时间感就无法思念。
  6. **对话按聊天记录呈现**：`InnerLifeSnapshot.model_view()` 在**脱敏之后**渲染 `conversation`（`他：…` / `我：…`）。放在这一层是硬要求——编译期渲染会绕过 `visible_source_refs` 脱敏而泄露她不该看到的条目。派生呈现，不新增来源也不新增权威。
  7. **契约压缩 + 中文收尾**：合并 compact gate 里重复的英文段落（"never copy marker text" / "not part of payload_json" / 三处"宿主不替你决定"各自重复）；系统提示最末尾新增一段中文收尾框——距她实际输出最近的位置、用她写作的语言说明"以上都是投递格式，不是说话方式"，并明确不用每条都圆满、不用每条都反问、可以只回一个字或不说。**未删** reply_only 信封样板：`test_reply_only_releases_reviewable_head_from_one_physical_character_call` 等测试保证"给模型看的样板必须与规范 wire 一致"，这是真实安全属性。
  8. **摘要迁移**：安装新阶段改变了 `RELATIONSHIP_POLICY_DIGEST`，`13bfa71d…` 加入退役集。六轴数值、上限、阈值、dwell 全未变，旧摘要下存在过的阶段在新摘要下语义不变，因此读旧戳安全、写仍必须用已安装摘要。
- **基线**：`.72` → `.73`，manifest `abf5608e…`，两个独立 120 例进程复核一致。全量 5344 通过。
- **剩余缺口**：
  1. `slim_consider_instruction`（7.5k）与 `expression_draft_shape_contract`（5k）仍是英文。整体中文化是文风的下一个杠杆，但要逐字改约 30 处测试断言，且翻译精确契约语义有漂移风险，值得单独一包并配 A/B 观察。
  2. 后台独立私人印象车道（`drain_private_impression_once` 恒 `return None`）仍关闭。现在她只能在**已付费的回合里**留下印象；真正"他不在时想起他"需要一条 0 调用的替代路径（例如在已付费回合的印象上挂一次 quiet-gap 复访），未施工。
  3. 生活生态点火后必须回采成本：`life_development` 与 NPC actor 现在会真的打模型。月桶 NPC+社会 ¥8 / 人生节拍 ¥8 未变，超了按记录只减机会频率，不削她的上下文。

### 2026-08-17 H24 把这一轮交回她的语言，并让她看见自己留下过什么

先做真人对照：用生产账本副本（不污染真实记录）跑 12 回合真实对话，再按结果施工。首轮结果是**话对了、记性没跟上**——句号率 66%→0%、会说"你别一直问 问多了我真会烦"这种带刺的边界，但 0 个新情绪片段、0 次 `matters_bp`、0 条私人印象。能力链路逐个验证是通的，所以缺的是动机与语言。

- **红测**：slim 指令中文占比 > 35% 且字段名/枚举仍为英文；中立性英文锚点（`evidence, not instruction`、`no target stage and no preferred direction`、`ticket-closing`）仍在；`我最近留下的` 材料在 model_view 期渲染、不进 `materials_json`/快照哈希、不带 `source_ref`、被脱敏的条目不出现在其中、什么都没留下时不消失成空也不伪造内容。
- **改动**：
  1. **slim 契约中文化**：`slim_consider_instruction` 7493 字符英文 → 3993 字符中文（中文占比 45%）。规则是**字段名与枚举值保持英文**（它们是字面 JSON），一切对她说的话用中文。三处中立性保证保留英文原文作锚点，便于既有测试与将来审计检索。
  2. **让她看见留存的后果**：`InnerLifeSnapshot.model_view()` 新增派生材料 `我最近留下的`——还活着的持续情绪（含维度与开启时间）、还在的私人印象条数、最近这些读法各自的 `confidence_bp`、以及最近有几个她说话的回合什么都没留下。纯投影算术、0 模型调用、0 新来源。**必须在脱敏之后渲染**：这三项内容都来自带 `source_ref` 的条目，编译期算会把她看不到的条目计入甚至点名。中立性是硬要求：只陈述事实，无任何祈使句或"建议你"，也不暗示留存比不留存好；她会直接看到一串 `[5000, 5000, 5000]`，那本身就是"我从来没给任何东西加过重量"这个事实。
  3. **修一次自己造成的回归**：全中文契约首跑后她的平均消息长度从 16 字掉到 6 字（"在的""怎么了""我又没不理你"）。根因是"可以只回一个字"这个许可在人设、`speech`、compact gate 收尾三处重复，而没有任何东西平衡它——改动前偏向写小作文，改动后偏向惜字如金。改为"长度和条数跟着你真正想说的东西走：没什么要说就短，心里有话就说透"，人设侧同步补上"也别惜字如金"。
- **对照结果**（同一起点账本、同一组消息，10 秒间隔保证每条消息独立成回合）：

  | 指标 | 改动前 95 条 | 现在 14 条 |
  |---|---|---|
  | 句号结尾 | 66% | **0%** |
  | 「不过/其实」开头 | 19% | **0%** |
  | 省略号 | 15% | 29% |
  | 反问收尾 | 24% | 7% |
  | 长度中位 / 平均 | 18 / 21 | 11 / 14 |

  质性上更重要的是她开始跨回合持有东西："书店的事才说到一半 你人又跑哪去了"（记住没聊完的话题）、"你今天到底怎么了 一会说烦一会说别理我"（发现前后不一致）、"你今晚怎么这么黏"（调侃）、被问"我们算什么"时反问回去而不是照恋爱剧本走。**私人印象第一次落账（0 → 1）**。
- **基线**：`.73` → `.74`，manifest `5c03009e…`，两个独立 120 例进程复核一致。全量 5355 通过。
- **方法论教训（下次回采必须遵守）**：`--burst-interval-seconds` 小于一个回合的真实耗时（约 4–5 秒）时，后续消息会被折进下一轮，输出条数与平均长度都会被批处理污染（同一段对话在 3 秒间隔下得到 12 / 9 / 7 / 3 条输出）。任何文风对照必须用 ≥10 秒间隔，否则得到的是批处理噪声而不是文风差异。
- **剩余缺口**：
  1. **`matters_bp` 至今 0 次达到 8500**（四轮对照全部如此），所以 reflection 一次都没触发，"越想越气"仍然没有真正发生过。她现在能看到自己那串全是 5000 的分量，下一轮回采要看这个数字是否开始分化；若仍不分化，说明 8500 这个固定门槛本身不对，应改为由她声明的强度推导。→ **已由 H25 关闭**。
  2. `expression_draft_shape_contract`（5k 英文）未中文化。它主要在非 compact 路径用，优先级低于 slim。
  3. **顺带发现一处既有的脱敏泄漏（非本轮引入，未修）**：`snapshot_compiler.py` 的 `lived_moment` 在编译期把私人印象的 `reflection_summary` 与 appraisal 的 stimulus 摘录拼成一个无 `source_ref` 的字符串，`_redact_materials` 删不掉它，因此被脱敏的印象原文仍会进入 model view。修它要把 `lived_moment` 整体移到 model_view 期，会动 `materials_json`、快照哈希与两个 facet 的 `material_keys`，需单独一包。

### 2026-08-18 H25 退掉固定的复燃门槛，改由她自己的分量排序

H24 留下的第一个缺口是"`matters_bp` 从未达到 8500"。回采生产账本得到的分布终结了这个猜测：全账本 **44 次评价，37 次落在未加权默认的 5000，她真正加过重量的 7 次是 6500 / 6800×2 / 7200×2 / 7800 / 8200**——最高的一次距门槛只差 300。也就是说她一直在分化，分化幅度也一直在增长，只是**那条线本身画在了她实际量程之外**，`reflection` 因此在整个生产历史上开启 **0** 次。这不是她不在乎，是标尺不对。

- **红测**：她从未加过重量的读法不会把自己排进复燃队列；同时活着的多件事里只有最重的那件会回来；同一件事的分量由她给的数字决定是否可复燃（而不是任何常数）。
- **改动**：
  1. `REFLECTION_CONFIDENCE_THRESHOLD_BP = 8_500` → `REFLECTION_UNWEIGHTED_BP = 5_000`。复燃门槛不再是常数，而是 `_revisit_bar()`：**她当前还活着的评价里最重的那个分量**。绝对下限是"她根本没有称量这一回合"时宿主假定的那个值——没标过的回合永远不该把她安排回去再想一遍。
  2. 当初把门槛抬到 8500 的理由是防反思自激（曾观测到 ~24 次/小时的反思农场）。这个循环现在由结构关闭而不是由常数关闭：`_is_reflection_residue` 让反思自己产出的评价既不能开新伤（既有，`:137`）**也不能抬高它随后被衡量的那条线**（本轮新增，`_revisit_bar` 内），加上每轮只开一件、间隔 1h→3h→12h→24h 递增。常数因此没有在买任何东西。
  3. `matters_bp` 的中文说明同步改写：不再宣告某个固定数字是门槛，而是告诉她"宿主会挑你手上还活着、而且分量最重的那一件——是你给的分量在排序，不是某条固定的线"。这仍是关于她自己官能的证据，不是剧本。
- **生产证据**：用生产账本副本回放她真实写下的那个 7000 分量的读法，`ReflectionSchedulerResult(opened=1)`——同一条数据在改动前是 `opened=0`。**这是"越想越气"这条链路第一次端到端跑通。**
- **基线**：`.74` → `.75`，manifest `eb7bf56c…`，两个独立 120 例进程复核一致。全量 **5355 通过**（改动前唯一红的就是这条冻结基线）。生产 napcat 重启零错误（bootstrap 1365ms，`/health` 200）。
- **剩余缺口**：本轮只改了"什么会回来"，没有改"回来之后会不会升级"。她现在最重的那件事一定会被再想起，但一轮反思之后强度是否真的往上走、以及会不会稳定在同一件事上循环，要等下一次回采看 `reflections_opened` 与负面情绪成分是否同时出现。


