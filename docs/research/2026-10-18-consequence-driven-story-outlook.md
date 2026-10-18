# 多线命运大纲：条件后果与滚动修订

2026-10-18。以下首先记录首次研究：按用户要求核对 GitHub 作者仓库及官方文档，当时只研究、未安装框架、未调用付费模型、未修改运行机制。后续实施核验见文末。项目边界采用 [ADR 0010](../adr/0010-controlled-high-variance-character-agency.md)。以下将外部实现、当前代码基础与建议分别说明；没有研究证明某框架能够保证多年陪伴的叙事质量。

## 结论

年度大纲应是**多条持续生活线及其可能变化的因果规划**。同一选择可以改变家庭资源、学业安排、工作机会与关系；变化被接受为世界事实后，命运机据此修订尚未发生的部分。不能把“年度覆盖”解释为预先确定一年结果，也不能让所有合法选择都绕回原来结局。

建议复用现有 Outlook、opportunity、planning、event detail 和 consequence 路径，补齐它们之间的故事线、条件与修订依据。这里借鉴的是成熟实现的机制，不是引入它们的运行框架。

## 已核对的一手实现

| 项目与精确材料 | 可复用机制 | 适用限制 |
|---|---|---|
| **Ink**：[WritingWithInk 的 labels/gathers](https://github.com/inkle/ink/blob/master/Documentation/WritingWithInk.md#gathers-and-options-can-be-labelled)、[全局状态](https://github.com/inkle/ink/blob/master/Documentation/WritingWithInk.md#1-global-variables)、[threads](https://github.com/inkle/ink/blob/master/Documentation/WritingWithInk.md#2-threads) | 分支可以合流，但用标签与共享状态保留先前选择，影响后续内容；threads 能组合不同来源的机会。可借“共享状态承接跨线后果”，不必把每个选择展开成全年完整树。 | 它是作者编写的交互叙事语言，不是 LLM 世界导演；其 threads 是内容流组合，选中一个机会会丢弃其余临时流程，不能直接当作持续并行的生活线程。合流本身也不保证选择有实质影响。 |
| **Yarn Spinner**：[官方 storylets primer](https://github.com/YarnSpinnerTool/YSDocs/blob/main/docs/write-yarn-scripts/advanced-scripting/storylets-and-saliency-a-primer.md)、[saliency](https://github.com/YarnSpinnerTool/YSDocs/blob/main/docs/write-yarn-scripts/advanced-scripting/saliency.md)、[虚拟机候选选择](https://github.com/YarnSpinnerTool/YarnSpinner/blob/main/YarnSpinner/VirtualMachine.cs) | 内容块以当前条件决定是否可用，再从合格候选中挑选；状态改变会改变后续可用内容。可借“先检查条件、再生成/呈现机会”，让同一家庭变化同时影响不同生活线。 | 它主要运行作者内容与策略。内置随机/优先级挑选不能迁移为角色行为选择器；本项目代码只验证事实和权限，角色是否接住机会仍由 CharacterInterior 决定。 |
| **Concordia**：[官方架构说明](https://github.com/google-deepmind/concordia#how-it-works)、[Sequential.run_loop / resolve](https://github.com/google-deepmind/concordia/blob/main/concordia/environment/engines/sequential.py) | 明确区分角色提出行动与 Game Master 结算世界后果；后果成为后续观察。代码先调用角色 `act`，再将 putative event 交给 GM `resolve`。可借“真实选择→实际后果→新世界状态”闭环。 | 它是 LLM 社会模拟库，所读路径不提供本项目级来源闭包、CAS、effect-once 与不可变账本保证，也不是年度多线规划器。不能将 GM 的一次自由文本输出直接升级为已发生事实。 |
| **Generative Agents**：[plan.py 中 _long_term_planning / _determine_action / _create_react / plan](https://github.com/joonspk-research/generative_agents/blob/main/reverie/backend_server/persona/cognitive_modules/plan.py) | 日级粗计划按需细化；收到观察后可插入聊天或等待，并重写受影响时间段的日程。可借“定期续编＋事件打断＋局部修订”，避免每次对话都重写全年。 | 这里的 long term 实际是一天；当前 reaction 实现主要是聊天/等待，还有关键词判断和固定时段限制。它不证明任意重大后果都能自动修订年度故事，不照搬这些行为规则。 |

以上两类应分开：Ink/Yarn 提供**作者式分支与条件内容**；Concordia/Generative Agents 提供**LLM 主体与环境反馈循环**。本项目需要后者的权威分工，加上前者的条件与跨线状态连接；不能只加更多提示词要求“精彩、曲折”。这是本次研究的工程判断。

## 当前基础与缺口

代码核对范围为 `world_outlook.py`、`world_outlook_runtime.py`、`world_outlook_author_schema.py`（2026-10-18 工作树）；与既有[长期故事研究](long-horizon-story-system-2026-10-07.md)的自主性边界一致。

- 已有 `threads`，支持 1–6 条；机会可关联多条故事线，并有条件、前置机会、来源与失效/修订机制。这是可复用基础。
- 年度 `long_term` 目前主要是日期区间、情境和 `planning_key`，缺少明确的故事线关联、成立假设、可能的不同后果及后果如何改变后续阶段的约束。已有线程字段不等于年度多线因果已经验收。
- 当前新大纲要求覆盖未来 365–730 天；剩余不足 330 天时续编，`review_at` 最迟七天；具体规划还有自己的复核时间与临近细节窗口的补充。这不是“每月固定重写一次”。周期复核也不等于每次一定生成全新大纲。
- 现有终态/来源变化能触发部分重审；仍需核对并补齐“角色合法选择→已接受后果→受影响故事线和客观规划”的及时修订与反事实验收。不能把尚未实现的联动写成当前能力。

## 建议补齐的最小契约

1. **同时维护生活线与条件性远景。** 例如学业、家庭经营、同学关系互有独立问题和可能交点。不是强制每条都制造苦难；每条有真实来源、未结问题、当前状态与下一次复核依据。年度阶段关联相应线程与成立假设，远处保留不确定性。
2. **区分方案、选择和结果。** “她愿意担保”是角色决定；是否真的签署、贷款是否发放、经营是否失利分别是行动和世界结果。命运机可设计有依据的风险与机会，但不能把一次泛泛聊天、拒绝安慰或未回消息直接解释为角色已承担财务责任。
3. **允许跨线且长期的实质后果。** 有因果依据的家庭财务恶化可以使未来住房、消费、打工与学业安排改变；已经确认的学籍、合同等约束须保留。角色如何理解、是否求助、是否休学，继续由角色选择。困难可以发生，系统不能以“剧情需要”强制她求救或迫使用户扮演拯救者。
4. **复核有两种时机。** 时间复核维持远期覆盖与近期开课安排；新事实破坏假设、行动/后果终态或机会失效时，事件驱动复核受影响线程。合并同一世界版本的一批变化，避免每句话触发一次全年生成；更新范围和费用优化不能漏掉重大变化。
5. **修订未来、保留过去。** 继续使用当前固定世界快照、版本、来源校验、CAS 与可重放提交。新 Outlook 标明它回应的已接受变化及替换范围；旧稿、失败、已发生后果保留，失效未来安排不再被当作事实或角色已知信息。
6. **控制长期规模。** 活跃线程、近期机会和细节窗口保持有界；远期按阶段描述，不枚举全年每分钟和全部分支。已结束线归档但可按来源重取；不能不断把历史全文复制进新提示。限额控制存取规模，不预设角色的语义选择。

这些是建议中的必要结果；具体字段应优先扩展现有模型，避免另建剧情引擎、行为评分器或强制情绪状态机。

## 能区分“有分支”与“看起来有分支”的验收

从同一份冻结世界与年度稿分叉，分别执行合法的接受、拒绝、延期、未行动，以及一个原稿未预列但合法的新选择。使用真实同一背景模型生成后果与下一版 Outlook，检查：

- 前提未发生时，相应后果不能发生；成立的新前提能改变未来机会、资源或规划，而非只改措辞。
- 一次跨线后果能同时修订受影响线程；其他线程可以继续，且不会全部被同一主线吞并。
- 新发展允许产生新阶段，旧结局可失效；不能为了按原稿收尾暗改角色动机或倒填历史。
- 按行动时间、结果接受时间和角色得知时间分别读取，未来私有方案及尚未获知的结果不泄漏。
- 复核失败后的旧有效安排、待重试状态、失败记录与任务身份可恢复；验证重启后不重复结算。

该实验尚未执行；本笔记只完成一手资料研究和设计边界梳理。当前年度覆盖与普通课程细节验收不能代替上述多线、分歧后果与重规划验收。

## 本次项目核验记录

2026-10-18 对候选工作树 `181ef1c8` 和候选数据库做只读核对，未修改运行机制、未调用付费模型、未重启服务。

- [`WorldOutlookRuntime._preparation`](../../src/companion_daemon/world_v2/world_outlook_runtime.py) 在未来覆盖不足 330 天时要求续编；新稿至少覆盖 365 天。规划使用各自的 `review_at`，细节覆盖结束前两天补齐，持续期间结束前七天复核。新稿 `review_at` 不迟于七天。
- `view().needs_revision` 会考虑来源 token 改变、部分新终态、机会失效和复核到期。`_TERMINAL_SOURCE_EVENTS` 及 `_scan_terminal_sources` 处理已完成／放弃活动、事件结算／取消／过期和事实变更；这不等于任意私下想法或任意角色选择都立即唤醒命运。普通变化仍依赖后续可执行的后台时机，积压可能增加延迟。
- [`OutlookStage`](../../src/companion_daemon/world_v2/world_outlook.py) 仅有阶段标识、日期、情境和可选规划键；`OutlookThread` 有问题、状态与历史锚点，机会可连接多条线并检查状态／token。这说明有复用基础，但年度阶段缺乏明确的线程和因果分歧表达。
- 本次抽样候选修订 **15 / `ef82231073cf`**：5 条线程（4 开放、1 关闭），10 个年度阶段主要为学期与假期，15 个机会均没有显式 `conditions`；部分机会关联课程与书店两线。没有条件不证明未来永不分歧，但这份样本不能作为重大选择能改变年度走向的证据。
- 现有 [`ObjectiveBiographicalTransitionDraft`](../../src/companion_daemon/world_v2/life_development_draft.py) 可将具体候选后果承接为客观人生坐标；并未在本次证明完整的家庭经营／债务／破产过程。严重经济变化需要真实执行和有来源的持续后果，不能仅写入年度草稿便成为事实。
- 运行 7 项离线机制测试通过：角色计划状态只影响依赖分支、来源／调度变化使旧机会失效、修订保留独立机会且旧快照看不到未来稿、细节覆盖自动续编并保留超时。使用 fixture 模型，不是实际多线故事的反事实验收。


## 后续实施核验（同日）

已在既有Outlook、Planning、Occurrence和来源审核上补齐多线发展、年度阶段来源条件、接受后果逐项回应、即时准备唤醒和准确前驱重规划；未新增剧情运行框架。六种受控选择经真实GLM世界作者／审核／命运修订后推进模拟世界两天，已证明持续租约状态差异、未行动不制造义务及新申请形成第三条线。公共开放时段没有被硬改；实际差异由已接受状态和规划发表链支持。实现权威边界、旧审核升级方式及范围见[ADR0025](../adr/0025-revisable-world-outlook.md)。

真实过程暴露“义务被标签写成已付款”的错误，已补齐客观标签精确审查，并在原冻结快照真实重审拒绝该负例。失败、旧版本及未知计费记录保留。证据目录为 `output/private-audits/story-choices-qualified-20261018/`；这组实验固定角色选择，不证明自由对话人格、全年故事质量、真实QQ或长期生产稳定性。
