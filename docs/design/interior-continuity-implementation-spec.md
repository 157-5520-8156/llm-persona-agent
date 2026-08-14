# 内心连续性与生活丰富度：施工说明书（H12–H16）

状态：说明书（2026-08-14）。**这不是第三份路线图。**

从属关系：本文是 [`harness-restructure-execution-plan.md`](./harness-restructure-execution-plan.md) 的下级工单，
只展开 H12–H16 五个包的施工细节。业务口径以
[`girl-agent-design-intent.md`](./girl-agent-design-intent.md) 为准，施工顺序与验收门以执行计划为准。
本文与那两份冲突时，以那两份为准，并且应当先改本文。

读之前必须先读：`AGENTS.md`（受控高随机宗旨）、`CONTEXT.md`（术语）、
[`ADR 0010`](../adr/0010-controlled-high-variance-character-agency.md)、执行计划的 §3（唯一 seam）、
§4（G1–G8 门）、§14（交付模板）、§15（必须交回用户的事项）、§16（明确不允许）。

### ADR 的可信度：**除 0010 外，一律当历史证据读，不当现行决定用**

`docs/adr/` **整个目录自 2026-08-12 之后再没有改动过**（`git log -- docs/adr/`）。
而 H1–H11 那一波全部发生在之后，其中 H1d 删掉了整条模型审查车道。
**因此 ADR 的 `status: accepted` 不代表它现在还成立。**

2026-08-14 逐条对代码核实的结果：

| ADR | 文件里写的状态 | 代码实际 | 怎么用 |
| --- | --- | --- | --- |
| 0010 受控高随机 | accepted | 宗旨，`AGENTS.md` 同源 | **必读，唯一无条件有效** |
| 0004 钉住回合组合 | accepted | `pinned_turn.py` 活跃 | 有效 |
| 0014 表达前私有回合状态 | accepted | `present_prompt.py:321-323,377` 活跃 | 有效，H14 直接依赖 |
| 0016 统一角色内心 | accepted | `CharacterInterior` 三入口 | 有效 |
| 0011 传记人生弧 | accepted | `LifeArcProjection` / `context_tags` 在，但 `life_arc_effect` **0 条生产者** | 机制有效，内容为空——这正是 H15c |
| 0008 生活生态单一唤醒 | accepted | `LifeEcologyRuntime` 在，但 H2 已把唤醒从「每次心跳」改成 due 驱动 | **唤醒方式一节已过时**，模块边界仍有效 |
| 0015 顾问端点与单元流 | accepted | 符号仍在 `deliberation.py` / `inbound_author.py` / `inbound_wire.py`，**未复核是否仍在生产路径** | 引用前自行核实 |
| 0001 模型主导表达 | **`proposed`，从未 accepted** | 方向已被 0010/0016 吸收 | **不得当决定引用** |
| 0012 开放式人生发展 | accepted | `b060d961` 改成恒 `no_op` | **事实上已被推翻，无 supersede 记录** |
| 0017 携带证明的选择性来源审查 | **「唯一生产可见聊天路线」** | `structured_source_review_model.py` / `source_review_authority.py` / `visible_source_review_model.py` **三个文件全部已删** | **完全作废，最危险的一条** |

**ADR 0017 是本次施工最可能踩的坑。** 它自称是「唯一的生产可见聊天路线」，
一个不知情的实施者读到它会以为携带证明的来源审查还在，从而在 H13/H14 改表达路径时
试图保留或恢复它。**执行计划裁决 7 已经删掉了整条车道，不得恢复。**

**ADR 0012 的偏离要补记录。** H15 若恢复 generative 路径，应当同时补一条 ADR 说明最终形态
（Occasion 门控 + one-shot），而不是让代码和 ADR 继续不一致。
若决定不恢复，也应把 0012 标记为 superseded。

### 现行事实以什么为准

优先级从高到低：

1. **代码**（本文所有坐标都在 2026-08-14 逐条核实过）。
2. [`configs/mechanism_closure.yaml`](../../configs/mechanism_closure.yaml)——机器可读的证据索引，
   仍在维护，比 ADR 新。它给出每个机制的 `status` / `runtime_activation` / `limitations` /
   `delayed_trigger_ids` / 对应测试。**遇到「这个机制到底通不通」先查它。**
3. 执行计划 + 设计总纲。
4. ADR：只当历史证据，按上表。

另注：执行计划 H1d 说要连带删除 `world_v2/life_review_identity.py`，
**实际保留了**（仍被 `life_development_runtime.py` / `life_development_source_closure.py` /
`batch_invariants.py` 引用）。这是计划文本与落地的一处小偏差，不是待办。

行号以 2026-08-14 的 `worktree-fix-cost-optimization` HEAD 为准，**改动前必须先确认**。

---

## 0. 这五个包要解决什么

用户的判断（2026-08-14）：内心「似乎成本较高也不够连续」，而且 §12.9 第 8 条把角色的生活变成了剧情库。
经账本与代码核实，这两句都成立，而且根因是同一条：

> **系统在为「想」付钱，但「想」的产物没有变成她下次能读到的状态；
> 而世界的内容面是一张有限的预写表，加行数只能提高密度，不能提高不可知性。**

因此这五个包的总方向是：

> **不再为「想」单独付钱，改成在她已经付费的每一次开口上多收一样东西；
> 世界只决定机会，内容由她顺手产出，偶尔硬化成一条改写规则表的弧。**

H12/H14/H16 不新增任何模型调用，H14 是负成本，H13 只在一种稀有情形新增机会，
H15 把 world-author 从「每次醒一次」降到「偶尔硬化一次」。**总账是减少调用。**

