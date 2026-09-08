# World Consequence / Character Life Response 迁移

状态：实施中。目标来自 ADR-0010 / ADR-0019；小屋和角色移动不在范围内。
基点：`79a9f0f2`，隔离分支 `codex/living-continuity`。不修改生产数据库或配置，不发送 QQ。

## 需要同时成立的结果

1. World Author 的新协议只声明环境变化及有执行来源的客观后果。没有前置角色行动的
   环境突发不能通过随机候选写出角色行动；双方都不能创建分支前的虚构个人经历。
2. 角色在已有世界感知回合内，自由形成回应及可选未来活动意图。没有回应是合法选择；
   技术失败不得替换为此选择，也不得由 host 写出角色反应。
3. 意图通过真实消费者生成 Plan，再由现有活动生命周期开始、完成或中止。意图正文
   不提供地点、NPC、物品控制、外部发送或已完成结果的额外权限。
4. 新经历保留世界结算和角色回应的双重来源。环境结果、角色理解和实际执行不混为
   一段无区别的事实。世界本身可以继续变化，不依赖角色同意其发生。
5. Aftermath 与通用 OutcomeProposalCompiler/acceptance 两条路径都遵守新合同。
   `world_life`、`recent_self_experiences`、Recall、记忆来源同时识别作者区别。
6. 已提交旧历史原样回放；新角色决定恢复原 audit。旧 pending 不能因字段缺省而升级
   作者权限，也不能把旧 token/draw 当作新增行动许可。恢复不得重复模型调用或 Plan。

## 当前确认的闭环与缺口

- `LifeAftermathRuntime` 在世界随机结算后直接复制 WA 候选到 occurrence result 和
  Experience；两份正文分别可被读取，单改摘要不足以关闭问题。
- `world_stimulus_appraisal` 已有角色回合及持久审计，可复用。现有
  `ExperienceTransitionSettlement` 处理 Goal/Thread/Commitment/Memory，不是
  ExperienceProjection 的创建器。
- `experience_memory_retention` 的输入必须引用已提交 Experience，不能移到前面作为
  新经历作者，否则会让一次决定依赖自己尚未创建的事实。
- 此前 `LifeIntentDraft` 和 Plan 生命周期只接受 inbound Observation。第一片已接入
  独立世界事件来源合同，旧聊天验证器仍保留原边界。

## 实施顺序与验收

### 第一片：角色可执行的世界回应意图

复用 self_directed 草稿，新增 world-stimulus 来源的 typed intent。严格绑定原
WorldOccurrenceSettled、角色 model result / proposal / lineage、actor、cursor 和
原始选择时刻。与旧聊天意图区分身份；计划时间从原选择推导，冷恢复不顺延。

前端拥有 world_stimulus 的同次调用、schema、capability 与精确失败反馈；后端拥有
新的来源证明、Plan 接受与生命周期衔接。角色可不作 Appraisal 而安排一件事，不能让
安排活动强迫她产生情绪。先接受行动意图，再终结原源触发器；崩溃从原决定恢复。

本片必须通过实际角色请求 → DecisionProposal → Plan → 开始/完成 → 冷恢复的公共
测试，另外拒绝错误 actor/source/hash、无角色决定、反向冒用聊天来源。它是完整迁移
的执行前置，单独完成不能声称已经关闭 WA 正文问题。

第一片已于 2026-09-08 接通（后端 `035a2b68`、前端 `fca6f29a`、公共恢复与读取测试
`5560dba9` / `5cfa0742`）。9 项经 MockTransport 的生产角色请求测试与 1 项独立情绪消费者
先完成的冷恢复测试通过；生产装配覆盖 Plan → start/complete → 后续聊天来源读取。
27 项后端来源/权限/生命周期测试通过。这里使用模拟供应商，不是新真实角色验收。
完整 120 固定场景仅一个场景的 replay hash 随新能力及合同文案改变；完成因果对照后
建立 `.100`，不带 limit 的正门通过，原行为断言、输出和调用次数均未改变。

新意图复用已有 world-stimulus 调用；其后开始/结束仍使用原活动生命周期角色调用。
新 schema 及意图正文会增加 token，不能由“没有独立意图调用”推导成本为零，亦未据此
声明每月 100 元目标已验收。该片新增付费调用为零。

### 后续片：世界来源与角色经历的分离

2026-09-08 当前进度：显式新合同的作者、结算、角色回应、经历与主要读取端已接通，
生产 manifest 缺省仍未切换。历史 `.1` 与旧 pending 不因新代码自动升级。

