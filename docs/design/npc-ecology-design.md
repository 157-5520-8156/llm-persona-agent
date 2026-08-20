# NPC 生态：稀疏机会，真实决定

> **现在到底怎么跑的：** 当前代码指定的是“真思考”：每次只选一个 NPC，调用 NPC actor 模型；若它提出行动，再交 World Author 裁决。权重表函数仍在文件里，但静态 AST 结果是各 1 个定义、0 个调用。可是当前 epoch 的生产事实是：截至 seq 13055，权重表和 actor 路径都没有产生过一次 NPC 决定，`NpcStateChanged`、NPC-owned Plan、NPC Ecology occurrence 都是 0。`9e574380` 放开结算门后，下一次符合条件的机会会走 actor 模型，不会走表。
>
> **“每个 NPC 有想法”真的贵吗：** 不贵，前提是“有想法”表示偶尔轮到一个 NPC 做一次决定，而不是所有 NPC 每个 tick 都计算。用当前真实 schema、一个有共同经历/目标/关系状态的单 NPC 档案静态序列化：actor 请求约 **1,810 input + 196 output token**；DeepSeek Flash 单次约 **¥0.0018–¥0.0072**（70% 前缀命中闲时，到冷缓存高峰）。成本与每天打开多少次决定机会线性相关，与登记了 5 个还是 50 个 NPC 基本无关。
>
> **推荐方案一句话：** 用可重放随机性稀疏地决定“这次是否给哪个合资格 NPC 一个机会”，轮到后让该 NPC 真调一次模型自由决定；已经接受的计划和已冻结的后果由现有事件机确定性推进，不重复买决定。

状态：长期设计资产；不是并列路线图，也不是宏大重构计划。若与
`girl-agent-design-intent.md` 或 `harness-restructure-execution-plan.md` 冲突，以两份权威文档为准。

## 1. 核查口径

- 代码：当前工作树；`npc_ecology.py` 当前是 **2,120 行**，用户问题里的 2,038 行已被后续提交改变。
- 生产账本：SQLite `mode=ro`；旧 epoch `data/companion.sqlite`（与 `companion.epoch1.sqlite` 同为 35,549 条，不能重复相加）和当前 `data/companion.epoch2.sqlite` 的生产世界 `world:companion-v2:qq-c2c:geoff`（seq 13055）。
- 外部模型调用：0；费用 ¥0。
- token：沿用后台切片审计的 `chars / 3.8` 静态估算，不冒充 provider tokenizer 精确值。

## 2. 现在生产到底走哪条路

### 2.1 代码路径：actor 模型，不是权重表

生产组合在 `production_turn_application.py` 构造：

```text
NpcEcology(
  actor_model=npc_actor_model,
  world_author=life_world_author_model,
  catalog=life_seed_catalog,
)
  → LifeEcologyRuntime(npc_initiative_followup=npc_initiative)
```

运行链是：

```text
Clock wake
  → LifeEcologyRuntime 判断 development due / 已到期 NPC Plan / 新 NPC 可见刺激
  → NpcEcology.advance_once()
  → 只选一个 active NPC（确定性 hash 只分配机会，不选行为）
  → _actor_decide()：NPC actor 模型
      ├─ no_op：只更新该 NPC 的私有状态
      └─ propose：_world_decide() 由 World Author 裁决
          ├─ later：落 NPC-owned ActivityPlanned
          └─ now：落 WorldOccurrence，走普通 aftermath
```

`_weighted_actor_decision` 和 `_weighted_world_decision` 确实仍定义，且仍 import
`NpcInitiativeWeightPolicy` / `pick_weighted_token`；但 AST 核查结果：

- `_weighted_actor_decision`：1 个定义，0 个调用；
- `_weighted_world_decision`：1 个定义，0 个调用；
- `_actor_decide`：1 个定义，1 个生产调用；
- `_world_decide`：1 个定义，1 个生产调用。

因此权重表是 H23 拆短路后的生产死代码，不是备用分支。

### 2.2 那道 `settled` 门：结论成立，但要限定刺激范围

`9e574380` 之前，NPC lane 的条件明确排除了
`aftermath_status == "settled"`。而 settlement wake 正是 NPC 最可能获得新共同事实的时刻；
当前 epoch 又长期以 settled aftermath 为主，所以 fresh social evidence 到来时 NPC lane 被同一个
wake 挡住。

提交做了两件事：

1. 从 NPC 排除集合中移除 `settled`；
2. 增加 `NpcEcology.has_stimulus()`，当“自上次 NPC consider 后，又落了涉及已注册 NPC 的 settled
   occurrence/experience”时允许 NPC lane 跑。