## 1. 立项证据（只读实测，2026-08-14）

### 1.1 内心车道的产出

归档 `data/companion.sqlite`（2026-07-24 – 08-13，只读）与新纪元 `data/companion.epoch2.sqlite`：

| 她开口时该读到的 | 快照槽位 | 三周产出 | epoch2 genesis | 判定 |
| --- | --- | ---: | ---: | --- |
| 说过什么（含送达状态） | `recent_dialogue` | 127 观察 / 120 送达 | 未导入 | 在线可读，跨纪元断 |
| 做过什么 | `recent_self_experiences` | `ExperienceCommitted` 41 | **0** | 日期过滤吃光（§2.1） |
| 私下想定了什么 | `private_impressions` | 3,034 次调用 → **7** 接受 | 2 | 车道等于没有产出 |
| 还没了结什么 | `unresolved` / `open_threads` | `ThreadOpened` **1** | 1 | 开环几乎不存在 |
| 在等什么回应 | `response_expectation` | **0 / 106** 已接受计划 | — | 机制齐备，从未声明 |

唯一还活着的是 `AppraisalAccepted` 324 条。**评价是逐时刻的判断，不是可累积的立场。**
所以她读到的「内心」≈ 一串判断加一个情绪强度。

关键澄清（不要重复走弯路）：**编译与传输没有问题。**
`InnerLifeSnapshot.model_view()` 把完整语义内容放在顶层 `materials.*`，`faculties` 只是索引
（`character_interior/contracts.py:736-743`）。空的是喂槽位的车道，不是管道。

### 1.2 成本形状

| 账本 | 区间 | 调用 | 已结算 |
| --- | --- | ---: | ---: |
| `companion.sqlite` | 08-07 – 08-13（UTC） | 943 | ¥13.94 |
| `companion.epoch2.sqlite` | 切库后约 9 小时 | 10 | ¥0.072 |

epoch2 明细：`inbound_turn` 2 次成功 ¥0.026（真回复）；`proactive_contact` 3 次成功计费 ¥0.029
但 **0 条送达**（审计 `primary_timeout` / `budget_exhausted`，provider 仍出账）；
`world_stimulus_appraisal` 1 次 24,547 token ¥0.017。缓存命中仍 **23.1%**。

**预算不是约束。** 执行计划 §12.5 的预期是 ¥15–29/月对 ¥100 信封，空出 ¥60–80。
当初贵不是因为世界丰富，是因为买了大量校验重试、审查和印象农场。

### 1.3 生活内容面

`configs/world_seed.yaml:88-91` 自己写着：

```yaml
    story_candidate_role: legacy_replay_and_fixture
```

| 内容源 | 条目 | 是不是生产车道 | 原因 |
| --- | ---: | --- | --- |
| present openings | 34 / 82 outcome | 否 | `legacy_replay_and_fixture` |
| future openings | 9 / 19 outcome | 否 | 同上 |
| aspiration seeds | 7 | 否 | `AspirationWeightPolicy` 仅测试引用 |
| NPC 主动事件 | 8 / 16 outcome | 是（加权表） | `b060d961` 之后 0 模型 |
| aftermath outcome | 每 opening 2–4 条预写 | 是 | 抽已写好的字符串 |
| `life_arc_effect` | **0** | 否 | 目录里一条都没有 |

长弧机制**已经存在**：`LifeArcProjection.context_tags` 通过 opening 的 `requires_all_context_tags` /
`context_affinity_bp` 门控资格——这就是「弧改写表」。但开弧只有两个入口：
reviewed outcome 带 `life_arc_effect`（目录 0 条），或 generative life development 的
`dynamic_life_arc_context`（`b060d961` 之后恒 `no_op`）。**两扇门都关着。**

结论：「实习改写接下来几个月」不是没设计，**是没有生产者**。

---

## 2. 全局约束（违反即退回，不接受「测试绿」抗辩）

1. **宗旨优先**（`AGENTS.md`）。系统只能决定**什么时候给她一次机会**和**她面前摆着什么事实**；
   不得决定她的动机、情绪、措辞、是否追问、是否主动、是否沉默。
   判据：这条改动约束的是「什么时候让她想」，还是「她能不能想这件事、能不能说」？后者一律不许。
2. **禁止再问一次模型**（裁决 7 / §12.10）。不得新增审查、自证、复核、重选、清单探针。
   需要更强保证只能加确定性检查。
3. **§12.10 保留清单一行不动**：`life_development_source_closure.py`、`isolated_source_closure_trace.py`、
   `private_self_expression_audit.py`、`proposal_audit.py`、`batch_invariants.py`。
4. **不新增第六种 Occasion，不新增 model-bearing purpose**。需要就停下来交回用户（§15）。
5. **G1–G8 全部适用**。特别是 G6（无孤儿产出）：每个 model-bearing purpose 必须能指出
   它的产出出现在 Present 的哪一段；指不出就删。G7：机会只能从本步新接受的事件派生，且带 expiry。
6. **Producer-First Authority**：新 authority 必须和第一个生产者同批落地（见 `CONTEXT.md`）。
7. **每包 diff 目标 ≤300 行，独立可回滚，按 §14 交付模板留证。**
8. **不得用本地模板冒充她说话。** 一次不成记技术失败、丢掉该机会，等下一个。
9. **不得为省钱削上下文、削记忆、让她失声，或把技术故障记成她选择沉默。**

### 2.0 ⚠️ 你编辑的就是正在运行的生产代码

