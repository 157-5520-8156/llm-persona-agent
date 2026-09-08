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

2026-09-08 进度：新协议的作者输入与角色回应两端已分别实现，完整经历链尚未接通。
新 `.2` 来源经公共 HTTP 角色调用生成 `life_responses`，逐源正文或显式 null 必填；
同一决定先接受回应，再接受可选意图/情绪。多源部分提交及独立情绪消费者先终结
触发器后，均从原 audit 恢复，不补模型决定。旧来源保持旧 wire。`.4` 提案不能对
新来源绕过回应要求后单独接受 Plan。

World Author 新请求只从原 manifest anchors 中提供至多四条可读执行材料，包含
真实开始/恢复记录以及原角色授权意图全文。没有可读 Action 正文的 receipt 不提供
执行权限。新请求在调用前保存完整 messages；结果反证模块核对原 ModelResult、
Proposal、request bytes、manifest、原 pin 和每条执行材料。该反证模块已有公共
作者调用中断后及 SQLite 重开测试，**尚未接到完整 SourceReview/接受路径**。
新 prompt 已接显式 `.2` manifest 的作者调用，未提供的 execution binding 会精确
交回同一作者重选一次。旧 schema 已独立逐字比较，不因兼容解析器增加新字段。

生产 manifest 编译器仍保持 `.production.2` 与原缺省，不启用新后果写入。必须按以下
顺序完成余下链条后才能切换：

1. 将已核实的原执行材料交给两种 review 编译器，持久保存新 packet/subject 版本；
   为新作者结果的单次来源纠正传递同一新合同。当前 source-rewrite helper 仍是旧 wire。
2. World Author 新候选使用结构化内容及 `result_contract`，Aftermath 与通用
   Outcome 接受均反证原作者/执行来源；旧 pending 不因缺省而升级。
3. 增加一个复合来源绑定的 Experience `.2`，同时引用世界结算与已接受角色回应。
   两种结算路径停止将新 WA 原文直接复制成经历；LifeContent、WorldLife、snapshot、
   Recall 与后置 retention 一同识别两种作者权限。不能只改显示标签。
4. 完成原冰雹/手账反例的整条修复与冷恢复，再做另列预算的真实角色实验。

本阶段合并的 466 项及随后新增 10 项请求审计测试通过；完整 120 固定场景的 `.100`
输出与此前逐字相同，
没有更新 baseline。这里不构成新语义审核质量或长期真人感验收。用户已明确暂时弃用
小屋，角色移动与小屋交互不在此轮目标内。

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