它不是“任何 settlement 都叫醒所有 NPC”。`has_stimulus()` 只认涉及已注册 NPC 的 material
evidence；而 `advance()` 仍只选一个 actor。放行后调用的是 `_actor_decide()`，不调用权重表。

当前 epoch 的 0 产出证明这道门确实造成了实际饥饿；但不应把所有历史失败都归给它。旧 epoch
还证明 actor 契约曾大量非法，预算耗尽也会阻止新调用。准确结论是：**这是当前 epoch 0 产出的
直接门控之一；修掉它只恢复可达性，不等于已经产出。**

### 2.3 账本：旧历史试过模型，当前 epoch 两条路都没跑出结果

旧 epoch（`companion.sqlite`；`epoch1` 是同一 35,549 条历史的副本）：

- `NpcRegistered`：7
- `NpcStatusChanged`：1
- `NpcStateChanged`：2
- NPC Ecology `ModelResultRecorded`：61 次 attempt
  - actor：59（`main_invalid` 29、`recovery_failed` 27、`main_invalid_recovered` 2、timeout 1）
  - world：2（先 invalid、后 recovered）
- NPC Ecology 自己产出的 NPC-owned Plan：0
- NPC Ecology 自己产出的 occurrence：0

这说明 actor 模型历史上**真的被调用过**，但 61 次 attempt 最终只留下 2 次 NPC 私有状态变化，
没有一件 material NPC 事件。它不是“从来没调”，而是旧形状的有效产出率接近零。

当前 epoch（生产世界 seq 13055）：

- `NpcRegistered`：4（范予安、沈岚、陈远、徐青禾）
- `NpcStateChanged`：0
- `NpcStatusChanged`：0
- `purpose` 含 `npc` 或 `actor` 的 usage 行：0
- NPC Ecology model audit：0
- NPC Ecology Plan：0
- NPC Ecology occurrence：0

全历史唯一口径（旧 epoch + 当前 epoch，不把 `epoch1` 副本再算一次）：

- `NpcRegistered`：11 个事件行；
- `NpcStatusChanged`：1；
- `NpcStateChanged`：2；
- NPC Ecology Plan：0；
- NPC Ecology occurrence：0。

另有其他生活 lane 写出的“参与者含 NPC”的普通事件：旧 epoch 2 个 committed/settled occurrence，
当前 epoch 3 个 committed、其中 1 个 settled；它们不能算 NPC Ecology 自主产出。

还有一个观测缺口：`world_v2_model_usage.purpose` 中 NPC/actor 是 0，但旧账本有 61 个明确的 NPC
model audit。原因是 `npc_ecology.py` 没有像其他 lane 一样包 `model_call_scope("npc_actor_decision")`。
所以“按 purpose 查到 0”不等于旧时没有发网；新设计必须补 purpose 记账。

## 3. `world_seed.yaml` 里的 NPC 实际是什么

“38 个 NPC”这个前提不成立。当前 YAML 有：

- `life_author_catalog.npcs`：**5 个 reviewed NPC**；
- 根级旧 `npcs:`：**7 行**；
- 全文件 `npc_id` 键：**23 处**；其余是 NPC 绑定和事件引用，不是 38 个人。

### 3.1 五个 reviewed NPC

1. 范予安：稳定身份摘要、`trait:literature-club`、personal、默认图书馆、每日
   09:00–18:30。
2. 林晚：稳定身份摘要、`trait:roommate`、personal、默认宿舍、每日
   18:30–23:30；要求 `residence:campus_dorm`。
3. 沈岚：妈妈、`trait:parent`、personal、嘉兴家中、每日
   07:00–22:00；要求嘉兴家庭住宅。
4. 陈远：爸爸、`trait:parent`、personal、嘉兴书店、每日
   09:00–20:00；要求嘉兴家庭住宅。
5. 徐青禾：嘉兴旧友、`trait:hometown_friend`、personal、嘉兴书店、每日
   10:00–21:00；要求嘉兴家庭住宅。

这些 seed 条目有身份、隐私、默认地点、出现时窗和 biography gate；**没有 seed 内的当前计划、
当前意图或双向关系状态**。计划、意图、私有内心和“他怎么看她”只有 actor 模型成功后才进入
账本 `NpcSubjectiveState`；共同关系证据由 settled shared history 投影出来。

当前暑假 epoch 注册 4 个而不是 5 个：林晚被宿舍 residence gate 挡住；范予安没有 residence
gate，仍 active；母亲、父亲、徐青禾由 reviewed-life.16 加入嘉兴情境。

### 3.2 seed 中的 NPC 绑定