**这不是一棵开发分支的树。** launchd 的两个生产服务直接执行本 worktree 里的脚本：

```
com.girl-agent.daemon → .claude/worktrees/fix-cost-optimization/scripts/run_production_daemon.sh
com.girl-agent.napcat → .claude/worktrees/fix-cost-optimization/scripts/run_production_napcat.sh
```

后果，逐条记住：

1. **改这里的 `src/` 就是改生产。** 进程重启（崩溃重拉、`launchctl kickstart`、机器重启）后立即生效。
   不要把「还没测完的中间状态」留在工作区过夜。
2. **`scripts/run_production_daemon.sh` 与 `run_production_napcat.sh` 曾经是未跟踪文件。**
   `git clean -fd` 会直接弄死生产。任何清理操作前先确认它们已提交。
3. **不要 `git checkout .` / `git stash` 整棵树**去「回到干净状态」。
   先看清楚工作区里有什么（见下条）。
4. 需要隔离验证时用 `scripts/run_isolated_daemon_acceptance.py`（双进程真实链路），
   不要直接拿生产进程当试验场。

### 2.0.1 开工前必须先确认工作区基线

2026-08-14 交接时，工作区有 **387 行未提交的 `epoch_continuity.py` 改动**
（外加 `start_world_v2_epoch.py` 与 `test_epoch_continuity_h10.py`）。
那是 H10 新纪元的实质工作：transition projection 补齐、语义指纹重算、evidence 重绑。
**当前运行中的 `companion.epoch2.sqlite` 很可能就是这份代码产出的**，
丢掉它等于丢掉说明书 §2.1 所依赖的「快照可重算」回滚保障。

**开工第一步**：`git status --short` 与 `git diff --stat`，确认这些改动**已经提交**。
如果还没提交，停下来问用户，不要在未提交的 387 行之上再叠自己的改动——
H12a 要动的 `_updated_at()` 正好就在这个文件里。

### 2.1 运维前提（必须先读，影响 H12a 的实际效果）

生产当前跑在 `data/companion.epoch2.sqlite`。

- **`rebuild()` 不能用来追溯修复**：它在 replay ≠ persisted head 时 fail-closed。
- 因此 H12a 修好编译器之后，**当前生产库里的 41 条历史经历不会自动回来**。
- **用户已裁定（2026-08-14）：那 41 条丢了无所谓，不为此再切一次纪元。**
  H12a 只需保证**修复之后新写的经历**能正确进入连续性快照与 Present。
  **不要**为了追回历史而提议切库、rebuild 或改写归档——这条已经问过了，答案是不做。
- 归档 `companion.epoch1.sqlite` / `companion.epoch1.rerun.sqlite` 只读，禁止删除。
- 原始 `data/companion.sqlite` 是切纪元前的文件，**禁止写入**。

---

## 3. H12 — 补上已付费但没送到的连续性（0 模型）

**一句话**：这一组不产生任何新调用，只是让已经花掉的钱到达她。

### H12a 经历在新纪元被日期过滤吃光

**现象**：归档 41 条 `ExperienceCommitted`，`epoch2` genesis 的 `continuity.experiences` 长度为 0。

**⚠️ 这个文件有未提交改动，先读 §2.0.1。** `_updated_at()` 已经被那份未提交的 diff 动过
（加了 `_as_utc()` 时区归一化，`datetime.min` 改成带 tzinfo），**但仍然没有读 `occurred_to`**，
所以本条 bug 依然存在。行号以工作区当前状态为准。

**根因**（已核实）：

- `world_v2/epoch_continuity.py:120-128` 的 `_updated_at()` 依次找
  `updated_at` / `committed_at` / `accepted_at` / `opened_at`，顶层和 `.values` 都找。
- `ExperienceProjection`（`world_v2/schemas.py:2205-2221`）顶层只有
  `experience_id` / `entity_revision` / `authority_contract_version` / `semantic_fingerprint` /
  `values` / `origin` / `status`——**没有任何上述日期字段**。
- `ExperienceValues`（`world_v2/schemas.py:2155-2182`）只有 `occurred_from` / `occurred_to`。
- 于是 `_updated_at()` 返回 `datetime.min`，被 `compile_continuity_snapshot`
  （`epoch_continuity.py:164-168`）的 `_EXPERIENCE_WINDOW = timedelta(days=30)`（`:49`）全部滤掉。

**改动**：`_updated_at()` 增加 `occurred_to` → `occurred_from` 回退。

**必须同时确认**：`_updated_at` 还被 facts / memory_candidates 的排序使用
（`epoch_continuity.py:143`、`:147`）。这两类有 `committed_at` / `updated_at`，
新增的回退**不得改变它们现有的排序结果**。红测要覆盖这一点。

**红测**（写在 `tests/world_v2/test_epoch_continuity_h10.py` 或新建 `_h12.py`）：

1. 构造一个 `occurred_to` 落在 30 日窗内的 `ExperienceProjection`，
   断言 `compile_continuity_snapshot(...).experiences` 非空。
2. 构造 `occurred_to` 在窗外的，断言被滤掉（窗口语义没坏）。
3. 断言 facts / memories 的排序在改动前后一致。

**验收**：对 `companion.epoch1.sqlite`（只读）重新编译一次连续性快照，`experiences` 应为 24 条上限内的非空值。

**剩余缺口（必须写进交付）**：当前生产库不会追溯拿到这些经历，见 §2.1。

### H12b 期待 advisory 是全局截断时第一个被赶走的

**现象**：`response_expectation` 编成的是 advisory
（`world_v2/response_expectation_view.py:264-290` `response_expectation_advisory`，
`kind="response_expectation"`）。

