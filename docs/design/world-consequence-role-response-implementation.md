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
- 已有 `LifeIntentDraft` 和 Plan 生命周期，但当前来源仅接受 inbound Observation。
  世界事件需要独立来源合同，不能借旧来源或放宽旧验证器获得许可。

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

### 后续片：世界来源与角色经历的分离

版本化世界后果材料，保留环境变化独立结算。给已有世界感知回合加入明确的角色回应
结果，记录原文与作者来源；用单个复合来源对象绑定世界 settlement 和角色回应，
记忆保留继续后置。各读取器输出分别有来源的资料，不能只靠显示标签提升原文权限。

对“无 Plan 的突发事件”“角色已选择的活动”“已有实际执行来源的客观成败”分别验证。
后两者不能在角色自由选择之前预写动作，再用后来的 Plan ID 回填许可。旧已结算、旧
pending、新决定已持久化但效果尚未完成三种恢复须独立覆盖。

## 不能冒充验收的证据

冰雹 fixture 的 supported critic 是测试替身，只证明机械链目前允许越权，不证明
真实 critic 必然放行。parser、source 坐标及固定回放检查不能证明任意自然语言没有
漏报。修复后仍需新的真实对话/生活链：确认角色如何回应、实际选择/执行、长期回忆
如何表述以及费用。当前累计已知 2.4989562 元，保守占用 2.8134232 元；封存的旧批次
与 low 单样本批次不再追加调用。新增付费试验须另列有界预算并继承累计账本。