- ordinary opening：3 个 NPC 绑定（范予安 2、林晚 1）；
- future opening：2 个 NPC 绑定（都为范予安）；
- `npc_initiated_events`：8 个（范予安 6、林晚 2），每个带 summary、时窗、
  `base_chance_bp` 和 2 个固定 outcome。

这 8 个 initiated event 正是“剧情库感”的来源。但在当前生产路径里：

- summary / `base_chance_bp` / 固定 outcomes 只被死掉的 `_weighted_*` 路径消费；
- 当前 actor 不会看到这张故事表，也不会从中选行为；
- catalog 的 candidate reader 仍被 focused snapshot 用来补可用地点，并被加载期可达性校验使用。

所以这张表不是当前行为作者，但它还残留一点 capability/校验用途，不能未经拆分就整段删除。

### 3.3 哪些字段是死的或近似死的

- 根级旧 `npcs:` 的 7 行（name/kind/location/availability/templates）没有 World V2
  生产 reader；乔宁、周栩也不在 reviewed NPC 注册链里。对当前 World V2 是死配置。
- reviewed `known_trait_refs` 会被注册、做 immutable 校验并在 dashboard 显示“数量”，但不进入
  `NpcIdentityView` 的 actor 语义档案；对 actor 决定近似死字段。
- `npc_initiated_events` 的行为性字段（initiative kind、summary、weight、outcomes）在权重短路
  拆除后没有生产语义调用点；只剩加载校验、地点候选和历史/测试价值。
- reviewed identity、privacy、location、windows、weekdays、context tags 都有生产 reader，
  不是死字段。

## 4. 设计判断：稀疏机会 + 真实决定

用户的初步判断成立，而且比“所有 NPC 每一拍都想一次”更接近真人社会：

```text
很多 NPC 存在
  → 本拍只有极少数人有进入她生活的机会
  → 记录一次机会分配
  → 只让被选中的一个 NPC 真正决定
  → 已接受的意图进入事件机，之后自然推进
```

这符合 ADR 0010：

- 随机性只决定机会、时机和注意力对象；
- NPC 的 motive、态度、是否行动、行动内容由 NPC actor 决定；
- World Author 只裁决外部可行性和不确定结果；
- 她如何评价、是否生气、是否告诉用户仍归 Character Interior；
- 权限、来源、CAS、effect-once、隐私和预算继续由确定性代码守。

权重表的问题不是“用了概率”本身，而是表内的“借书/拉去讲座/意见不合 + 固定 outcome”已经替
NPC 决定了行为语义。它是剧情候选库。相反，“今天下午，合资格的徐青禾获得一次考虑机会”
没有决定她想什么，因此不是剧情库。

## 5. 稀疏到多少

建议起始目标是 **每周 5–8 个新 NPC 决定机会**：

- 常态约 0–1 次/天；
- 有新的 shared settlement、到达长期计划的真实分叉点时可出现第 2 次；
- 不按“NPC 人数 × tick”展开；
- 已接受计划的 start/settle/complete 不占新决定机会。

依据：

1. 当前生活 cadence 的 ambient 档是 45m、2h、4h、6h、8h，若每次都调 NPC，均值约
   4.1h，即接近 6 次/天，明显过密。
2. 她通常一天只需要 1–3 个可辨认生活变化；NPC 外力占其中少部分即可让世界像有别人存在。
3. 不是每个 NPC 决定都会 materialize，也不是每件 material event 都会被她告诉用户。因此
   5–8 次机会/周通常只会留下少量可见社会纹理，不会把聊天变成 NPC 新闻流。
4. “他是否在线”不应决定 NPC 有没有生活；只影响她是否以及何时表达。NPC 世界可在离线时推进，
   但用户回来时 Present 只取来源闭合的最近后果。

实现上不要先写死“每日 1 次行为配额”。复用 recorded draw：

- ambient life wake 先以约 15%–20% 质量打开 NPC decision occasion；按现有约 6 wake/日，
  初始落在约 0.9–1.2 次/日；
- fresh NPC-visible settlement 可直接打开一次 source-bound occasion，但与同日 ambient
  合并/替换，不能无限叠加；
- 到期 NPC Plan 直接进入 continuation，不走 occasion draw。

频率是可审计运维参数。上线观察 14 天后按“实际 actor calls / materialized NPC effects /
重复噪声”调机会质量，不按模型选择了多少 `no_op` 去惩罚它。

## 6. 哪些状态可以确定性演进

判据：**如果下一步已经被一个 accepted intent、冻结 outcome envelope 或硬时钟事实完全授权，
且不需要回答“这个 NPC 现在想不想”，就不应再调 actor 模型。**