**根因**：`world_v2/context_capsule.py:170-190` `RANK_DOMAIN_IMPORTANCE_BP`：

```
character_core / current_situation 10,000
relevant_facts                      9,000
recent_dialogue                     9,500
relationship_slice / appraisals / affect_episodes 8,500
open_threads / perception_results / private_impressions 8,000
active_memory_candidates            7,500
world_life                          7,250
recent_experiences                  7,000
available_capabilities / action_budget 6,000
advisories                          5,000   ← 最低
```

全局超 `hard_max_characters`（100,000）时按最低 `rank_score_bp` 逐出
（`context_capsule.py:2174-2187`，另见 `:2224`、`:2243`）。
所以**即使她声明了期待，预算一紧张它第一个掉**。
`recent_experiences` 7,000 低于 `active_memory_candidates` 7,500 也说不通——
「她做过的事」不该排在「记忆候选」后面。

**改动（推荐方案，避免新增 slice）**：

1. 在连续性下限（floor，`context_capsule.py:2119-2134` 一带）里给
   `kind == "response_expectation"` 的 advisory 一个保底，使其不被 tier-1 逐出。
2. `recent_experiences` 提到 `active_memory_candidates` 之上（建议 7,750；
   不要越过 `open_threads` 8,000）。

**不要做**：为期待新增一个 `SliceName`。那需要 Producer-First Authority 与
`vertical_registry.assert_bounded_vertical_coverage` 覆盖，成本远高于收益，且属于 §15 的新增 authority。

**红测**：构造一个超 100k 的 capsule + 一条 pending expectation advisory，
断言逐出之后该 advisory 仍在；断言 `recent_experiences` 不再先于 `active_memory_candidates` 被逐出。

**注意**：`RANK_POLICY_DIGEST`（`context_capsule.py:193`）由这张表哈希得到，
改表会改 digest。确认这个 digest 是否进入任何 replay 断言或 golden 夹具；若进入，
按现有 migration golden 的做法处理，**不得**为了让测试过就把断言删掉。

### H12c `delivery_state` 没有 `unknown`，她可能不知道自己说过

**现象**：`world_v2/recent_dialogue.py:60`

```python
    delivery_state: Literal["observed", "provider_accepted", "delivered"]
```

companion 气泡只在有 visible receipt 时进对话（`recent_dialogue.py:372-374`、`:433-444`）。
Action 停在 `ActionUnknown` 时，**那条消息可能根本不出现在她的对话史里**。

这直接把执行计划 §7 的反重复根因放回来了：看不见自己说过什么的人会问第三遍。
（H1c 已让 `unknown` 不再是终态并加了对账，但对话可见性这一侧没跟。）

**改动**：

1. `Literal` 增加 `"unknown"`。
2. 让处于 unknown 的 companion 气泡进入对话材料，文本照旧，状态**如实**标 unknown。
3. **可读 ≠ 可引用为事实**：`character_interior/inbound_wire.py:2093-2111` 的 proof 侧
   目前只在 `delivery_state == "delivered"` 时给 companion proof，**保持严格不放宽**。
   unknown 的气泡只能作为她的对话材料出现，不能成为「我已经告诉过他」的事实来源。

`snapshot_compiler.py:730-748` 已经透传 `delivery_state`，不需要改。

**绝对不许**：把 unknown 显示成 delivered，或反过来把它藏起来。两者都是替她判断。

**红测**：Action 落到 unknown 之后编快照，断言（a）气泡在，（b）`delivery_state == "unknown"`，
（c）source proof 不把它当 delivered。

### H12d 删掉一条死路

`character_interior/snapshot_compiler.py:717-719` 读取 slice
`recalled_emotional_associations`，但该名字不在 `context_capsule.py:58-75` 的 `SliceName` 里，
生产也没有 installer，**恒为空**。

按 G6（无孤儿产出）处置：**删除该读取**。
（不建议补 slice：H9 的 recency×importance×relevance 打分已经覆盖召回侧需求。）

### H12 验收

- 上述四条红测全绿。
- `uv run python scripts/test_fast.py --tier character` 全绿；ruff 绿。
- 模型调用数与账单**不变**（本包 0 模型）。
- 生产证据：重新编译一次连续性快照并贴出 `experiences` 长度；贴出一次超预算 capsule 的逐出结果。

---

## 4. H13 — 期待与沉默的语义（≈0 新增调用）

**背景（用户原话，2026-08-14）**：真人对自己发出的消息有期望效果；用户不回，
她可能根据心情接着说，或者因为不回话不高兴。这是当初设计内心的原因之一。

**已核实**：整条机制都在，从未被喂过。

| 环节 | 位置 | 状态 |
| --- | --- | --- |
| 她声明 | `world_v2/expression_draft.py:264-277`、`:334-335` | 可选字段，模型从不填 |
| 冻成权威 | `world_v2/schemas.py:1161-1181` `ResponseExpectationAuthority`；`expression_plan_acceptance.py:420-432` | 通 |
| 挂在计划上 | `world_v2/expression_plan_manifest.py:128` | 通 |
| 编成材料 | `world_v2/response_expectation_view.py:71-183`、`:240-290` | 通 |
| 入站顾问 | `world_v2/pinned_turn.py:1229-1260`；生产开关 `production_turn_application.py:3623` | 通 |
| 事后评估 | `world_v2/runtime.py:802-878` `ResponseExpectationAssessed` | 全库 1 条 |

**同时已核实**：沉默其实有**两条锚点不同**的车道，不要重复造：

