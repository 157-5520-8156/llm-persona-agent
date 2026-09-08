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

## 迁移前确认的问题与可复用机制

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

2026-09-08 当前进度（`66e373c9`）：作者、结算、角色回应、经历与主要读取端已接通。
隔离代码中的实际 `ProjectionLifeCapabilityManifestCompiler` 新请求默认改为
`life-development-capability.production.3` / `world-consequence.2`；没有修改正在运行的
生产程序或配置。历史 `.1` 与旧 pending 保留原 manifest、审计及正文，不自动升级。

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

公共机制测试已覆盖无 Plan 突发事件的文字/null 回应、角色实际开始尝试后的客观后果，
以及角色选择新活动和长期方向。新活动完整走过实际默认 compiler、作者、角色决定、
Plan、ActivityStarted、Aftermath、Experience `.2` 与冷恢复；不再用测试子类手动加 marker。
旧冰雹/手账 fixture 原样保留，新路径的版本化替代不更改旧合同，也不证明真实 critic
必然识别所有越权叙述。另列预算的真实语义审核和角色对话仍是未完成的资格。

新未来 Plan 不允许借用另一个已开始活动的客观执行结果；已有执行来源的结果仍可由
world_contingency 表达。动态 Plan 转换保留 `.2` 描述符，读取器反证 `.8` 原稿来源。
长期后果候选先核对完整结构和隐私，再交由原角色决定；角色所选 life direction 同时
进入原 Proposal 和实际 settlement。系统不从事件类别推导她的方向、心情或说法。

已审计而未完成的请求恢复原 cursor：只读 ledger facade 限制嵌套读取到原前缀，
重新编译后严格比较 capsule、snapshot、cursor 和完整正文 hash，再恢复原 manifest。
普通 Context 读取仍要求当前头；恢复接口不产生新的可信角色决定句柄。新/旧协议、
active Plan、不同预算和后续事件隔离均有反证及零 HTTP 恢复测试。

小屋和角色移动不在本轮范围内。当前 `66e373c9` 通过 55 文件中的 **846 项不同用例
（68.55 秒）**。正常 CLI 不带 limit 的完整 120 场景通过，产物与上一 `.100` 逐字一致；
旧阶段 476/790 等组保留在审计文档，不累加。这些仍是离线机制证据。

版本化世界后果材料，保留环境变化独立结算。给已有世界感知回合加入明确的角色回应
结果，记录原文与作者来源；用单个复合来源对象绑定世界 settlement 和角色回应，
记忆保留继续后置。各读取器输出分别有来源的资料，不能只靠显示标签提升原文权限。

新环境文本仍是自然语言，schema 或字段名不能证明正文没有夹带角色行动。新合同
必须把完整环境文字、客观执行结果及其实际执行来源交给既有 focused source review，
保留同一作者的一次精确纠错；不恢复已退役的普通全文审核器，也不引入关键词行为
判定。机械闭环只证明权限、来源和读写类型，语义检出仍需真实模型验收。

真实试验仍须分别观察“无 Plan 的突发事件”“角色已选择的活动”和“已有实际执行
来源的客观成败”。后两者不能在角色自由选择之前预写动作，再用后来的 Plan ID 回填
许可。机制覆盖不能证明实际模型会自然运用这些能力，也不能代替长期多样性观察。

## 不能冒充验收的证据

冰雹 fixture 的 supported critic 是测试替身，只证明机械链目前允许越权，不证明
真实 critic 必然放行。parser、source 坐标及固定回放检查不能证明任意自然语言没有
漏报。新默认首次真实试聊 trial-07 在两回合后因下一笔预约超出剩余额度停止，未完成
生活链。它再次暴露聊天正文显式空 claims 时的事实漏报，以及作者/critic 的主体错配
和错误拒绝；不能用这次机制迁移宣称这些语义问题已关闭。预算拒绝的审计 slot/outcome
配对异常已由 `84d098b9` 窄修复，仍须继续核对真实角色选择/执行、回忆和费用。

trial-07 的 9 次已结算调用约 0.2823606 元，当前累计已知约 **2.7813168 元**，保守
占用约 **3.0957838 元**；原关账保存精确 manifest 数值。新批次总上限 1.20 元，每次
先预约 0.60 元，旧封存批次与 low 单样本批次不追加调用、不释放历史 unknown。