可以确定性推进：

1. 已接受 future NPC Plan 到点：planned → active。
2. 已激活 occurrence 已有冻结候选与 recorded draw/结算证据：committed → active → settled。
3. occurrence settled 后对应 NPC Plan：active → complete。
4. 时窗已客观关闭且不存在可执行分支：标记 missed/expired（若“要不要改约”是新意图，另开一次
   稀疏决定机会，不能自动改约）。
5. active/retired、地点 capability、biography gate、时窗可用性等客观状态。
6. settled shared event 数、last shared time、active plan refs、最近共同经历等 Projection。
7. 已接受 intent 的 effect-once、CAS、receipt、重放和来源闭包。

仍需模型：

- 形成新目标、改主意、取消仍可履行的计划、主动邀请、是否介入；
- 对新共同事件形成主观关系变化；
- 前提改变后要不要坚持、调整或放弃；
- World Author 需要为新的 immediate proposal 产生真正不确定的外部 outcomes。

确定性代码可以发现“出现了重新决定的机会”，不能替 NPC 选“坚持/取消/改约”。

## 7. 一次 NPC 决定的最小档案

当前实现其实已经接近正确形状，它没有读取主角完整 Inner Life Snapshot。建议把它正式命名为
NPC Actor Profile，并只保留：

1. **我是谁**：稳定 identity descriptor、actor ref、当前 lifecycle。
2. **我和她的来源闭合历史**：最近 3–4 条共同 settled experience、最后共同时间。
3. **我上次留下的自己**：actor-authored inner state、最多 4 个 active goals、对她的 directional
   social variables。
4. **悬着的事**：我拥有/参与的 active plans、pending intent、未结 occurrence。
5. **现在**：Logical Time、当前地点、这次可用的少量地点/参与者 capability。
6. **硬边界**：可引用 source refs、privacy、允许 now/later、计划时窗与 effect 限制。

明确不放：

- 她的 Affect、Private Impression、对用户的关系和完整 Character Interior；
- 用户聊天全文；
- 其余 37 个 NPC 的状态；
- `npc_initiated_events` 的故事 summary/outcome 菜单；
- “更应该联系/更应该冲突”之类 advisory。

## 8. 成本估算

### 8.1 静态实测

用当前 `NpcActorDecision` JSON schema、当前 system prompt、一个含身份/1 条共同经历/1 个 goal/
1 个 active plan/关系八轴的徐青禾档案：

- actor：6,880 字符，约 1,810 input；典型 proposal 约 196 output；
- World Author（later plan）：2,998 字符，约 788 input；短 accept 约 9 output。

按仓库 2026-08-17 DeepSeek Flash 价格：

- actor：闲时 70% hit 约 ¥0.00176；冷缓存闲时 ¥0.00360；冷缓存高峰 ¥0.00719；
- world later adjudication：约 ¥0.00042–¥0.00245；
- immediate outcome 会比 9 output token 长，预算时按 world ¥0.001–¥0.005 较稳妥。

相比今天 `life_ecology_core` 的 7.3k–9.1k sliced prompt，NPC actor 的 1.8k 更小，因为它不需要
她的完整主观世界。当前实现已经证明“单 NPC 私有 capsule”是可行 seam；不需要复制
BackgroundContextProfile 的 protagonist material，只需复用它的“purpose-specific provider view +
稳定前缀 + 审计覆盖”方法。

### 8.2 月成本

推荐频率按 30 天计算：

- 30 次 actor（约 1/天）；
- 预算场景假设其中约 1/3 提出新 material intent，于是约 10 次 world adjudication。这个 1/3
  只是成本情景，不是生产行为配额。

结果：

- 良好缓存/闲时：约 **¥0.06–¥0.12/月**；
- 全冷缓存/高峰：约 **¥0.24–¥0.35/月**；
- 2 次/天的上界场景：约 **¥0.12–¥0.70/月**。

即使留 2 倍工程余量，也远低于现有 NPC+社会 ¥8 月桶。真正的成本风险不是 NPC 数量，而是：

- 把共享 life cadence 的每个 wake 都变成 actor call；
- 非法输出后自动再调；
- propose 后又叠 world correction 和 1–2 次 prose critic；
- 没有 `model_call_scope`，预算系统看不见该 lane。

当前 `_run_model_with_one_reselect` 最多两次，world accept 还可能触发 critic/retry；这不是上述
“一次决定”的最小成本形状。历史 61 attempt 的失败分布说明应优先 Postel 宽进、简化 wire 和
精确失败审计，而不是预算两次调用为常态。