- `silence_appraisal`：锚在**她自己最后一条可见回执**（`world_v2/silence_appraisal_trigger.py:54-117`，
  消费点 `character_interior/world_stimulus.py:118-132` 的 `unanswered_visible_expression`，
  装配 `character_interior/production.py:903-907`，默认 idle 3600s
  见 `production_turn_application.py:784`）。
- `spontaneous_contact` / `ambient_presence`：锚在**他最后一条消息**
  （`world_v2/social_initiative.py:1102-1216`）。

所以「他没接我的话」和「最近没事」在**结构上分得开**。缺的是**语义**：
没有 `response_expectation`，`silence_appraisal` 对「我问了他一句要紧的」和「我说了句嗯」一视同仁。

### H13a 让她能声明（在已付费的那次表达上加一个可选字段）

她实际在用的是 slim 契约。位置：

- `world_v2/present_prompt.py:234-244` `_SLIM_CONSIDER_KEYS`
  （`messages` / `felt` / `stuck_with_me` / `wants` / `photo`）
- `:286-349` `compile_slim_consider_payload`
- `:352-412` `compile_slim_interior_envelope`
  ——**`response_expectation` 与 `response_expectation_assessment` 在 `:385-386` 和 `:402-403`
  被硬写成 `None`**。

**改动**：

1. `_SLIM_CONSIDER_KEYS` 增加可选键 `waiting_for`（短字符串）。
2. 在 `compile_slim_consider_payload` 里把它编译成 `ResponseExpectationDraft`
   （`expression_draft.py:264-277`）。注意该 draft 有 `expiry_follows_wait` 校验（`:274`），
   `wait_seconds` → `not_before`、`expires_after_seconds` → `expires_at` 必须自洽。
3. `hoped_response` 的长度上限见 `response_expectation_view.py:62`（`max_length=128`），
   slim 侧要按同一上限裁剪，不要让编译期抛异常吃掉整个回合。
4. `pressure_bp` / `importance_bp` 在 slim 里没有来源。**默认取中位（5,000）**，
   并在说明里写清这是系统默认而非她的判断。**不要**用关键词或标点去推断强弱。

**现成的形状定义**：`world_v2/structured_expression_reselection_model.py:942-953`
（H1d 明确保留、要复用的严格 schema 构造器）**已经内建了 response expectation 的校验**：
`expires_after_seconds` 必须是 60–172,800 的整数，且与 `wait_seconds` 有先后约束。
接 `waiting_for` 时复用这里，不要另写一份形状。

**G4 核对（硬门）**：slim 目前 5 个字段，加 `waiting_for` = 6，仍 ≤8。
但必须用 `present_prompt.json_schema_g4_metrics`（`:191-214`）对 compact gate 的实际 schema 复核
必填 ≤3 / 总字段 ≤8 / 深度 ≤2。**超限即停，交回用户**（§15）。

**铁律**（`expression_draft.py:265` 已写明 *never inferred from punctuation*，不要退化）：

- 完全可选。她不给就是没有期待，**不许**从问号、句式或关键词推断。
- 期待不该只挂在问句上——「我跟他说了一件我在意的事」同样带期待。这由她判断，不由代码判断。

### H13b 沉默带上语义

`response_expectation_view.pending_response_expectation()` 已经支持**精确锚点链**：
`anchor_event_ref`（一条 `ExecutionReceiptRecorded`）→ action → manifest beat → 该 manifest 的冻结期待
（`response_expectation_view.py:109-140`）。

**改动**：`silence_appraisal` 那次 consider 的材料里带上
`pending_response_expectation(projection, anchor_event_ref=<该次沉默锚定的回执>)` 的结果。
她因此读到：「我当时希望他 X；压力/重要性；等了 N 分钟；送达状态 Y」。

系统只摆事实。**是不是不高兴、要不要接着说、要不要放下，全部由她决定。**
`response_expectation_advisory` 现在就是这么写的（只给语义值，不给 ID/哈希），保持这个形状。

### H13c 他回话时评估一次（这才是「越想越气」的正当来源）

`ResponseExpectationAssessed` 的写入路径已在 `runtime.py:802-878`，
draft 形状在 `world_v2/proposal_envelope.py:1165-1171`
（状态 `fulfilled` / `superseded` / `still_pending` / `uncertain`）。
门控参数 `response_expectation_assessment_required` 已存在于
`character_interior/inbound_tool_contract.py:833`、`:943`、`:1136`。

**改动**：有 pending expectation 时，入站回合允许（并在契约里说明）她给出一次 assessment。
slim 侧同样加一个可选键（或复用 `felt`，由实施者按 G4 余量决定）。

**这一次评估搭在她本来就要做的那次 inbound 上，0 新增调用。**

设计要点：升级来自**比一次期望与现实**，不是让她反复读自己生成的反思链
（后者正是私人印象农场的形状，见执行计划 §12.3）。

### H13d 实现那句只存在于注释的抑制

`world_v2/social_initiative.py:1168-1170`：

```python
        # A response expectation is the stronger and more specific authority.
        # Do not also manufacture a generic idle opportunity for that expression.
        source_kind = "ambient_presence" if ambient else "spontaneous_contact"
```

注释说了，**代码没做**。于是有 pending expectation 时，`silence_appraisal` 和
`spontaneous_contact` 会同时开火。

**改动**：存在未过期的 pending expectation 时，沉默归 silence 车道，不再另造 generic idle 机会。

**红测**：pending expectation + idle 超时 → 只开一个机会。

**注意**：`social_initiative` 现有的 idle 1800s 与 contact_cooldown 900s 属于**机会节奏**，
执行计划 §7 明确予以保留，**不要**顺手改成次数配额。

