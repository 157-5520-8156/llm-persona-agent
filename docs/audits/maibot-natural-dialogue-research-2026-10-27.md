# 麦麦的自然对话设计：源码阅读与 Girl-Agent 适用边界

## 范围和版本

用户提到的“麦麦”对应开源项目 [MaiBot / MaiSaka](https://github.com/Mai-with-u/MaiBot)。
本记录以官方 `v1.3.15`、commit `062d45cf8bd4272ac57d7ec03fcf405e5a458211` 为准；
[更新日志](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/changelogs/changelog.md#L1-L20)
将该版标为 2026-10-23，commit 时间为 2026-10-24 东八区。其 v1.3.14 曾使用单独的
mid-term 摘要模块；v1.3.15 删除该模块，改为滚动上下文压缩，不能把两版细节混为一谈。

本次是官方源码与文档阅读，没有启动麦麦、没有访问任何人的麦麦会话，也没有做真实用户
盲评。源码能证明“采用什么机制”，不能证明它在本项目相同模型、角色与场景下更自然。
官方 README 的自然体验表述是项目目标，不是本文核实的比较测试成绩。

另进行下述六次本项目冻结作者输入的去重探针：这是直接 DeepSeek 对照，不是运行麦麦，
也不是完整 World／QQ 链路测试。

## 源码观察

### 回复与内部决策分开呈现

Planner 自行选择是否调用 `reply`，并写非空 `reply_reference`，包含相关上下文和回复方向；
随后 Replyer 再生成用户看到的文本。`reply_reference` 是信息及方向，不是最终台词。
见 [Planner 提示](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/prompts/zh-CN/maisaka_chat.prompt)
和 [reply 工具声明与调用](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/src/maisaka/builtin_tool/reply.py#L120-L139)。

Replyer 的中文基础模板较简短，以角色身份、表达风格、可选聊天注意事项及回复信息参考
组成；发送给回复模型的历史主要由真实 IM 消息构建，角色历史消息可以作为 assistant
消息。内部通知、模型输出、工具结果和聊天回想不会作为普通聊天历史冒充用户消息。
但 Replyer 仍可额外接收 Planner 的 `reply_reference`、可选表达习惯、当前消息、附件及
配置提示；因此不能说“麦麦完全没有内部思考、也没有记忆”。见
[中文模板](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/prompts/zh-CN/maisaka_replyer.prompt)、
[请求构造](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/src/chat/replyer/maisaka_generator_base.py#L631-L740)
和[历史过滤](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/src/chat/replyer/maisaka_generator_base.py#L967-L980)。

官方默认模板配置 Planner 使用 `deepseek-v4-flash`，Replyer 配置列表包含
`deepseek-v4-pro` 的 thinking 和 nonthinking 版本；本地实例可修改配置。具体使用的
检查点、模型路由、参数和温度均需读取实际实例，不能仅因两者都叫 DeepSeek 就视为
同一模型实验。[默认任务模板](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/src/config/default_model_config.py#L22-L49)

### 表达学习是重要机制，也包含项目不应照搬的规则

开启表达学习时，系统收集真实聊天素材；学习提示要求从他人发言提取“情境—表达”候选，
保留对应来源编号，不把自己发言作为新学习内容。候选可经过 AI 检查，并作为可管理资源
写入数据库。[学习提示](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/prompts/zh-CN/learn_style.prompt)、
[来源与过滤](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/src/learners/expression_learner.py#L284-L304)
及[写入流程](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/src/learners/expression_learner.py#L390-L500)。

v1.3.15 新配置的默认快速档用加权抽样选表达候选供 Replyer 参考；精细档使用向量候选
并由独立模型筛选。选出的候选以情境与句式关系展示给 Replyer，筛选失败也有直接注入
回退。是否启用、历史配置、候选库与实例设置会影响实际行为。[配置](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/src/config/official_configs.py#L4029-L4047)、
[注入方式](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/src/chat/replyer/maisaka_expression_selector.py#L176-L184)、
[候选与回退](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/src/chat/replyer/maisaka_expression_selector.py#L481-L555)。

另有可配置的关键词/正则匹配反应以及按概率选择临时风格；后者默认概率为 0，不能说所有
麦麦实例都会启用它们。[实现](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/src/chat/replyer/maisaka_generator_base.py#L125-L140)、
[关键词反应](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/src/chat/replyer/maisaka_generator_base.py#L493-L529)、
[默认概率](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/src/config/official_configs.py#L204-L227)。

### 对话压缩、入站合批和中断

v1.3.15 使用滚动对话摘要，组织话题、背景、进展、未完事项、约定，并要求区分猜测与事实、
工具请求与实际结果；摘要是内部参考，并通过结构与长度检查，原有近期消息另行保留。
源码说明了想避免哪些误解，不证明模型生成的摘要必然正确或具备本项目的来源闭包。
[摘要提示](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/prompts/zh-CN/context_compaction.prompt)、
[构建与验证](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/src/maisaka/context/compaction.py#L215-L249)。

官方还提供配置化入站防抖，分档等待和合批；它只规定处理窗口，不能证明“慢一些就是更
自然”。配置中的快速反应模式可在新的消息到来时打断并带上新消息重新请求。
群聊的概率门控与私聊不同：该版本私聊不使用该群聊动态门控，不能笼统说“私聊靠概率
决定是否回复”。[防抖机制](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/src/chat/debounce/README.md)、
[快速反应配置](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/src/config/official_configs.py#L505-L541)、
[私聊与群聊门控](https://github.com/Mai-with-u/MaiBot/blob/062d45cf8bd4272ac57d7ec03fcf405e5a458211/src/maisaka/turn_trigger/gates.py#L64-L105)。

## 与 Girl-Agent 的边界

可借鉴的是**请求呈现与可观察性**：把可核验的真实双方发言、角色当前状态、来源和可用
能力分清楚；在同一个 pinned World Context 里减少重复的内部说明，使角色看见对话的
连续关系。可考虑对比 provider 原生多消息与既有 JSON 表示，但不得改变实际发送证据、
已提供的来源范围、记忆检索、权限、工具选择和可见审核。模型能否利用这些材料仍需实测。

不能直接迁移“规划者决定方向、另一个回复者写台词”的双作者结构，也不能复制情境—固定
句式、关键词/正则反应、随机注入角色风格或按概率规定是否回复。这些会偏离
[受控高随机 ADR](../adr/0010-controlled-high-variance-character-agency.md)：角色模型保留
行为与措辞选择；系统只承担事实、隐私、授权、CAS、回执和可重放等硬边界。固定延迟可以
作为接入技术参数的试验对象，但不能用它悄悄取代角色的节奏和沉默判断。

摘要方法也只可作展示层候选：保留原文、来源身份、发生时间、隐私、状态和可重放性；
摘要或对话中的用户陈述不能自动升级为 World 事实。不得为提高真人感关闭 Recall、审核、
降低生活频率，或只统计成功可见输出。表达学习涉及私聊和第三方语料，不得无授权导入。

另一个可验证的参照是本地已存在的 SillyTavern 官方检出 `8172dcd0ee67`：其
[Chat Completions 历史构造](https://github.com/SillyTavern/SillyTavern/blob/8172dcd0ee672d3cd9a5e5f7af134f91a45cd2b8/public/scripts/openai.js#L558-L571)
把用户与角色历史分别标为 user/assistant。昨晚本项目真实作者请求只有 system 与 user 两条，
双方历史主要嵌在 user 的 JSON 中。原生消息表示是否改善自然度仍是**未测假设**：必须
保留同样的消息、来源、授权、发送状态和重叠时间，不能把未送达草稿变成 assistant 历史。

## 固定 DeepSeek 的去重探针

先离线核对昨晚三份真实 `slim2` 作者请求：顶层 `plan_management` 展开请求本地引用后，
与嵌套 `request.plan_response_input.capability_json` 完全一致；八项计划的 ID、revision
和状态与 `target_jsons` 一一对应，这三份中 `last_transitioned_at` 和 `terminal_reason_ref`
均为空。原始冻结请求、所有宿主证明和引用绑定原样保留在私有目录，未修改 World 或计划。

冻结三份输入，A 为原始请求，B 仅在发给作者的视图中去掉嵌套的 capability 副本和八条
target 证明；顶层计划、所有原对话、source inventory、工具 schema、DeepSeek `deepseek-flash`、
温度 0.7、输出上限 4096 均相同。逐对象对照与把移除字段还原后完整相等的检查通过。
这只证明上述三个样本的直接展示与计划坐标，没有证明任意输入的展示等价，也没有验证
来源审核、角色接纳或冷回放；若其他计划有非空状态、终止原因等，不能用此探针推定可删。
冻结 protocol 中“宿主保留完整证明”是未来接入的设计条件；本探针没有构造宿主。原始证明
只保留在冻结基线文件中，试验范围澄清另见私有 `probe-scope-clarification.json`。

依照预先冻结的预算与顺序，两种视图每个场景各调用一次，共六次，全部收到模型返回、
通过 `slim2` 传输解析。每对输入 token：31,250→26,118、32,116→26,983、
31,493→26,363，合计减少约 **16.2%**；provider 报告六次合计 174,323 输入、1,846
输出 token，实际费用未知。没有发送 QQ、请求图片、执行 World 接纳或独立来源审核。

表达没有表现出可认定的稳定改善：倾诉场景仍出现概括和替人定性，且一条去重候选将
“差两个名额”说成“名额就两个”，容易理解为名额总数只有两个；原话并未给出总数，应保留
为未经来源支持的疑似误读。每组
每例只有一次采样，也没有真实聊天者盲评；不能把输入更短当作自然度改善或事实合格。
所以这一候选没有接入生产，也没有覆盖上轮 `slim4` 的改进。原始结果与预注册协议位于
git 忽略的 `output/private-audits/maibot-dialogue-20261027/`，目录 0700、文件 0600。

## 下一步隔离对照建议

不切换作者模型，不复制麦麦话术。先冻结同一批源闭合的完整聊天上下文、角色身份、
能力、原始工具 schema、DeepSeek 模型与参数、审核和费用上限；记录所用版本和 hash。

1. A 为现有请求；B 仅把同样的已核验对话及角色上下文换为角色明确的多消息呈现。两边
   保持事实/来源、Recall 与角色决定权一致，不新增 Planner/Replyer 作者。若格式无法在
   不改变来源语义的前提下恢复，停止该对照并报告，而不是伪造 user/assistant 历史。
2. 去重探针已揭示潜在 token 收益但也出现用户事实误读；如需继续，先按非空状态、隐私、
   source coverage 和审核 receipt 做完整负例及盲评，再决定是否值得版本化接入。不要同时
   改变温度、材料范围、审核或换模型，避免不知道差异来自哪里。
3. 对话至少覆盖连续倾诉、日常、打趣、分歧、角色选择沉默或拒绝，以及依赖具体记忆的
   情境；同一输入多次抽样、盲化方案和顺序。由实际对话者评价角色感、是否反复读心、
   是否顺着真实聊天推进、风格是否有变化；单独记录虚构、source review、技术失败、沉默、
   接纳/回执、延迟和费用。短样本只能筛选方案，不能得出线上质量或成本结论。
4. 优先用临时 SQLite 与 CaptureDelivery 做多轮、重启与回放，原模型和模型预算固定；
   再独立验证真实供应商及完整来源审核。现有 20 次冻结直接作者对照已记录在
   [对话时间与自然度调查](dialogue-overlap-and-quality-2026-10-27.md)，其小样本未支持
   放松分析措辞或移除 appraisal 后已改善，不能拿源码阅读推翻该观察。

上述是待检验的设计假设，不是已部署修改、真实对话效果或“麦麦优于 Girl-Agent”的结论。