## 9. 最小代码改动

这是一个小包，不需要重写 NPC Ecology：

1. 在现有 Life Ecology wake 与 `NpcEcology.advance_once()` 之间加一个 NPC-specific、recorded、
   replayable occasion gate；已到期 Plan 和 pending world adjudication绕过新决定 gate。
2. 将“continuation”与“new decision”分开：已有 Plan/occurrence 确定性推进；只有 new decision
   才调用 actor。
3. 给 actor/world 增加独立 metering purpose（如 `npc_actor_decision`、
   `npc_world_adjudication`）和预算日/月统计。
4. 沿用现有单 NPC capsule，但给 profile 加固定静态体量基线；不接主角
   `BackgroundContextProfile` 的素材。
5. 删除 `npc_ecology.py` 内 `_weighted_actor_decision`、`_weighted_world_decision` 及相关
   production imports。它们没有调用点，留着只会误导下一个维护者。
6. `npc_initiative_weight_policy.py` 若历史分析/关系测试仍需，可暂留为明确 retired module；
   若测试迁走后无 consumer，再删除。不要把它重新接成 fallback。
7. 把 `npc_initiated_events` 拆成 capability-only 数据与 legacy story fixture；生产 actor 只读
   capability，不读 summary/weight/outcomes。根级旧 `npcs:` 可在单独的 seed 清理中删除，
   不与本次行为改动绑成大迁移。

预计主要触及 occasion/trigger、`npc_ecology.py`、purpose/profile 注册和针对性测试，约 3–4 个
生产模块；无需新增 authority、账本表或第二套 NPC Interior。

## 10. 连续性风险与控制

### 风险 1：稀疏后像随机噪声

不能从 5 个 active NPC 中无条件均匀抽一个，再让它凭空行动。先编译“合资格机会集”：

- 当前可出现；
- 有新 shared evidence、active/pending plan、未结 matter，或确有 ambient opportunity；
- 来源与地点 capability 闭合。

随机只在合资格集内分配注意力，并做 starvation prevention；它不按“关系越亲就越该联系”映射行为。

### 风险 2：同一个 NPC 前后不像一个人

连续性不靠多调模型，靠同一个 actor-scoped ledger state：

- 每次决定重读该 NPC 上次 `inner_state`、goals、directional relationship；
- 重读最近共同 settled experience、active plan、pending intent；
- 新结果必须引用这些 source refs；
- 接受后写回同一 `NpcSubjectiveState`，而不是新建自由文本人物简介；
- plan/occurrence 的完成结果回到下一次该 NPC capsule。

“记得上次的事”应由 source-bound shared history 保证，不由 prompt 说“请保持一致”。

### 风险 3：旧目标永久黏住

goal 不能仅追加。actor 在新决定时应能保留、修改或结束自己的 goal；系统只做结构与来源校验。
长期没有新机会时，可确定性标记“多久未复核”，但不能自动把目标改成冷淡、放弃或更想念。

### 风险 4：一次机会变成调用瀑布

默认 one-shot；非法 wire 记录技术失败并稍后重试。若保留一次 constrained reselect，它必须计入
同一个 opportunity 月桶，并用历史 invalid rate 作为告警。critic 只守真正的 user-channel
authority；能用已有确定性字段 `user_channel_completion=none` 闭合的，不再额外买 prose 判断。

### 风险 5：NPC 抢走主角的戏

NPC 只产生世界 stimulus。它不能写她的 Appraisal/Affect，不能替她给用户发话，也不能决定她
是否在意。settlement 之后仍由唯一 Character Interior 消费。

## 11. 最小验收

上线后观察 14 天，只验机制，不给角色行为配额：

1. actor calls 为 5–8/周量级，且与 active NPC 总数不呈线性倍增；
2. `npc_actor_decision` usage 与 model audit 一一对应，成本可见；
3. 至少出现 no_op、new Plan 或 immediate proposal 中任一种合法结果；技术失败不冒充 no_op；
4. 已接受 Plan 的 start/settle/complete 不产生第二次 actor call；
5. NPC Ecology occurrence 能经 aftermath 到她的 Character Interior；
6. 同一 NPC 的下一次 capsule 精确含上次 state/goal/settled history；
7. `npc_initiated_events` 固定 summary/outcome 不进入 actor request；
8. 14 天 NPC lane 成本低于 ¥0.50；超出时先降低 occasion 频率，不削 actor 档案。

成功不是“每周必须吵架”或“每个 NPC 都要出场”。成功是：世界偶尔由另一个有连续状态的人先动，
其行为不是程序从剧情表里替他选的，而成本仍只按少量真实决定次数增长。