### H13e 期待过期了他还是没说话

这正是用户描述的场景。**好消息：这条车道也已经注册了，不需要新建。**

`configs/mechanism_closure.yaml` 的 `situation-context-and-advisory` 机制里列着
`conversation.expectation_expiry`，并且它在代码里是实打实注册的：

- `world_v2/vertical_registry.py:585`
- `world_v2/delayed_trigger_owner_registry.py:294-310`：
  `runtime_owner=SituationCompiler.compile`、`trigger_mode="derived_formula"`、
  `projection_due_fields` = `ResponseExpectationAuthority.not_before` 与 `.expires_at`、
  public seam = `QQC2CHost.inbound_text` / `inbound_fragment` / `tick`。

**`derived_formula` 意味着它不是一条要排期的定时器，而是在每次 inbound / tick 由
`SituationCompiler.compile` 顺带算出来的。** 所以到期判定本身是 0 成本的。

因此 H13e 的实际工作量只是：**到期且仍无回应时，让她有一次机会**——
而不是造一个新的机会类型。

**约束**：**不得新增第六种 Occasion**（§15）。挂在已有的 `unsettled_feeling` 或 `quiet_gap` 上。
必须带 expiry，重启后过期即丢弃、不补做（G7；参考执行计划 §13.1 对 proactive 缺过期判定的说明）。

**同时说明了 H13 的整体判断**：这条延迟触发器、`ResponseExpectationAuthority` 的
`not_before`/`expires_at`、`SituationCompiler` 的接线**全都是为了这个场景造的**，
唯一缺的就是 §H13a——她从来没有机会声明期待，所以这一整套永远算出「无期待」。

### H13 验收

- 生产账本出现 `response_expectation` 非空的 `ExpressionPlanAccepted`（当前 0/106）。
- 出现 `ResponseExpectationAssessed`（当前全库 1 条）。
- pending expectation 期间不再出现重复的 generic idle 机会。
- 每条用户消息的模型调用数不上升（G2 health 字段）。

---

## 5. H14 — 残留改成副产品，删掉独立的私人印象车道（负成本）

**证据**：`private-impression` 车道三周 3,034 次 attempt、**7** 条被接受（0.2%），折算约 ¥45。
H1b 已把形状改宽、H5 已把机会派生收到 head 的 `AppraisalAccepted`（G7），
但**独立 faculty 仍在**，仍然是一次专门为「想」而发起的调用。

**改动**：

1. slim 里已有的 `stuck_with_me` / `wants`（`present_prompt.py:296-307`）
   直接编译成私人印象 / 开环（thread）的更新。
2. **删除独立的私人印象模型调用链**（`world_v2/private_impression_producer.py` 的调用方；
   执行计划 §13.1 H5 行给了坐标：`:513-548` 机会派生、`:101-107` 次数上限、
   `:203-279` draft 校验、`:1034-1067` `no_change` 处理）。
3. 保留 reducer / acceptance / 投影侧（`PrivateImpressionAccepted` 仍是同一条接受链），
   只换生产者。

**这是删一个 model-bearing purpose，不是加。** 符合 §16。

**来源闭包**：从 slim 编译时，`source_refs` 应绑定本回合 pinned capsule 的已知 refs，
与 H4「来源闭包移到她之后」一致；**不要**要求她在输出里自证 ref。

**风险**：印象原有 14 条合取校验里包含「与 anchor appraisal 的 ref 有交集」等约束
（`private_impression_producer.py:246-284`）。从 slim 编译时要么满足，要么按 H1b 的 Postel 精神放宽。
放宽必须是**形状**层面的，不得放宽隐私或来源边界。

**验收**：

- `world_v2_model_usage` 中 private-impression 类 purpose 调用**归零**。
- `PrivateImpressionAccepted` 数量显著上升（基线：三周 6 条）。
- 后台月成本下降（基线：该车道折算 ¥45 / 三周）。

---

## 6. H15 — 生活：开门，内容由她产出，偶尔硬化成弧

**先纠正一个可能的误解**：撤回 §12.9 第 8 条**不等于** `git revert b060d961`。
旧的 NPC 模型链在归档里的表现是 **61 次调用、0 次 validated**
（`main_invalid_output` 33 + `corrective_invalid` 27）。把它原样接回来 = 再买一遍非法输出。

**正确形态是「开门」**：节奏层继续 0 模型，内容层改由她在**已付费的调用**里产出，
长弧层补上生产者。

### H15a 层一：什么时候发生（保持 0 模型，不要动）

`day_skeleton.py` 的日程骨架 + 加权表（`weighted_table.py` / `NpcInitiativeWeightPolicy`）
决定几点、在哪、谁在、有没有事。**这一层是对的。**

判据（写进代码注释以免后人改错）：表决定**机会**，她决定**内容与意义**。反过来就是剧情库。

### H15b 层二：那是什么样的（内容由她产出，0 新增调用）

目标是用户说的「小事件丰富度」——回家路上看见了猫。

**成本铁律：不得为每个微事件单开 provider 调用。** 那只猫不值一次 provider call。

三个候选落点，实施者按 §7 的探针结果择一或组合：

1. **`day_open`**（H8 已有，每天一次，已付费）：给她当日确定性骨架，
   她给出当日质地，包含她注意到了什么。
2. **aftermath outcome 不再只抽预写字符串**：目前 `activity_kind` 匹配到目录里 2–4 条固定文案。
   改成由她给出质地。**但不要为此新开 world-author 调用**，见 §7 的懒求值风险。
