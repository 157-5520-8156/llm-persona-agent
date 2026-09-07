# 连续性生活：系统认识与本批修复

日期：2026-09-07。起点：`da8aae88c1245dbd41f763d5c0df50025fb76c2e`。
集成分支：`codex/living-continuity`。此文是代码审查和实现记录；
产品权威仍是[设计总纲](../design/girl-agent-design-intent.md)，施工权威仍是
[执行计划](../design/harness-restructure-execution-plan.md)。
最终代码验证提交：`93620579`；其后的本批提交只回填文档结果。

## 认识与证据边界

项目要提供一个有连续生活史、私人内心、自主选择和后果的角色。聊天软件是用户感知她的渠道。
核心判据是：一次经历或选择是否真的改变以后可读的状态，以及她之后是否有能力据此选择和行动。

本次对照了权威文档、提交历史、实际 production composition、关键生产路径和既有运行记录。
历史记录中持续无回应后的主动消息、重复的反思型生活事件，提供了问题线索；
它们无法单独证明某个新版本中的具体根因。本批每项修改都进一步落到代码路径与离线回归。
未将历史测试通过数、健康端点或类型数量作为今日体验证明。

最大的共性问题是**连接处失真**：上游有生活或决定，下游却没带上相同的内容、身份、时间、来源，
或根本没有消费其结果。因而优先修完整路径，而非继续列举剧情、情绪模式或聊天技巧。

## 当前系统地图

| 层次 | 实际角色与连接 | 本批判断 |
| --- | --- | --- |
| 世界权威 | WorldLedger 保存不可变事件；reducer 构造 projection，CAS 与接受契约守合法状态变更 | 这是历史与后果的底座，不自行决定角色意义 |
| 生活环境 | Clock、LifeEcology、LifeDevelopment 提供日常、计划、环境事件与长期变化；世界作者提案经接受后成为事实 | 人生发展与动态 Life Arc 有生产接线，不能误报成只有设计文档 |
| 主角内在 | CharacterInterior 在 pinned snapshot 上解释材料和选择；Appraisal、Affect、PrivateImpression、Aspiration 等保存不同类型的后续状态 | 统一作者边界已存在，但各后台 profile 曾丢失重要共同背景 |
| 记忆与来源 | Memory、经历、事实、回忆、日记进入模型视图；隐私和来源库存约束角色可知材料 | 看得到文字却丢失来源也是断线，不能只检查 canonical snapshot 有数据 |
| 行动与关系 | Thread、Commitment、期待、revisit、关系声明与 SocialInitiative 提供未来安排和考虑机会 | 时间、独立计划身份、排期变化和技术恢复需跨模块一致 |
| NPC 生态 | NPC 有独立 actor 范围；自己的意图与世界作者结果分开；主角不能全知 | later 计划缺世界结果已修；NPC 独立通信到主角仍缺完整能力 |
| 可见表达 | Proposal/Action、表达阶段、dispatch、receipt 形成实际投递；媒体另有能力、权限与投递链 | 获准、生成、发送与送达不是同一件事；本批未改变部署资格 |

应持续追踪的链是：有来源的世界或交互变化 → 她实际看见的现在 → 她的解释与选择 →
接受的持久状态/行动 → 世界或通信后果 → 下一次可读的现在。任一处缺失，增加内心文本都不能补齐。

## 本批代码变化

### 1. 换场景仍是同一个人

[background_context_profile.py](../../src/companion_daemon/world_v2/background_context_profile.py)
让生活选择、主动联系、感受与反思共享有界的生活、愿望、记忆、已有解释和近期对话。
World Author 单列，避免继承主角私人内心。原 canonical snapshot 不改写。

对话限额保留最新一端，同时兼容稳定前缀/易变尾部；compact appraisal 表也保留相同限额和来源识别。
独立审查进一步发现：日记显示的经历比来源清单多，需在真实 compile → model_view → profile
路径保留逐条来源。它是事实来源缺口；没有证据证明该问题造成 actor 隐私泄漏。

### 2. 留下她真正想留下的东西

`PrivateTurnState.stuck_with_me` 与 `inner_state_summary` 分开。
入站和主动回合选择保留印象时，保存前者的原文，不能由宿主替换成 `my_state`。
新模型选择 `keep_impression=true` 却未提供内容时，走已有的同角色一次精确纠正；
再次无效仍为技术失败。旧审计缺该可选字段仍可反序列化，省略空字段以保留历史 payload。