World Author 从原 manifest anchors 提供至多四条可读执行材料，包含实际开始/恢复
记录及原角色授权意图。没有可读 Action 正文的 receipt 不提供执行权限。新请求在
调用前保存完整 messages；接受前反证原 ModelResult、Proposal、request bytes、
manifest、原 pin 和每条执行材料。两个 review packet 均使用该原始证据。新 `.8`
possibility 必须逐项绑定原作者审计中有序的 canonical consequence hashes，不能用
自洽的新 sidecar/hash 替换审核过的正文。新语义 focused critic 未配置时明确失败。

新合同的 source-closure rejection 只交给同一个 World Author 一次：保留原请求和
拒稿，加入精确失败坐标，再完整重审替换稿。不得本地删改角色动作、补写故事或用
no_op 冒充技术恢复。已审计稿件后的 CAS 失败及 SQLite 重开不重复付费调用。

Aftermath 与通用 Outcome 均发布精确选中的 `.2` 结果正文及来源描述符。环境可以先
结算，但没有已接受 CharacterLifeResponse 时不创建新 Experience。通用 Outcome
的接受后中断会先补齐正文再终结触发器；世界感知模型调用前也检查实际发布的正文，
不能在内容缺失时询问角色、生成回应或把存储失败解释为角色选择。

同一次 world-stimulus 调用的 `life_responses` 按源保存原文或显式 null，再组合成
Experience `.2`。一个复合来源同时引用精确世界 settlement 与已接受角色回应；摘要
仅包含世界结果定位和角色原文，不把世界作者的叙述复制成角色亲历。回应、经历与
可选 Plan/Appraisal 的部分提交及独立消费者先结束触发器均从原审计恢复。Memory
retention 在 Experience 之后复用现有角色调用，输入分别列出世界后果和私人回应，
校验全部来源后限制可读文本至 3000 字并标记截断；不把回应中的推测变成外部事实。

LifeContent、WorldLife、snapshot、Recall 与 Memory retrieval 识别复合来源，世界
部分和私人解读分栏。复合 Experience wrapper 不自动获得 `past_world/shared_history`
权限；世界事实仍引用独立 settlement，私人解读进入 reflective 索引。新候选在
Outcome 的 advisory 与角色输入中保留结构，不能把 JSON 字符串裁坏后当摘要。
选中结果的隐私下限不能低于 occurrence 或 candidate 中较严格的一方；withhold
后果不进入新角色回应/经历来源。

NPC 仅取得世界部分及其 settlement/descriptor 来源；私人反思保留两个作者的原始
分栏，并复用已有读取预算。缺正文、错 hash 或 reader 缺失时拒绝该次技术输入；
上层将该经历域标为 unavailable，不能把读取失败改成“可用但没有经历”。

还需完成：旧冰雹/手账反例的版本化替代和已执行活动/客观成败路径；
切换真实生产 manifest 的完整资格检查；
另列预算的真实语义审核和角色对话。以上机制测试使用替身供应商，不能替代这些验收。
小屋和角色移动不在本轮范围内。上一阶段 476 项及完整 120 场景的证据保留在审计文档；
本轮集成点 `dc00ddd7` 通过 49 文件中的 790 项不同用例（59.84 秒）。正常 CLI
不带 limit 的完整 120 场景通过，产物与上一 `.100` 逐字一致；两组仍是离线机制证据。

版本化世界后果材料，保留环境变化独立结算。给已有世界感知回合加入明确的角色回应
结果，记录原文与作者来源；用单个复合来源对象绑定世界 settlement 和角色回应，
记忆保留继续后置。各读取器输出分别有来源的资料，不能只靠显示标签提升原文权限。

新环境文本仍是自然语言，schema 或字段名不能证明正文没有夹带角色行动。新合同
必须把完整环境文字、客观执行结果及其实际执行来源交给既有 focused source review，
保留同一作者的一次精确纠错；不恢复已退役的普通全文审核器，也不引入关键词行为
判定。机械闭环只证明权限、来源和读写类型，语义检出仍需真实模型验收。

对“无 Plan 的突发事件”“角色已选择的活动”“已有实际执行来源的客观成败”分别验证。
后两者不能在角色自由选择之前预写动作，再用后来的 Plan ID 回填许可。旧已结算、旧
pending、新决定已持久化但效果尚未完成三种恢复须独立覆盖。

## 不能冒充验收的证据

冰雹 fixture 的 supported critic 是测试替身，只证明机械链目前允许越权，不证明
真实 critic 必然放行。parser、source 坐标及固定回放检查不能证明任意自然语言没有
漏报。修复后仍需新的真实对话/生活链：确认角色如何回应、实际选择/执行、长期回忆
如何表述以及费用。当前累计已知 2.4989562 元，保守占用 2.8134232 元；封存的旧批次
与 low 单样本批次不再追加调用。新增付费试验须另列有界预算并继承累计账本。