3. **`open_world` 微事件车道**：代码里已有，目前因为 open-life 装配而被关闭
   （`production_turn_application.py` 约 4084-4175：`open_life_requested` 时装
   `life_development` + `npc_initiative`，**不装** `open_world_event`）。

**已核实的边界**：activity lifecycle 本身**没有**容纳即兴细节的位置——
操作只改 plan 状态（`world_v2/activity_lifecycle_contract.py:27-33` 的
`EFFECT_BY_ACTIVITY_OPERATION`）。细节只能来自 aftermath / open-world / NPC occurrence。
不要试图把细节塞进 activity 操作里。

### H15c 层三：长期意味着什么（长弧，持续期 0 成本）

**机制已存在，缺生产者。**

- `LifeArcProjection.context_tags` 通过 opening 的 `requires_all_context_tags` /
  `context_affinity_bp` 门控资格 = **弧改写表**。
- 开弧路径一：reviewed outcome 带 `life_arc_effect` → pending biographical settlement →
  `world_v2/biographical_lifecycle_runtime.py:190-233` `_arc_from_effect` → `LifeArcChanged`。
  **`world_seed.yaml` 里目前 0 条 outcome 带 `life_arc_effect`。**
- 开弧路径二：generative life development 的 `dynamic_life_arc_context`（`arc_kind="dynamic"`），
  **`b060d961` 之后恒 `no_op`**。
- 关弧：`ends_at` 到期或终态转移（`complete` / `abandon`）。

**实习那个例子的完整链**（用户给的目标形态）：

1. 申请季由日历/传记确定性打开（0 模型，`biographical_lifecycle` 已有 `term_windows` 之类抓手）。
2. 她决定投不投、怎么谈——这是她的决定，走正常 consider。
3. 已结算的结果硬化成一条 arc（需要 `life_arc_effect` 的生产者）。
4. arc 改写 `context_tags` → 之后几个月的可选项、地点、可达 NPC 全变。
5. **持续期 0 成本**，因为持续作用在表上，不在调用上。

**实施顺序建议**：先做（3）的生产者最小闭环（哪怕先由 seed 里少量 outcome 带 effect 验证链路通），
再考虑 generative 路径。generative 路径涉及重开 world-author，**必须** Occasion 门控 + one-shot +
Postel 宽进，且不得为每次 ecology wake 打模型。

### H15d 撤回 §12.9 第 8 条时的成本信封（用户已认可的约束）

| 层 | 保持 0 模型 | 允许的模型（门控） | 月桶 |
| --- | --- | --- | ---: |
| 世界步进 | 天气、课表、NPC 日常加权表 | 无 | ¥0 |
| NPC actor | 每个 ecology wake | Occasion 到期 + one-shot + Postel | ¥8（社会合计） |
| 世界作者 | 审查 / 纠正 / 每醒一次 | 稀有节拍 one-shot；审查已删，禁止加回 | ¥8 |
| 她的内心 | 扫历史 appraisal、入站后再付一次 24k stimulus | 入站 1 次；未平情绪按 H6 间隔；安静窗口须留下残留或真发出 | ¥12 |

### H15 验收

- 生产出现非目录来源的生活细节（她产出的质地），且该细节能在**下一次** Present 里被读到。
- 生产出现至少 1 条由已结算结果开出的 Life Arc，且能观察到它改变了后续 opening 资格。
- NPC / 世界作者相关的 provider 调用数不回到 `b060d961` 之前的量级。

---

## 7. 必须先验的风险（H15 的前置探针）

**这是整个方案里唯一没把握的地方，必须先验再决定 H15b 的形态。**

设想：occurrence **确定性地提交**（它确实发生了），只有**文本质地**等到她下次开口时才由她给出——
即「世界的事实是即时的，世界的叙述是懒求值的」。

这会产生一段「已发生但还没有质地」的状态，可能与事件溯源的 replay 确定性冲突。

**要验的问题**：reducer / replay 是否允许「先提交 occurrence，之后再补 content excerpt」？

相关坐标：

- `world_v2/world_life_context.py:266-308`：settled occurrence → `WorldLifeContextItem` +
  可选 `LifeContentExcerpt`（按 `occurrence_id` 关联）——**这里已经是可选的**，是个好兆头。
- `world_v2/life_aftermath_runtime.py` 约 `:1007-1099` `_commit_experience`：
  `ExperienceCommitted` 的 summary 文本目前来自已结算 outcome。
- `ExperienceValues.summary_ref` / `summary_payload_hash`（`schemas.py:2156-2157`）是内容寻址的，
  且 `ExperienceProjection` 的 `semantic_fingerprint` 由 values + policy_refs 计算
  （`schemas.py:2214-2220`）——**如果 summary 事后才有，指纹怎么算**是这个探针的核心问题。

**探针交付物**：一个只读实验 + 一页结论，说明「懒求值质地」可行 / 不可行 / 需要什么形态。
不可行则 H15b 退到「只在 `day_open` 与 inbound 里产出质地」，不改 aftermath 的即时提交。

**在探针结论出来之前，不要动 aftermath 的提交路径。**

---

## 8. H16 — Occasion 队列做实（承重墙）

上面每一件都挂在执行计划 §3 的硬规则上：**一个 Occasion 恰好一次 `consider()`，
且一次调用必须留下将来的她还能读到的状态。**

**已核实的现状**：五种 kind 只是枚举，大部分没有真的用 `OccasionIdentity`。