私人印象反思机会也不再通过截断含冒号的引用猜测来源身份。
愿望当前文本与最新修订来源绑定，同时保留最初 planting 的稳定身份，避免将后来想法倒写进过去。

进一步审查发现，paid 留存还会选择全局最近的 active appraisal 并混入同对象的旧解释。
本批将其收紧到本次角色结果及其已接受 appraisal 的完整因果链；普通 paid 留存没有默许
引用任意旧感受的能力。新输出明确不做 appraisal 却要求留存时，应进入一次精确纠正；
接受阶段缺失合法来源则记录技术失败，不能把新想法挂到别的起因上。
同一 paid 请求显式重入时，已接受的印象在原 appraisal 仍活跃或后来过期后均幂等返回。
若原 ModelResult 与 typed Proposal 已保存、Acceptance 前发生存储故障，在同一合法 prefix 下
复用原提案继续接受；不重新生成语义或擅自重设 CAS。
这些是请求重入的恢复证据，**不等于生产重启会自动重新发起 paid 留存请求**。

### 3. 主观注意不再自产客观经历

`noticed` 保留为本轮主观注意和审计。入站、活动选择和生产装配不再将它喂给新的
open-world occurrence；公开旧入口明确拒绝新的 paid attention 事件。

已有 LifeDevelopment World Author 继续负责新环境事件。这并不自动将 `noticed` 转成永久记忆；
需要保留的内容使用明确的角色选择与对应持久机制。
旧 open-world proposal/occurrence 仍可恢复；冻结基线事件验证了两种起点的恢复、幂等与重放，
不修改旧事件内容或 hash。

### 4. 她安排的以后能够到来

声明的 expectation/revisit 时间先于普通空闲冷却被检查；peek 与真正 drain 使用相同逻辑。
枚举每个未决计划，避免最新已结束计划挡住旧计划；重启、技术重试和模型输入使用原计划身份。

Thread 真正改期形成新的考虑身份；同一 due_window 的 importance 更新不制造新机会。
审查发现旧 open process 恢复后会被最新 transition 检查误拒绝，已在实际 proactive audit
中确认同一连续有效排期。真实改期替代的旧来源仍须拒绝。Commitment 的机械 open → due
同样保留原考虑身份。安装 SocialInitiative 后，旧 fallback 不再生产另一套 Thread/Commitment
机会，只保留已打开历史 process 的恢复及消费记录。
未完成的 Thread/Commitment 不会因为另一段对话到来或另一件事考虑成功就被隐式取消。
技术重试逐个寻找仍有效的原安排，调度器选择最早有效的重试时间；被改期替代的失败不能挡住
其他未决安排，较晚失败的 backoff 也不能推迟较早已经到点的工作。
这些机制只安排考虑机会，角色仍可选择联系、等待或不联系。

### 5. NPC 的未来计划有世界后果

[npc_ecology.py](../../src/companion_daemon/world_v2/npc_ecology.py)
将原先停在 active 的 later 计划接到原 NPC 意图 → World Author → occurrence → 既有余波 → 完成。
使用 plan 派生的稳定结果身份；恢复已保存裁决不会再次询问模型，重复 tick 不会制造第二件事。
晚到的唤醒保留实际活动开始时间。已承诺的过程不因新环境机会关闭或新调用预算耗尽而被抛弃。

世界作者拒绝、无结果和 stale revision 是技术状态，不伪装成完成或无变化。
NPC 独处事件不会直接创建主角 appraisal/记忆；未放宽参与者权限来伪造她的知情。

## 验证

全部验证使用离线 fixture models、内存或临时 SQLite；没有调用真实 provider 或 QQ。
测试覆盖实际 application、生产配置装配、模型输入编译、typed 接受链与恢复路径。

| 范围 | 关键验证 |
| --- | --- |
| 共同背景 | 生活/愿望/已有解释保留，最新对话，compact appraisal，日记来源，World Author 隔离 |
| 私人残留 | slim/full 入站与主动生产路径保留原文，缺内容精确纠正，历史缺字段兼容 |
| 世界权限 | paid attention 拒绝且无事件，冻结旧事件重放、未完成旧 proposal 恢复 |
| 未来安排 | 短于冷却的声明时间，多独立计划，进程重启，改期与同排期更新，真实 source gate |
| 愿望变化 | 修订来源与时间，稳定 planting 身份，结晶兼容 |
| NPC 后果 | later、延迟唤醒、SQLite 重启、中途写入中断、重复 tick、预算耗尽、stale prefix、主角不可知 |

冻结场景基线从 `.92` 升为 `.93`。分别在干净 `da8aae88` 和集成树跑完整 120 个输入后，
逐项比较导出清单：仅 120 项 `replay_hash` 变化；输出、事件种类、Action 终态、模型调用数、
room view、后台运行字段和所有场景断言一致。抽查事件链确认新请求身份通过 model-result、
acceptance、expression 审计传播；未改旧存储事件。`.93` 的完整清单 hash 为
`92ec85bce2396318298ac53bdd5cd19b72a6ddd04e388cbef69358a1471353c3`。

第一轮整库回归为 6069 passed、19 skipped、11 failed。逐项定位后修复：

- 6 项安装测试在干净基线同样失败：fixture 依赖固定部署路径与本地 `.venv`。
  现使用临时源仓库、匹配的临时 plist、测试解释器与 fake launchctl/curl；
  生产脚本、真实 plist、严格路径检查与安装/回滚断言不变。
- 3 项预算测试依赖运行时是否处于高峰窗口。在干净基线强制高峰可稳定复现 3 个相同失败；
  只为费用上限测试固定进入费用检查的时段前提，专门的高峰推迟测试仍保留；21 项均通过。
- 2 项旧 opener 测试暴露新增匹配逻辑预读全部历史 hypotheses。
  已改为按合格候选惰性检查精确来源，保持原 head/fallback 次序；新增真实 ledger/public opener
  回归覆盖最新机会与已留存来源不重复开启，相关 69 项通过。

最终整库回归：**6082 passed、19 skipped、0 failed，421.66 秒**。
另有 1 条现有 Starlette/httpx 弃用警告。该次完整运行包含未设 limit 的冻结 120 场景基线测试，
`.93` 的实际结果与上述 hash 一致。

其他最终检查：机制目录 schema 2 / 26 项通过；平台反向依赖边界通过；formal-eval
`verify-fixture` 以 exit 0 通过产物结构校验，其合成评估报告仍为 `blocked`，不冒充真实评估。
修改的 Python 文件通过 Ruff，`git diff --check` 通过。

重现整库测试：在本分支目录执行
`PYTHONPATH=src:tests/support /Users/geoff/Projects/Girl-Agent/.venv/bin/python scripts/test_fast.py --tier full`。
本地原始结果位于忽略的 `output/living-continuity-gates/`；正式保留的结论、范围和基线身份在本文，
不要求提交临时 SQLite 或运行日志。

## 仍未完成的能力和证据

- **NPC 独立通信与主角知情。** NPC 私有生活有结果，不等于主角已经听说；需要来源清楚的
  actor 通信、可观察暴露与后续感受路径。不能把 NPC 私域直接塞进主角上下文。
- **主动放下等待与私有 revisit。** 部分期待终止仍依赖用户入站；主动回合中不发送却安排私有回看
  的能力需要进一步脱离送达前提。不能以 `no_op` 代替缺失的选项。
- **私人印象的变化能力不均衡。** 专门反思支持整合、替代和放下；普通已付费回合目前主要支持保留。
  是否遗忘、如何重新理解属于角色，不能补成按时间自动删掉情绪。
- **paid 留存的自动恢复调度仍缺。** 已完成聊天/主动 process 后，hitch 保存失败虽然会留下技术审计，
  当前后台 drain 不会自动按同一已付费决定重新发起保存。独立反思的 trigger/预算不是它的替代品。
  本批只证明精确请求重入与同 prefix 的 pending 提案恢复；需要后续接上持久工作与无新增作者的消费者。
  更早的“派生 ModelResult 已写、typed Proposal 未写”切点还没有可续接的 typed 提案，
  当前同请求重入也可能拒绝；本批不能表述为所有保存切点均可恢复。
- **自然语言事实保证有界。** 当前许多路径的确定性检查验证引用存在、范围与接受条件，
  不等于穷尽证明所有自由文字都被来源蕴含。本批没有恢复已退役的独立模型审查。
- **真实运行资格。** 未部署、未调用真实模型、未测此次上下文的在线成本/缓存/延迟，未做 QQ 回执验收
  或多日自由相处。绿色离线结果不证明她必然在被忽略后不再追问，也不证明事件多样性或真人感达标。

这批修复使已存在的生活和决定更忠实地进入后续系统状态。完整产品仍应以多日连续后果和真实相处
检验；能力缺失不能归因为模型性格，也不能通过预写情绪、动机或回复掩盖。