| kind | 定义 | 运行时实际 |
| --- | --- | --- |
| `user_message` | `occasion.py:15-28` | 走 `CausalOpportunityIdentity` 的 `opportunity_ref`（`character_interior/core.py:1369-1371`），**只有这条是真的** |
| `quiet_gap` | 同上 | 只有 expiry helper `occasion.py:79-80`；实际是 QQ ingress debounce + `SocialInitiativePolicy.spontaneous_expiry_seconds` |
| `unsettled_feeling` | 同上 | 实为 `ReflectionScheduler` / `process_kind="life_reflection"` |
| `life_beat` | 同上 | **只有枚举，运行时不存在** |
| `day_open` | 同上 | 实为 `activity_lifecycle_worker.py:199-271` 的 daily store `spent`/`mark` |

另外 `OccasionConsiderGate`（`occasion.py:50-68`）是**进程内 `set`**，重启即失忆。
对 G2 是缺口：跨进程重启后同一 Occasion 可以再考虑一次。
实施者要判断是否需要落到可清理的 durable sidecar（H2 已经为 claim/lease 建了这个模式，可复用）。

**改动目标**：五种都成为真的 `OccasionIdentity`（有 `merge_key`、有 `expires_at`），
G2 / G7 在全部五种上成立，而不只是 inbound。

**这一包应当排在 H13e 和 H15 之前**——它们都需要一个真的机会队列来挂靠。

---

## 9. 建议顺序与依赖

```
H12（0 模型，修泄漏）
  └─ H13a/b/c/d（期待，≈0 新增调用）
        └─ H16（Occasion 队列做实）
              ├─ H13e（期待过期仍无回应，唯一新增机会）
              └─ H15（生活开门）← 前置：§7 探针
H14（删印象车道，负成本）可与上面任何一步并行
```

理由：H12 让已经花掉的钱到达，是所有「她能读到」类验收的前提；
H13 直接对上用户的原始动机且几乎不花钱；
H16 是承重墙；H15 最重且有未验风险，排最后。
H14 独立且是负成本，随时可插。

---

## 10. 验证口径（每包交付必须跑，附结果）

沿用执行计划 §12.7，另加本文专用四条。数据库用运行中的 `data/companion.epoch2.sqlite`（只读打开）。

```sql
-- A. 她是否真的开始声明期待（H13 主指标；基线 0/106）
SELECT COUNT(*) total,
       SUM(CASE WHEN json_extract(payload,'$.response_expectation') IS NOT NULL
                THEN 1 ELSE 0 END) with_expectation
FROM (
  SELECT json_extract(event_json,'$.payload_json') payload
  FROM world_v2_events
  WHERE json_extract(event_json,'$.event_type')='ExpressionPlanAccepted'
);

-- B. 事后评估是否发生（H13c；基线全库 1 条）
SELECT COUNT(*) FROM world_v2_events
WHERE json_extract(event_json,'$.event_type')='ResponseExpectationAssessed';

-- C. 内心四条车道的产出（H12/H14 主指标）
SELECT json_extract(event_json,'$.event_type') t, COUNT(*) n
FROM world_v2_events
WHERE json_extract(event_json,'$.event_type') IN (
  'ExperienceCommitted','PrivateImpressionAccepted','ThreadOpened','AppraisalAccepted')
GROUP BY t;

-- D. 生活是否真的在长事件（H15 主指标）
SELECT json_extract(event_json,'$.event_type') t, COUNT(*) n
FROM world_v2_events
WHERE json_extract(event_json,'$.event_type') IN (
  'WorldOccurrenceSettled','ActivityStarted','ActivityCompleted',
  'LifeArcChanged','NpcStateChanged')
GROUP BY t;
```

另外每包都要跑执行计划 §12.7 的第 1（按 purpose 的日成本）、第 2（每条用户消息的调用数）、
第 3（缓存命中率）、第 5（无效成本率）。

**注意口径**：`/health` 的 `model_usage` 按 **UTC 零点**切日，
上海时区看「今天」会少算。人工核对时用 `recorded_at` 自己按 +08:00 切。

---

## 11. 明确不许做

- 用固定话术、模板、关键词、正则或随机 `act/hold` 替她作语义决定。
- 从问号、句式或相似度推断 `waiting_for` / 期待强弱。
- 用主题黑名单、n-gram 去重、固定话题冷却或次数配额压制「心心念念」。
- 新增任何「再问一次模型」的车道（审查、自证、复核、重选、清单探针）。
- 为省钱削上下文、削记忆、让她失声，或把技术故障记成她选择沉默。
- 把 `delivery_state=unknown` 显示成 delivered，或把该气泡藏起来。
- `git revert b060d961`（见 §6 开头）。
- 为每个微事件单开 provider 调用。
- 动 §12.10 保留清单里的任何模块。
- 未经用户批准做 epoch 切库、删除归档、或写入 `data/companion.sqlite`。
- 把「测试绿」当作生产完成。

## 12. 必须停下来交回用户的情况

除执行计划 §15 已列的之外，本文另加：

- §7 的懒求值探针结论是「需要改 `ExperienceValues` 指纹口径」——那是 authority 变更。
- H13a 的 G4 复核越界（必填 >3 / 总字段 >8 / 深度 >2）。
- H15c 需要重开 generative world-author 且无法做到 Occasion 门控 + one-shot。

## 13. 交付模板

每包按执行计划 §14 的八项留证：红测、最小实现、相关测试 + 全量测试、静态检查、
生产证据、成本与延迟、剩余缺口、精确 commit。

施工记录写回 [`harness-restructure-execution-plan.md`](./harness-restructure-execution-plan.md) §17，
**不要**在本文里追加执行记录——本文是工单，不是台账。
