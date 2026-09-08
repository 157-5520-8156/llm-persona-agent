# 2026-09-08 自主迭代与逐回合试聊

本阶段由用户明确授权创建 Goal 并持续迭代。仍在 `codex/living-continuity` 隔离分支工作，
生产数据库、配置与实际 QQ 不在写入范围。初始试验批次总预约上限 ¥2，每个 fresh world
上限 ¥1；此前三次试验的 ¥0.8820866 持续累计，不能用新数据库抹去测试成本。该批次已
封存；后续独立对照另列上限并继承全部累计占用，不重新打开旧批次或释放其未知费用。

最新进度：隔离代码已切换新世界后果合同。真实 trial-07 在两回合后因下一笔预约
超出剩余预算停止，再次暴露聊天事实漏报和来源审核的语义误判；预算拒绝导致的
审计异常已窄修复。累计已知约 **2.7813168 元**、保守占用约 **3.0957838 元**。
细节见本文“新默认下的真实 trial-07”一节，以下按各阶段保留原始证据。

## 初始封存批次收束时的结论

已执行真实逐回合聊天、生活推进与相同输入对照；当前仍未达到真人感阶段验收。
已经修复来源字段被丢弃、生活意图缺少执行消费者、活动来源不可读、部分调度断点、
World Author 内心权限矛盾和 NPC 当地时间输入。后续真实样本仍暴露三个实质缺口：
聊天用空声明讲出不存在的当前生活、focused critic 漏检既往前提、NPC 在同一响应中
混用上午和下午。当地时间输入补齐、审查合同可执行，都不等于模型行为已经正确。

最后的 stock thinking 对照耗尽 4096 输出 token，正文为空，本批不再追加付费调用。
所有可取得的主账已知用量合计 **1.5980047 元**，另有历史未知 **0.241365 元**；
campaign 保守占用 **1.9124717 / 2 元**。其中最后一笔已知费用 0.056898 元包含在
未释放的 0.13 元 allocation 内，不能再把它叠加到占用金额。跨阶段已知费用
**2.4800913 元**；未对供应商发票，未验收每月 100 元。

后续独立 low 对照新增上限 **0.13 元**，实际已知 **0.0188649 元**，仍无最终正文。
该次 low 对照结束时，跨阶段累计已知 **2.4989562 元**，累计保守占用
**2.8134232 元**；旧 campaign、历史 unknown 及旧 high 的完整 0.13 元占用均不变。
其证据与局限见本文对应的 low 对照段落。

2026-09-08 用户随后明确纠正范围：小屋已暂时弃用，本轮不做角色移动。此前提出的
移动纵向链停止；它不再是本 Goal 的待交付项或继续迭代的前置条件。已创建的空隔离
worktree 未修改任何代码，未集成新的地点或移动机制。当前重点回到已有聊天、生活
经历、记忆和自主联系之间的连续性；不将聊天事实漏报默认改造成物理位置模拟。
主动联系的测试仍须跨过实际已声明的机会时点，短时无消息记录没有证明角色自主
选择沉默。

## 恢复迭代：发送事实与等待连续性

公共应用链在临时 SQLite 中复现：角色声明等待意图、消息获得 `provider_accepted`，
随后回执变为 `failed` 或 `unknown`。Action 已进入对应终态，但部分 pending/living
hope 读取器及主动联系 self-history 仍把旧接受回执算作“已发送、无人回复”。
这会把传输失败误呈现为用户没有回应，不能据此评价角色是否追问过多。

现在统一以每个 Action 的最新回执判断当前发送证据，保留首次可回答回执的原始时点。
后续失败或未知会退出当前等待证据；迟到的成功回执可以恢复证据，但不会把原始发送
顺序移到已收到的用户回复之后。同一表达计划的其他消息成功不能替代邀约消息的失败。
历史读取严格使用修订截止点之前的回执和锚点，后来的失败不反写过去。

原始角色等待意图和事件没有删除，代码没有决定失落、继续期待、追问或沉默。
self-history 同时区分平台接受与已交付，删除“每个期待都会再次唤醒”的保证性描述。
实际公共应用、回执入口、冷重启和下一回合模型输入覆盖失败/未知两条链；另覆盖迟到
交付、已有用户回复、多消息锚点和历史截止点。相关 **98 项测试通过**，Ruff 与 diff
检查通过。这里使用 fixture 角色与传输端口，只证明机制；本次未增加真实模型费用，
尚无这些修复之后的真实聊天或生产 QQ 验收。

## 恢复迭代：经历记忆的恢复队列

另一条公共 SQLite 反例创建两条有真实类型来源绑定的 Experience。第一条的记忆判断
发生技术失败后，恢复入口反复选择它；即便它还没到重试时间，或已用尽既有的八次
重试，后面的经历也永远轮不到。失败持续三天并冷重启仍可复现。这是后台恢复入口
的队头阻塞；不能外推为每条经历都丢失，正常提交后的直接处理路径也存在。

选择器现在跳过未到重试时间或预算已耗尽的来源，每次仍只处理一条合格经历。已经
持久化的角色决定、尚待接受的候选优先恢复，无须再问一次角色。原失败记录、八次
上限、退避时间、事件合同和预算机制保留；技术耗尽没有被转写为角色决定“不记得”。
公共测试覆盖等待期间后续前进、耗尽后的三天与多次冷重启、角色 retain/no_change、
已作决定但内容存储暂时失败后的恢复与不重复采纳。集成后 77 项相关回归通过；这仍
是 fixture 角色的机制证据，尚不能证明实际模型愿意记住重要经历。

两项修复集成点 `277d088c` 的完整 120 场景冻结门禁也通过。所有 manifest 字段及
整文件字节与上一基线一致：语义 hash 仍为 `8f3579b38dbc6895330db8f02bbbbbf12eb78604d11d2f3f5424724313459f60`，
JSON SHA-256 仍为 `56ad03ebc1649166ab3089f21526ab5f6f955864a8872b8d829df746cc9ce1d3`。
证据为 `baseline-receipt-experience.json`，基线版本 `.99` 未调整；仍只代表固定离线机制。

## 恢复迭代：精确撤回多个安排中的一条

公共 SQLite 链保留两条同一 `schedule.commitment` predicate 的 set Fact，并让角色
采纳相应记忆。角色随后明确选择撤回一条，原 adapter 仍只接受 single 撤回，原始
请求及一次纠正后均为技术失败；旧安排继续有效。此前 trial-02 的十次 `retain:false`
则是模型确实没有选择记录，不能把两种问题混称为缺少事实消费者。

新增同一 Fact 模型通道的显式 `target_fact_ref`，系统将它绑定到已提供成员的版本、
值 hash、接受事件及新 Observation。决定先持久化，再由 proposal/effect 两处检查
同一授权；删除新绑定、改用旧 policy、换目标/版本/hash/用户/来源均不能通过。
批处理中先执行一条不会让另一条被偷偷改绑；撤回前后崩溃均重用原决定和效果。
旧 `.3` single/no_change 决定仍按旧来源上下文恢复，不获得新 set 撤回能力。

当前资料最多提供 16 个可消费的 `.2` active、非 withhold set 成员，另保留旧 single
资料。不在资料中的成员不能猜测撤回，所以尚未解决任意久远安排的检索覆盖。
已撤回安排退出当前事实；其 before-image 与截止时间仍进入历史回忆，角色原始记忆
不被自动删除。没有关键词取消规则、额外模型通道、行为脚本或小屋机制。

新身份为 adapter `.4`、source-context `.2`、`fact-member-withdraw.1` policy；保留
既有 single 与 `.1` 历史编码，全局 bundle `.56` 沿用现有追加 policy 的方式。
此前可被手工拼出的无绑定 `.2` set 撤回现在会被拒绝；旧 adapter/lifecycle 无此
producer，但这不能证明所有生产历史都不存在此类事件。未扫描或迁移生产数据库，
不能宣称任意既有 `.56` 账本都兼容。

集成点 `4c6d0538` 的 **236 项联合回归通过**，Ruff 与 diff 检查通过；完整 120 场景
冻结门禁再次通过，`baseline-continuity-recovery-final.json` 与前述 manifest 全字段
及字节一致，未调整 `.99` 基线。这些均为 fixture 机制证据，不是新版本的真实聊天
验收。本轮项目真实模型调用费用增量为零；原封存试验费用与未知预约均未重算或释放。

固定实现 `dbac6f29` 的更广 Fact/reader/accepted-contract/authority/registry 15 文件
共 175 项回归通过，其中包含前述联合集合中的 61 项，不重复累加成总数。固定提交
独立审查无确认 P1/P2；这不是未审计生产历史的兼容证明。

成本材料按实际公共两成员反例构造：messages 从 4,810 到 7,079 UTF-8 字节，增加
2,269 字节，其中 `current_set_facts` 数组 1,656 字节。16 条短测试正文的构造样例中，
数组 13,136 字节，messages 从 4,758 到 19,235 字节；这不是生产输入 token 上限，
长正文还会继续增加。保持原始来源文本，不截断后冒充完整事实；既有 provider 在实际
wire payload 上预约预算，较长输入可以因此被拒绝。未新增模型调用通道不等于成本
不增加；没有依据这组字节数外推月费，也没有把保守预约金额当成实际费用。接下来的
成本优化应减少模型不必复制的来源元数据，并继续保留 host 的完整来源绑定。

## 来源输入精简与聊天提示自相矛盾

`295c0fbd` 只精简新 set Fact 的模型展示。每条仍保留成员 ID、predicate、完整原文及
提交、更新、Observation logical/received 四个时间；版本、事件和值 hash 留在 host
完整行中，继续绑定角色选中的精确目标。single 资料、16 成员选择和隐私过滤不变。
`fact-set-source-view.1` 进入新请求身份；旧 `.3/.4` 已持久化决定沿用其原始请求
hash、审计和恢复路径，不重新调用模型或把历史重标为新展示。

同一短来源 fixture 的实际 HTTP 请求体，两成员从 7,159 减为 6,290 字节（12.1%），
16 成员从 19,384 减为 12,038 字节（37.9%）。这些是构造样例的 wire 字节，不是
生产 token 上限、实际费用或月费预测；没有截断长原文，也没有新增模型通道。
独立固定提交审查未发现确认 P1/P2。

重新核对 trial02/04/06 的 24 次真实聊天请求和原始响应：全部明确返回了空
`world_claims`，并非 adapter 把非空声明删掉；也不代表 24 次全部含外部事实错误。
反例包括把明确标为“惯常作息，非今日实际活动”的图书馆背景写成刚刚到达，以及把
自己此前“去拍几张”的表达写成已经拍完。单个输入中的干扰项不证明因果，但空声明
不能被视作正文没有事实。

`99f10157` 移除照片接口样例中无来源的“刚才那张”，改为尝试分享的意图；compact
reply-only 与 full-turn grammar 澄清是否谈外部事实由角色选择，谈起后匹配声明和来源
是合同要求（full-turn 同类表述在合入后的核对中一并清理）。
最初改文使一个紧凑请求达到 46,044 字节；随后缩短说明并通过原有 46,000 字节门禁，
没有提高预算或扩大 schema。该清理没有被当作事实漏报的语义修复。

根分支合入后 117 项 Fact/提示回归、20 项撤回/历史恢复回归，以及 5 项 compact/
reply-only 检查通过（不同测试集合合计 142 项）；Ruff 和 diff 检查通过。完整 120
场景正常冻结门禁也通过，`baseline-fact-compact-prompt-final.json` 与上一 manifest
全字段和字节一致，JSON SHA 仍为 `56ad03ebc1649166ab3089f21526ab5f6f955864a8872b8d829df746cc9ce1d3`。
未调整 `.99` 基线。这些改动尚无新的真实聊天验收。

full-turn 后续清理的 `486f84f4` 再次通过上述 5 项紧凑检查和完整正常冻结门禁；
`baseline-fact-compact-prompt-followup.json` 仍与前述 manifest 字节相同。

## 新复现：世界偶发事件可以夹带未由角色选择的行为

独立只读诊断及 root 运行现成公共 fixture
`test_world_author_can_commit_a_free_adverse_world_contingency` 均确认：初始状态只有
Clock 与可用地点资料，没有 Plan、行动意图或角色决定，World Author 仍能提交冰雹
事件及“她及时收回了手账”的候选全文。fixture 断言角色调用为零，Occurrence 已
激活且保存该原文；本次该测试通过。这不是已授权“收回手账”行动受到天气影响的成败。

其 reviewer 是返回 supported 的 fixture，因此它证明机械接口允许混合作者权限，
不能声称真实 critic 已在这个新样本上放行，亦未在本次测试中推进到最终 Experience。
当前生产代码的 `world_contingency` 分支跳过初次角色决定；LifeAftermathRuntime 按已记录的
抽样选择候选，复用 WA 的结果正文。另一类 `character_choice` 分支虽然允许角色选
候选，结果接口主要仍是 selected token，并没有角色自行创作最终动作正文的独立载体。

这使权限缺口超出“漏掉了一句新情绪”：`LifeDevelopmentOutcomeDraft.text` 的当前
合同本身允许 objective actions，但结果来源没有分别绑定环境变化和角色自己的回应。
下一处设计工作应版本化区分这些来源，保留世界事件独立发生及角色决定如何回应的权利。
上游 `intention_summary` 是未来计划，不能冒充已经发生的角色行动。尚未实现新协议，
旧结果和审计不作静默重写；这项工作不包含小屋或移动。

## 已确认问题与机制修复

- compact `reply_only` 与 `full_turn` 曾无条件把角色的 `world_claims` 清空。现在保留其原文、
  scope 与 source refs，缺省/null 仍兼容空声明。角色陈述外部事实应明确提供声明，但结构校验
  **不能证明自然语言正文没有漏报**，是否改善必须继续核对真实请求与交付。
- expression materialization 曾删掉非法引用、保留对应正文。生产开关与无人调用的 helper 已移除。
  实际 CLI → host → HTTP adapter 的本地伪造来源反例现在不能产生 `ActionAuthorized`；
  允许既有显式服务故障通知，不把它当成角色回复或角色选择沉默。
  实际 HTTP 本地反例另核对每次尝试恰好原始＋一次校正：两者 snapshot ID 相同，校正请求包含
  `role_result_correction` 与精确的时态/来源失败原因。adapter 单次失败不代表最外层没有重选。
- Deliberation 曾对 Capsule 未绑定的 evidence 再做一次相同剥离。现保留原候选并报告精确失败引用，
  公共回归覆盖无恢复、已有同角色恢复成功、第二次仍非法；正常来源保留并成功。七项旧反向测试
  曾把伪造来源通过当成成功，现恢复拒绝；另两项正向修正 fixture 的 speaker/scope，继续要求通过。
- 已有 World Author novel-origin reviewer 的 `.2` 请求曾完全遗漏 premise。`.3` 在同一次请求中
  包含完整 premise 与权限边界；审阅者用原文片段标出未来源化的既往经历及越权内心。
  片段不存在、用 outcome 片段代替 premise、判 supported 却同时给出违规坐标都会失败。
  不增加审核调用次数；完整 premise 会增加该次请求 token，已有预算预约使用实际新请求。
- 保持当前 one-shot 组装，不复活已退役的泛化 LLM reviewer，不使用关键词拦话、模板补答、
  固定社交规则或擅自生成当前生活事实。

## 可复用的试聊入口

`scripts/run_world_v2_longitudinal_audit.py --interactive` 复用安装的 QQ host 与长程运行器。
输入仍是外部用户消息；角色选择由正常模型链产生。命令使用 stdin JSON lines：

```json
{"id":"hello","at_minutes":0,"text":"今天怎么样？"}
{"wait_until_minutes":20}
{"id":"return","at_minutes":20,"text":"刚忙完，回来了。"}
null
```

每次输出上次观察以来的交付、输入、终态与技术失败，操作者可读完再决定下一条。
等待只推进已登记的时钟与调度，不制造用户消息或替角色选择沉默。命令不能写角色动作、
越过旅程边界或回拨时钟。stdin 可取消，无阻塞读取线程；退出仍关闭 host 与其资源。
`operator-commands.jsonl` 纳入产物 hash，timeline 保留实际消息。`null` 或 EOF 记录
`operator_stopped`、`completed=false`，不能把主动结束试聊当成完整旅程通过。

首轮独立审查发现操作者等待后的账期复查缺失、终点未显示尾段交付；分别补充公共 runner
回归，在任何新模型工作前重查真实账期，并以只读最终观察显示尚未读过的尾段。

本地回归使用实际 host、fixture 模型与本地 HTTP 对端；只能证明机制，不评价人格与真人感。
真实试聊证据将保存在 `output/adaptive-companionship-2026-09-08/`。当前阶段尚未完成，
长期六维、真实 QQ 与每月约 ¥100 均未验收。

## 第一轮真实对话与继续修复

trial-01 在 `c49e8996` tracked clean 上运行。输入“嗨，今天过得怎么样？”后，实际捕获交付为
“还行吧，早上在图书馆看了会儿书，你呢”。请求中 current/past authority 均为空，没有 active
activity 或已结算 Experience；ledger 15 接受的表达仍为 `world_claims=[]`。这再次反证
“保留声明即可防止正文虚构”。当时只保存请求字节及归一化结果，不能判断模型原 wire 是省略、
null 还是显式空数组，也不能把已归一化 proposal 的 response_hash 当成原始 HTTP 响应 hash。

随后实际 PTY 输出触发 `BlockingIOError`，本轮记 `technical_failure:BlockingIOError`，
1 次输入、2 次模型调用（聊天和互动事实草稿）、¥0.0250003；所有预约已 settled，
连同此前三轮为 ¥0.9070869。这次工具故障不是角色沉默，也不是供应商故障；原试验目录不重用。

后续修复：

- 撤掉 world_claims 属于行为任选字段的说法，统一“是否陈述由角色决定、选择陈述后来源不能省略”。
  给真实 compact slim 示例及其说明 schema 补上已有字段，并加入仅展示来源映射的占位格式；
  不提供可照抄为经历的具体生活样例。替换重复文字，实际请求仍通过原 46,000 字节门禁。
  描述性 slim 字段由 25 增到 26，实际 provider carrier 仍为两个字段，未扩大 provider 工具限制。
- 真实本地 PTY 重现 dup(stdin) 共享 open-file-description 导致 stdout/stderr 一同进入非阻塞。
  审计入口改用独立终端描述符与可取消的就绪读写；262KB 观察在消费者延迟读取时保持完整，
  不阻塞事件循环、不改继承的 fd flags、不裁剪输出或吞掉错误。最终 CLI 摘要使用同一路径。

提示、示例与字段说明一致只是改善模型可用性，不是语义完整性证明。继续用真实逐回合对话复测。

## 第二轮：真实十回合暴露生活读写断点

trial-02 在 `4d62611e` tracked clean 上运行，操作者按角色的实际回复逐条输入，未预写后续
角色行为。10 次输入覆盖 79 分钟 Logical Time，其中有 30 分钟及 40 分钟的无输入区间。
最终主动停止，`operator_stopped / completed=false`，不是整个旅程通过。

这轮新增 HTTP 响应原字节旁路留存：实际消费的流被有界、私有地捕获，不提前读取、不额外
调用模型；正文缺失、截断、取消与传输错误保留独立状态。29 份请求内容 hash、29 份完整响应
正文的长度及 SHA256、577 个 event payload hash，以及 manifest 的四份产物 hash 均复核一致。
流 EOF 仅证明响应字节完整；SSE 工具参数另外重组检查，不能把 EOF 当成模型语义完成或交付证明。

| 对话观察 | 对照证据与判定 |
| --- | --- |
| 首轮称上午在图书馆；第二轮称刚在翻城市随笔 | 首次输入尚无 accepted activity 或 Experience；第二次之前已接受的活动却在校园服务楼，主题是征稿。前两次原始 SSE 工具参数均显式给出 `world_claims=[]`。说明来源字段保留及提示统一仍未解决正文漏报。 |
| 已接受的生活对聊天不可读 | seq52 计划、53 角色接受、54 接受记录、55 活动开始存在；聊天 compact context 没有可用的活动描述及当前事实来源。花费模型调用生成的生活，未完整进入同一角色的聊天判断。 |
| 37 分钟说想拍照，38 分钟说会去；79 分钟声称已经拍了校园落叶 | seq422 只有 appraisal/expression，`activity_transition=null`；45 分钟 seq536 暂停的仍是原征稿计划。整轮只有一个计划，没有新的校园摄影计划、完成记录或 Experience。聊天没有安装显式生活意图到计划的生产消费者，口头想法未执行，后来却被讲成经历。 |
| World Author 让她想起先前半成稿、明天研讨会的阅读任务 | 原始候选 `5b8273ed…` 含这些未来源化前提；focused critic `2c3159a5…` 实际看过完整 outcome，却判 supported，并把它们一概当成分支内想法。随后角色选择 `fc8b4ebc…` 继续吸收“笔记本里一直没写完的随笔”。这次是语义误判，不能用“已补全审查输入”声称解决。 |
| 能复述分享会改到周四，但把同事修改要求关联到周五项目会 | 会话原文只说同事改分享会材料。日期记对和关系绑定记错并存；十次互动事实草稿均未提交事实，未证明持久记忆或跨周回忆能力。 |
| 两段无输入区间没有新的可见消息 | 仅是本次观察；没有据此证明角色显式决定不追问，也不能证明主动联系、长期多样性或情绪演变。 |

费用按本地记录的供应商 token usage 与已安装费率重算，未对供应商账单：

| 用途 | 调用数 | CNY |
| --- | ---: | ---: |
| 入站聊天 | 10 | 0.2003190 |
| 互动事实草稿 | 10 | 0.0034574 |
| 世界生活候选 | 1 | 0.0350175 |
| focused novel-origin 审查 | 1 | 0.0114195 |
| 角色生活选择 | 1 | 0.0140600 |
| 活动生命周期选择 | 5 | 0.1159795 |
| 私人印象反思 | 1 | 0.0243297 |
| 本轮 | 29 | **0.4045826** |

29 笔预约均 settled，无未知费用，无模型技术失败。阶段新增累计 **¥0.4295829**，连同此前
三轮总累计 **¥1.3116695**。这份短时 fresh-world 样本不足以外推每月成本；没有实际 QQ 发送。
下一步分别修复生活的可读来源、角色显式生活能力的完整执行链，再以新试验目录复测。

既有 focused critic 的 outcome 说明随后改为先逐分支区分事件与其先决事实，再检查内嵌断言：
“想到/记起/准备做”这个心理活动本身不证明其中提到的旧经历、已有物件或外部安排；未来日期
也不是已有预约的来源。保留新行为、感受和分支内新事件的自由，不增加审查调用、字段或通用
reviewer。生活 runtime/production 的 118 项本地回归通过，只验证现有契约和调用链兼容；
该说明能否降低真实语义误判仍未验证，不能把本地通过记成上述反例已修复。

## 集成测试继续暴露的调度断点

聊天生活能力的本地 HTTP 公共旅程先成功创建 30 分钟计划，并在第 1 分钟由角色选择开始；
第 2 分钟重启后重放一致，第 3 分钟终止检查却报 `unprocessed_due_before_end`。根因是生产
`declared_due._extract_plans` 对 active/paused 仍返回早已消费的开窗时间，同时丢失真正的
关窗唤醒。已先用公共 collector/clock selector 复现红测，再让 active/paused 使用既有
`closes_at`；planned 保留开窗，completed/abandoned 无待办唤醒。代码不决定角色是否参与或
如何结束，既有生命周期仍负责合法转换；没有放宽审计终态、缩短计划或补造完成经历。

## 已合入的当前活动读取与证据传递

`8d89f9d7` 合入 `a033bfe2`：读者逐项验证当前 cursor、actor、隐私、已接受计划及最新
ActivityStarted/Resumed，再通过 Proposal 与 sidecar 哈希取回角色当时接受的意图。当前快照
`.19` 增加 `current_activities`，边界 `.9` 增加当前活动来源；意图明确标为仅证明接受的
打算，不证明内嵌旧事或未结算结果。新增视图不收 planned/paused/其他角色/withhold 项；
暂停后的可读连续性仍是限制。后台 profile `.2` 将此私人意图排除在 World Author 输入外，
角色生活、主动联系及内省保留它。

实际 HTTP 回归进一步发现，两个 compact context 入口原先只为照片保留来源封套，丢掉了
world_life bindings。修复后仅将模型实际选中声明的事件带入 Proposal evidence；不把整份
Context 证据复制到每条消息。该改变也覆盖旧 world_life 来源的共同通路，不只修新活动字段。

相关子集 593 项通过；加强后的 HTTP active/resumed/非法 past 三场景另行复验通过。根分支
集成后 33 项活动/HTTP/clock/one-shot 检查通过，调度相关另有 72 项 lifecycle/ecology 检查
通过。HTTP 正向要求准确的事件 id、world revision、hash、Action delivered、terminal
receipt 和 cold replay；反向要求原 pinned Context 的一次纠错后仍非法则无 Action。

在还未合入聊天生活意图能力的干净 `8d89f9d7` 上，另跑完固定 120 场景：与 `.96` 的
全部业务字段、字段集合及顺序一致，仅 replay hash 改变。候选保存在
`output/adaptive-companionship-2026-09-08/baseline-current-activities/`，没有据此更新安装的
冻结基线。最终组合仍需重新验证；本地固定模型与模拟回执不证明真实模型会自发完整引用。

## 继续集成：聊天意图、完成来源与真实调度入口

`bf66fa98` 将角色同一次聊天中显式提交的 `life_intent` 接到已审计 Proposal、
ActivityPlanned 和原生命周期选择。普通聊天文字和 `wants` 不会被代码解释为计划；
表达/Appraisal 的失败不会抹掉独立的合法意图。计划只授权本人的私人活动，不授权地点、
NPC、外部结果或既往经历。原选择时间、原模型来源、同一 Observation 的 effect-once
以及接受 CAS 的 30/120 秒有限恢复均有持久绑定。具体契约见
[`chat-life-intent.md`](../design/chat-life-intent.md)。

公共 host 测试继续找到了三个仅修领域函数不能解决的问题：

- `fe4cc782`：调度器原先只在 `life.ecology` 调用生活 owner；活动开关窗虽然推进 Clock，
  却未交给 owner。现在根据已注册的 owner 元数据处理边界，包括与 Action 同时到期。
- `f1fb95f2`：Clock 已提交但被可见聊天抢占时，重启从原 Clock transition 找回尚未处理的
  边界。复用原时钟与机会身份，不补造新的角色考虑机会。
- `58cdc37e`：生活 owner 已把下次处理时间推迟，通用 collector 却还保留 projection 中的
  旧时间，造成多余唤醒和 `unprocessed_due_before_end`。现在 owner 的有效时间覆盖同类
  静态投影时间，显式 `None` 也表示该 owner 没有待办。其他 owner 的到期项保留。

`7334b49e` 进一步安装最近三条已结束的聊天活动读取。它逐项校验原计划和模型意图、
当前 actor/cursor/隐私以及真实 ActivityCompleted 的事件/hash/revision。可用的 past
来源只证明生命周期结束，既不证明目标完成，也不证明意图文字内嵌的旧事、照片内容或
任何其他结果。没有生成 Experience，也没有把该私有材料交给 World Author。

五分钟本地 HTTP 旅程覆盖计划、开始、完成、重启、第二次聊天引用和冷重放；修复过期
cadence 后 manifest 才真正 `completed=true`，没有缩短场景消除红测。第二次表达获得
`provider_accepted` 且 `is_terminal=false`，因此只能称本地捕获接受，不能称终态送达。
root 合入后的完成/当前活动、来源、后台隔离及入站契约定向检查 **379 项通过**。

在较早 `bf66fa98` 上进行的一次完整回归为 **6436 passed / 24 failed / 19 skipped**。
其中旧 compiler 默认被新的 registry 版本错误抬升的问题已由 `11e6b93d` 修复；
机制资格表、可选字段说明、平台反向依赖和 slim 描述字段数量由 `1d33635d` 修复。
通用 Proposal 默认 `.1`、显式生活意图 `.3`、FactCommit v2 的独立 `.2` 保持分开。
实际 provider carrier 仍是两个字段，原 **46,000 字节**请求上限未提高。
这一轮完整回归不能记为通过；最终组合仍须重新跑完整回归和严格 120 场景基线比较。

`1fef769b` 随后补齐晚接受计划的初次考虑：两次 CAS 后 150 秒才接受的计划保留原开窗，
151 秒的真实 Clock 提供原生命周期能力，角色选择后才开始。计划事件/hash 与原模型意图
共同绑定机会；合法 no_op 关闭本次来源，技术失败则在 30/120 秒后有限重试，最多三次，
每次仍沿用原模型及一次受约束纠错。它不替角色开始、放弃或缩短活动。

复核真实找到 Core 已持久化 no_op、领域 consideration 日志尚未写入的中断窗口。恢复现在
读取原终态和 prepared 状态，校验 hash、actor、cursor、原模型、capability 与 typed source；
原决定使用原 author Clock，补写日志使用当前恢复 Clock。公共 HTTP 崩溃/重启回归只有
一次 lifecycle 调用和一条原 attempt 的 declined。`326cce6f` 又修复 SQLite 在校验前按
来源字段筛选的问题：来源损坏必须先报完整性错误，不能伪装成“没有原决定”而再次询问。
该损坏回归由两次调用变为一次，且不补造角色 no_op 记录。

作者侧综合 166 项通过，root 合入的聊天/完成活动/host/due 子集 54 项通过。干净
`1fef769b` 和 `326cce6f` 各自完成严格 120 场景导出：8 个根字段、每场景 18 个字段、
120 个唯一且顺序一致的场景 ID 均核对；17 个非 replay 业务字段全部与 `.96` 一致。
两份 `.97` 候选 hash 都是
`f4a4a4bed998d37de4122f41eec99d3377e1c53b1dba8d8d6927737d21c385f9`。
此时仍未安装候选：正在复核普通 cadence 的旧时间是否遮住新计划的一秒唤醒，以及完整
组合的最终回归。上述过程尚未用新的真实对话证明角色会自然使用该能力。

`4d4ade7d` / `d15f939a` 最后闭合短计划的调度边界：已到期的普通 cadence 不再遮住新
计划的未来 Clock；active 的到期时间同时遵守原 `activity_minimum_completion_delta`。
60 秒计划的公共 HTTP 回归保留窗口 0–60 秒，实际在第 1 秒开始、第 61 秒完成，没有
改成暂停/放弃，也没有改写原窗口、catalog 或历史事件。测试角色在没有合法 complete
时明确选择 no_op 等待；这只描述固定测试角色，不是生产行为规则。

root 最终组合 60 项定向通过。干净 `d15f939a` 的第三份完整导出存于
`baseline-life-links-clock/`，同样只有 120 个 replay hash 变化，全部 17 个非 replay
业务字段与 `.96` 一致；候选 SHA256 再以独立命令核对。基于这些证据安装 `.97` 及上述
`f4a4a4be…` hash。固定场景与 mock HTTP 仍不能代替真实模型、真实 QQ 或月度费用验收；
完整回归与新一轮真实自适应对话继续单列结果。

## `.97` 完整回归与第三、四轮真实试聊

干净 `47b3c4e6` 的完整回归为 **6488 passed / 19 skipped**，用时 499.22 秒。
日志为 `output/adaptive-companionship-2026-09-08/full-life-links-97.log`。
这证明当前固定机制回归通过，不能替代以下真实模型反例。

trial-03 只完成 20 分钟生活预热后主动停止，未发用户消息。五笔已知账单均 settled，
费用 **¥0.0906133**。World Author 的修正候选仍把“最近的草稿”作为既有物件，focused
critic 看过完整内容却判 supported；角色随后接受了这个前提。此前的说明调整没有消除
语义误判。775 分钟窗口先被原上限拒绝，修正为 655 分钟后通过；这个大窗口随后暴露了
机会可用时段被默认当成本人活动时长的另一问题。

trial-04 在同一干净代码上由操作者逐条回应角色实际输出：**11 次输入、112 分钟逻辑时间**，
到达 30 分钟墙钟上限而结束，`wall_time_limit / completed=false`。末轮四条本地捕获消息
已获 provider accepted，但最终还有墙钟技术终止；不能称完整旅程通过或 QQ 送达。

| 观察 | 证据及边界 |
| --- | --- |
| 首问就称刚在图书馆坐下 | 当时只有下午 13:00–15:00 的未来计划，没有 ActivityStarted/Resumed 或当前地点权威。原 SSE 参数显式 `world_claims=[]`；正文事实漏报仍在。 |
| 能区分固定周四下午三点与本周临时周五上午十点 | 两个用户断言分别形成 seq107、415 的 FactCommittedV2；第 90 分钟正确回答本周和下周时间。近期对话仍在输入中，未证明跨周、重启或纯记忆检索能力。 |
| 说休息后去倒水、站窗边 | 这段没有对应活动/结果记录，不能用聊天自己的回顾补作事实来源。 |
| 50 分钟选择回宿舍写作，112 分钟称已有开头 | seq469 是真实角色接受的写作意图，470 为 Plan、473 为 Started；因此并非完全没有生活选择。但末轮仍 active，没有完成或成稿结果来源，`world_claims=[]`，把进行中的意图升级为已有结果。 |
| 有一份世界候选确实被来源门拒绝 | 67 分钟 critic 标出未来源化的旧梧桐经历和具名熟人，seq511 为 source_closure_rejected，未接受为 Plan。该审查仍漏列同一候选另一分支的“昨晚没读完的章节”；既不能说门完全失效，也不能说它已完整。 |
| 机会窗口被完整继承 | 第二个角色选择的两个时间字段均 null，系统把机会 10:50–15:00 的 **250 分钟**直接写入 Plan。意图是回宿舍写作，地点却沿用 campus-path；不能将其概括为角色主动选择散步 250 分钟。 |

38 份请求内容 hash、38 份响应原字节长度及 SHA256、事件 payload 与 manifest 产物 hash
经只读脚本核对。38 笔预约全部 settled，无未知费用，本轮 **¥0.6105934**。
阶段四轮累计 **¥1.1307896**，连同此前 **¥0.8820866** 为 **¥2.0128762**；阶段预约上限
仍为 ¥2，此前历史不属于本阶段额度，但持续列入总账。费用是供应商 usage 按安装费率重算，
未对发票，也没有足够运行历史外推每月 ¥100。

## 调试账本镜像的实际缺陷与修复

trial-03/04 中 primary usage 正常预约并结算，第二份全局调试账本却因不认识 primary 的
reservation ID 拒收同一账单。`PYTEST_CURRENT_TEST` 默认关闭这条 hook，之前的普通
HTTP 测试因此没有覆盖它。这不是角色数据库丢失，也没有导致这两轮测试免计费。

`8ab2e628` 为 debug ledger 增加已结算账单导入：验证来源账单及其原预约，保留原时间、
token、费用与费率；目标使用独立导入身份和原账单 hash，事务写入账单及回执，不创建目标
预约，不抵消其他 pending 预算。同一账单重入不重复计费，内容冲突拒绝。既有 primary
admission 和 provider hook 不因观测成功而绕过持久证据。

作者侧 179 项相关检查与独立审查通过，集成后账本/支出子集 **44 项通过**。另把 trial-04
只读备份中的 **38 笔真实账单**导入全新临时镜像，再全部重入一次：76 次导入仅产生 38 笔
账单和 38 个回执，目标预约数为 0，原字段完整保留，费用仍为 ¥0.6105934，原 trial DB hash
不变。这验证真实历史账单的导入，不是新 HTTP callback 的端到端验证；没有补写全局历史
账本。证据在 `output/private-audits/trial04-mirror-real-bills-ji4x741s/report.json`。

同输入 thinking 对照的首次请求被供应商以 HTTP 400 拒绝：当前工具选择参数不受支持。
primary 记录为 not_billed、零 token、预约 settled；尚未产生可比较的角色回答。原始输出
位于 `output/private-audits/trial04-thinking-7cd8rcmt/`，不得把接口拒绝当成语义对照结果。

原 400 的完整 146 字节响应及其 hash、primary not_billed 记录和 settled 预约已独立核对，
仅对应 campaign entry 以比较后写入的方式结算 0，保留供应商拒绝状态。随后两次单请求
对照都使用原首问的 messages、tools、4096 token 上限及 55 秒时限，未写入 World：

| 配置变化 | 实际结果 | 时长 / 费用 |
| --- | --- | --- |
| thinking enabled，并因接口限制另改 tool_choice 为 auto | 表达在考虑下午去哪看书，没有声称当前已在图书馆；一个合法工具结果 | 27.94 秒 / ¥0.0375030 |
| 仅 tool_choice 改 auto，thinking 仍 disabled | 再次声称已在图书馆翻书，raw world_claims 仍为空 | 1.42 秒 / ¥0.0014463 |

第一项有额外参数混杂，且每项都只有一个样本，不能据此宣布推理模式解决了问题。第二项
有 16,896 个缓存命中 token，第一项没有缓存命中，两者费用也不能作为稳定倍率比较。
这两次只检验 provider 输出；没有运行角色接受、来源闭包或 QQ 交付，更没有修改生产默认。
证据分别在 `output/private-audits/trial04-thinking-auto-5fgwngtc/` 和
`output/private-audits/trial04-fast-auto-h7pz03qo/`。

费用审计脚本现同时归集旅程和独立 probe，不把镜像导入重复算作新调用。阶段实际累计
**¥1.1697389**，加此前历史为 **¥2.0518255**，无 probe 待对账预约；仍未对供应商发票。

## 已知当前位置在角色输入中的丢失

另一个读取缺口不依赖模型采样：SituationCompiler 已产出 `location_slice`，Snapshot 的
情况字段白名单却没有它。快照 `.21` 现在保留已编译的位置条目及其原 availability、reason
和情况来源；没有从习惯、Plan 地点或聊天回顾推导位置，也没有新增来源权限。

公共模块组合测试从真实 SituationCompiler 的 available / redacted / unavailable 结果
分别经过普通聊天与恢复 compact 入口，再编译 CharacterInterior 快照。六个用例先因
缺少 location_slice 全部失败，修复后保留实际地点或原不可用状态；地点本身不创建活动。
情况/输入子集 **44 项通过**，当前活动、关系与 inbound author 子集另 **122 项通过**，
包含原 46,000 字节请求门禁。全量场景 hash 必须随最终集成另验，不预先更新冻结基线。
这只验证既有可信情况材料的读取，没有真实位置写入或新地点事实验收；trial-04 首问原本
没有位置权威，故不能把这项修复声称为该次正文虚构已经解决。

首问真实请求进一步按结构统计为 **68,473 字节**：system 51,889、user 11,924、工具 2,162
字节（其余为序列化与请求字段）。46 KB 是现有较小 fixture 的回归限额，不是这份真实请求
已经满足的大小，也不是 runtime 的通用硬上限。主要重复集中在能力说明、人设 prose/JSON
和多个输出范本；供应商只返回整次 16,932 个输入 tokens，没有分块 token 账单。

工具描述还存在实际契约漂移：持续情绪错误地指向 `mood`，同段又笼统排除 media，与已安装
即时照片能力冲突。描述现在使用 `affect/components`，只排除超出已安装即时照片能力的
media；没有新增能力、改变 payload parser 或恢复第二审查模型。原工具契约 **61 项通过**。

## 机会可用窗口与本人活动时长分离

`0f7cac67` 合入角色时间选择修复。新的 life-development choice payload `.2`、tool v2
及 authority `.2` 要求 accept 同次明确 opens_at / closes_at；no_op 仍无时间要求。工具
与硬边界材料把外部时段明确作为可用范围，角色自己选择起止，系统只验证范围和先后关系。
Plan 与 canonical choice 必须精确等于原 InnerDecision 的时间，不只检查仍落在可用范围内。

实际 Core→StructuredRole→DeepSeek MockTransport→LifeDevelopment→Plan 的两个回归
先复现了时间缺失/null 被直接接受为完整 60 分钟窗口；修复后同一角色只纠错一次，选择
第 7–19 分钟，最终 Plan 精确为 12 分钟。没有本地默认时长、随机分钟或额外模型 lane。
协调篡改 Plan 和 canonical choice、却仍在可用范围内的负例也被原作者时间证据拒绝。

新决定的 subject 包含当前契约。只有旧 subject 加原 ModelResult、Proposal 和 hash-bound
InnerDecision 才能走冻结 `.1` 恢复，保留历史 null 继承语义；新输出在写入可恢复审计前验证，
不能先发 `.1` 再靠重启降级。SQLite 关闭重开分别验证新 12 分钟和旧 60 分钟恢复，篡改原
sidecar 则失败。历史字节没有重写；真实供应商是否自然选择合理时长还须单列试聊结果。

作者侧 327 个不同定向用例通过并经独立审查；根分支与位置读取组合的 **275 项通过**。
现有 80% 完成资格门槛及机会地点继承尚未改变，它们仍是独立待审问题。冻结 `.97` 的
120 场景业务字段与新审计 hash 需在最后组合完成后再核对，不能直接用新版本覆盖差异。

## 人设重复呈现与最终组合基线

`0713ece4` 只删人设 JSON 中已经在同次 prose 逐字完整呈现的六组重复字段。每组有一个
非空条目未逐字覆盖，就保留整个原字段；不归一空白、不判断语义近似、不修改 persona
对象、事实 scope 或其 source hash。slim/full_turn 范本和历史 parser 均未改。

公共 HTTP 请求测试验证精确内容保留与体积减少，另有各字段空值/首尾空白的保守负例。
作者侧 143 项通过，集成后的 identity/author/tool **181 项通过**。原 trial-04 首问的私有
prepared-only 对照只替换 identity JSON：请求 **68,473→64,152 字节**，system 删除 4,231
字节，外层转义另省 90 字节；原 snapshot、user、prose、source、tools 及 provider 参数均
保持。该字节变化本身不能证明实际费用倍率、人格稳定或事实漏报已改善。

干净 `0713ece4` 完成严格 120 场景导出：根 8 字段、每场景 18 字段、ID 顺序和唯一性均
一致；17 个非 replay 业务字段全部与 `.97` 相同，仅 120 个 replay hash 改变。候选再次
独立计算 SHA256 后安装 `.98`：
`c554344cc79b35a870db771d3ca5d666b985b6b1caf8d7d87b8776411505da26`。
证据在 `output/adaptive-companionship-2026-09-08/baseline-choice-context/`。最终完整回归
与真实新旅程继续单列，固定场景没有替代生产资格。

## `.98` 完整回归、第五轮试聊与仍存在的事实漏洞

干净 `90f23381` 的完整回归为 **6533 passed / 19 skipped**，用时 501.00 秒；唯一
warning 是既有 Starlette/httpx 弃用提示。日志为
`output/adaptive-companionship-2026-09-08/full-choice-context-98.log`。此结果在后面的
thinking auto 传输修改之前，不能当作后续所有提交的完整回归。

人设逐字去重的真实单请求对照也已结束：保留原 Flash、forced tool、温度和输入，只改变
上述重复 JSON，输入为 15,983 tokens、输出 124 tokens、缓存命中为 0，耗时 1.744 秒，
费用 ¥0.0245325。返回仍声称已经在图书馆读书，另添无依据的当天课程安排，原工具参数
显式 `world_claims=[]`。这是负结果：减少重复输入没有解决事实漏报。它没有写入 World
或执行 QQ 交付；原始请求 SHA256 为
`7c47c37d725971a5d3c7989ff788601457fb04e51c0a241154cb7253d714bed3`，证据在
`output/private-audits/trial04-identity-control-_1ehasji/`。

trial-05 使用干净 `90f23381`、独立新 World 和本地 CaptureDelivery，完成 **3 回合、22
分钟逻辑时间**后主动关闭，`operator_stopped / completed=false`。其证据位于
`output/adaptive-companionship-2026-09-08/trial-05/`。

- 外部机会 10:05–22:00 共 715 分钟；角色用新的 choice v2 明确选择 10:05–12:00 共
  115 分钟，seq17 Plan 与 seq20 Started 保留了原选择。这是真实模型使用个人时段的
  正例，没有把机会完整窗口直接作为她的活动时长。
- 首问时确有上述图书馆活动，不应笼统称整段活动虚构；但靠窗位置、已翻到具体蓝皮诗集、
  尚未读过等细节来自习惯、候选结果或接受意图，没有已结算的经历来源。当前地点条目也
  仍不可用。最终纠错回复却将这些细节写成实况，raw `world_claims=[]`。
- World Author 写入“此前没见过这本书”一类角色既往知识，focused novel-origin critic
  仍判 supported。新时间能力与 reader scope 没有消除这一作者越权及漏判。
- 后两轮关于再待一两个小时、用户去泡茶后再聊的表达已得到本地捕获。关闭发生在第三轮
  之后，未继续观察沉默时段、主动联系或跨日变化，不能把措辞自然当成长期真人感通过。

首问原请求与最后纠错必须分别计数。原请求其实尝试写了两条 source claims，但外层
JSON 中的 `payload_json.messages` 缺少闭合 `]`；不能把它描述成原模型完全没有声明。
接收器捕获解析错误后过早释放非法 head，上层开始一次纠错时取消原流。capture
`4c3d5705a6a54c21944487f54c2993a6` 留下 53,267 字节、159 个完整 SSE 帧，所有 usage
均 null，没有结束帧，状态是 `cancelled / body_complete=false`；响应前缀 SHA256 为
`f5e33c18d562e9d19b406ce4f1a6cf470a06cabed681f4b401f52abfe0022652`。主账准确记录
`caller_cancelled / unknown`，不能从后续成功回复补造这笔用量。

该轮 11 次 provider 尝试中，10 笔 known 费用合计 **¥0.0798991**，另有上述未知账单的
**¥0.241365 保守预约**；没有普通 pending 预约。旅程 manifest 的 `model_failures=[]`
只表示最终处理未留下该类失败事件，不抹掉原失败 provider attempt。194 个事件 payload、
11 份请求及 10 份完整响应和 1 份不完整响应前缀的 hash 均核对；不完整响应仍明确不完整。
10 笔 known 在全局调试账本恰有 10 份来源路径、预约、原账单 hash 与字段一致的自动镜像，
unknown 没有导入；没有手工补写历史账本。

费用汇总脚本已区分 known、not_billed 和 unknown，并纳入独立 probe。当前阶段已知
费用 **¥1.2741705**，加先前历史的已知累计为 **¥2.1562571**，另保留未知预约
¥0.241365。campaign 为 unresolved trial-05 继续占用整个 ¥0.60 入场预约，阶段承诺
额度为 ¥1.7942714，仍受原 ¥2 上限约束。这里没有把未知账单当成零，也没有声称已核对
供应商发票或满足每月 ¥100。付费探测暂缓，先修复上述收尾时序。

## Thinking 单工具传输的显式协商

`2b7e2abd` 在计算调用身份前协商单工具选择：显式 thinking 使用版本化
`single-tool-auto-transport.1`，保留原 schema identity；普通 Flash 继续 forced，其
请求身份不变。atomic 与 SSE 都校验唯一预期工具名，名字迟到时先缓冲参数，拒绝错名、
多工具和纯文本。初始、final、表达修正、Recall 及 StructuredRole 经同一协商入口。
没有改变默认 thinking 开关、模型路由、超时或原重选次数；没有混入计费尾帧修复。

集成后相关 **366 个定向测试通过**，独立审查无具体 P1/P2。完整 120 场景导出在
`/tmp/girl-agent-thinking-auto-frozen-20260908.json`；root 独立重新计算 hash，并逐字段
确认它与 `.98` 候选完全一致。部分 final/表达修正测试只验证参数协商，未增加或证明
原本被合同禁止的第二次 HTTP。之前 27.94 秒的真实 thinking probe 也不证明此版本的
真实时延、World 接受或 QQ 交付合格；这些资格仍未完成。

## 旅程报告保留原始用量尝试

旅程最终报告增加一份独立的 `provider-usage.json` 私有证据：在 host 和 provider 资源
shutdown/quiescence 之后，以同一只读 SQLite 事务采集主账模型用量表及预约原行，
不初始化/迁移账本，不重算费用，不合并成最终 World ModelResult。manifest 绑定该文件
hash，并把状态与 billing state 计数送入 review；详细原始错误只留在私有材料中。
缺失/不可读表为 unavailable，真实空表才报告零条；预算拒绝、未知费用、已知账单保持
各自状态。行数不证明 HTTP 发出，也不自动建立与 World 事件的对应关系。
固定 scope 明确仅含 `world_v2_model_usage` 及预约，不包括独立 `usage_events` 外部
调用账单或全局镜像；零模型行不代表零媒体费用，既有总费用 health 仍单独呈现。

公共 runner 回归先复现“最终成功事件不含失败、关闭时才收到用量”丢失报告字段，再验证
同轮 1 failed/unknown 与 1 succeeded/known 都进入原行证据及 hash 绑定摘要。配套
只读、缺表、损坏文件、空账本与预算拒绝用例通过；全部纵向评估相关 **115 项通过**。

同一个新 reader 对原 trial-05 主账另做只读核验，得到 11 行、10 known + 1 unknown，
10 settled + 1 billing_unknown，原数据库 hash 不变；未重写原 trial 的 manifest 或报告。
这份附加证据在 `output/private-audits/trial05-usage-evidence-akv5cit5/`。新的报告能力
不补齐已经丢失的供应商用量尾帧，也不证明费用总额或语义质量已经合格。

## 非法首帧的计费尾流修复

`e5e98399` 修正了上述提前释放时序：首帧解析错误后不再向上层交出非法载体，继续在
原请求既有 deadline/cancellation 下读取响应，结束后才进入既有的一次同角色纠错。
没有加模型车道、提高时限或修改 `llm.py`；合法首帧仍可在用量尾帧之前交付。

公共回归经过真实 CharacterInterior、DeepSeek MockTransport、异步分隔的 SSE 用量
尾帧、主账、capture 与 Action 交付。旧代码在原尾帧尚未放行时已经发第二次 HTTP；
修复后第一条非法文字没有交付，两笔原 token 账单均 known/settled，且第二次角色调用
使用同一快照并带精确失败原因。deadline 和外部取消仍产生 unknown，并在重开账本后
保留预约；没有收到的尾帧不会因测试放行一个已取消的 peer 而被补造。

作者侧相关 387 项通过，独立 Standards/Spec 审查无具体问题。合并报告功能后的
**431 项定向测试通过**。干净 `e5e98399` 的完整 120 场景再次通过；root 逐字段对比
`.98` 候选并重算 SHA256，完全一致，证据为 `baseline-usage-tail.json` 及
`baseline-usage-tail-verification.json`，因此没有提高冻结版本。

此片只处理尚未释放首帧的结构错误；已释放后的尾段问题不在该证据范围内。原请求的
运输可记 succeeded/known，而角色载体依然非法，两个结果不可混为一谈。等待尾帧可能
消耗原剩余时间，没有保证每次都能及时取得用量；原 trial-05 的 unknown 保持原状。
尚未进行新的真实供应商复测，不能用 mock 账单结算代替该项资格。

## 正文与依据同段的私有协议原型

只读分析实际请求确认：18 个含 `messages` 的可解析示例中，16 个省略 `world_claims`；
slim schema 没有将它列为 required，缺失/null 又会补成空数组。但真实反例已经显式发出
`[]`，所以单独补 required 或修改示例不能证明修复。也不能靠来源编号合法，推导正文中
的蓝皮诗集或未读历史受到该来源支持。

另在 `Girl-Agent-typed-text-segments-prototype` 的被忽略私有目录
`output/private-prototypes/typed-text-segments/` 实现一次性纯解码原型。角色自己为每段
文字选择 World 来源、当前用户报告承接或非外部断言；气泡只从片段精确拼接，claim_text
机械取同一段文字，再接已有来源 scope 和 ExpressionDraft materializer。没有额外模型、
关键词分类、默认片段类型或另一份可以漏关联的正文载体；没有接入生产。

作者与 root 分别运行 **35 项原型测试通过**，验证同字 Proposal evidence、Unicode、
错误来源/分类形状及附加正文拒绝。刻意保留三个可通过的语义反例：把事实误标为非外部
断言、用合法来源支持不蕴含的正文、从当前用户报告增加未报告的旧事。结构覆盖因此不能
当作事实真实性闭包。当前 canonical draft 还会丢失 report/nonworld 分类，正式迁移必须
保留作者原载体及版本、实际 alias/Context/source 身份，不能只存拼接结果。

真实 trial-05 首句只作同口径载体比较，固定原私态与外层：空 claims 的 430 字节变为
typed 分类的 606 字节（+176）；双方都显式声明同一事实时为 601→651（+50）。这只是
UTF-8 字节，不是 token、费用、模型正确率或流式兼容测试。原型及 comparison.json
保留为下一次有界协议对照的材料；当前未改变生产表达格式或宣称事实漏洞已关闭。

## 长期生活背景的审查覆盖与历史身份

`e55210bb` 补齐一个独立的输入缺口：World Author 可写入持久生活背景的
`outcomes.N.dynamic_life_direction`，之前整项没有进入现有 focused origin critic。
现在传入完整对象，并允许模型针对 summary 与各类 tag 的原字段路径、原文片段报告
越权或缺乏来源。代码只核对坐标，未增加本地语义分类器或模型调用。合法 unsupported
结论仍结束该次 admission；没有恢复额外 World Author 重写调用。

新 review `.4`、packet `.5`、subject `.4` 标识扩大后的输入。旧 packet `.4` 的真实
审计继续使用 subject `.3` 原公式。公共冷账本反例还发现：只改新 Proposal 的复制审计，
可以伪装旧 subject。因此 reducer 现在将两种已存在的 review 副本，与原 ModelResult
中的角色、Context、请求、响应和 subject 身份逐项绑定；删标、旧标及重算副本都不能
获得历史资格。历史 fixture 来自 `233249ed` 的公共 runtime 和本地 stub，未重新生成
旧事件或给它们升级覆盖承诺。

作者侧 Life/biography/public reader **371 项通过**；root 集成后定向 **124 项通过**。
干净 `e55210bb` 完整运行 **120 个冻结场景**，逐字段比较原 `.98` manifest，并独立
重算 SHA256，完全一致，保持原版本与 hash。证据为 `baseline-dynamic-origin.json`
和 `baseline-dynamic-origin-verification.json`；这批离线场景不证明真实模型能发现
隐含前提。

该修复不能解释或解决此前真实误判：trial03 的旧草稿前提、trial05 的未读诗集前提
已经完整进入原 critic，仍得到 supported。这两个实际反例均未使用新补齐的字段。
机制覆盖、模型语义判断与长期真人感继续分开验收。

## 同一真实输入的两种正文协议：仍未解决事实误述

干净 `b1c0e286` 在私有 debug 通道执行两次请求，材料在
`output/private-audits/typed-control-on9vw127/`。两臂保持 trial04 首问的用户输入、
World 材料、身份、alias、工具、采样和输出上限原样；共同删除 18 个有明确边界的
示例块，实验臂只修改 slim 正文及其必要格式说明。canonical、full_turn 和 Recall
能力保留，若选其他载体则不作为该比较的结果。它不是原生产请求与新路由的完整对照。

| 实际结果 | 对照：独立 claims | 实验：正文片段自选依据 |
| --- | --- | --- |
| 原始正文语法 | 合法 slim | 合法 typed slim |
| 当前活动陈述 | 图书馆、三楼靠窗、今天没课 | 图书馆、刚坐下、翻了几页书 |
| World 声明 | 空数组 | 显式引用 S1–S6 |
| 已知费用 CNY | 0.0192108 | 0.0198438 |
| 输入 / 输出 tokens | 15,653 / 165 | 15,949 / 207 |

输入当地时间为 10:20，图书馆计划是 13:00–15:00 的 `planned_future`，惯常日程明确
标为背景，实际活动、Occurrence、Experience 权限为空。S1–S6 的合法范围只是年龄、
学期等传记坐标，不支持当时已经到馆或翻书。实验因此表明片段载体可以让这一次模型
写出声明，但没有解决声明与正文不相符的问题，不据此迁移生产协议。

独立重建原始 SSE，确认每臂都是一个合法工具调用、一个 usage 与完整结束标记；
原始 arguments、返回值与分析结果一致，没有由本地修补产生上述文字。实验中的 S8
是当前用户问题的 recent_dialogue 投影；只检查直接 trigger refs 的诊断字段为 false，
不代表它来自其他回合。两臂总 wall 约 2.745 / 3.281 秒，包含本地核验；未测 TTFT，
输出长度不同、调用顺序固定，不能将差值归因于协议或当作交付延迟验收。

复查还发现一个确定的输入缺口：完整 manifest 已生成的
`biographical_coordinate_authority` 被 `present_hard_boundary_prompt` 删掉，而 system
仍要求按其中的具体字段引用。实际 S1–S6 只各出现一份 opaque hash 映射，模型没有
alias 对应字段与值的说明。该问题已进入下一片修复；缺少可读来源与模型的语义误判
不能当成同一件已证实的因果关系。

两次新增费用共 **0.0390546 CNY**，两笔均 known/settled，没有新增 unknown。四个
实际私有 runner 的离线失败链测试确认：无 usage、工具名过早失败均保留整个试验
额度；已知账但工具无效仍判样本无效；第二臂预算拒绝不会发送 HTTP 或伪造 known
账单。源码、请求、响应、主账及应用回执留在私有目录。

root 另复核原 trial05 的完整只读三表快照、main/WAL、捕获与关闭证据，仅释放
原 0.60 元试验额度中未使用的 **0.2787359**，保留已知 0.0798991 加未知 0.241365。
这是 admission allocation closure，未把未知请求改成 settled，也未改原账本。两次
对照后本阶段已知 **1.3132251**、保留未知 **0.241365**，合计占用 **1.5545901**，
原 2 元上限不变。跨阶段累计已知费用 **2.1953117**；这些是用量与安装价表证据，
不是供应商发票对账或每月约 100 元目标的验收。

## 将已有的传记来源对应表送到角色面前

`7e63d98c` 只在展示层保留非空的 `biographical_coordinate_authority`，以
`expression-hard-boundaries.present.2` 标识新的输入。表中的 alias、字段、值、时间和
scope 都来自同一份已生成的 provider-visible manifest；没有扩张事实权限、从不可用
材料补来源、把居住背景改成当前地点，或增加模型调用。

新增测试先在真实 HTTP 请求生成链上得到两项缺表失败，再验证修复：有无
InnerLifeSnapshot 时的六个精确坐标、对应 alias、当前 scope 均能到达出站请求；私有
方向、不可用和空材料不会因此成为坐标来源。作者目标 **251 项通过**，root 集成后
**10 项通过**，独立审查无具体问题。

另只用此前实际可见的六个传记值重算 canonical refs，全部与 S1–S6 精确一致。
同一 captured request 的 boundary / user JSON 增加 **864 字节**，整个 HTTP JSON
因嵌套转义增加 **986 字节**。这是 UTF-8 增量，不是 token 或费用估计；私有核算为
`biographical-source-readability.json` 和 `biographical-source-wire-size.json`。

完整 120 场景首先触发了原 `.98` 摘要门。随后以公开 CLI `--limit 120` 导出整个
120 场景候选并逐项比对：所有判据通过，每一行仅 `replay_hash` 改变；输出、Room、
事件类型与顺序、调用数量、终端状态、错误和其他字段均不变。候选摘要为
`e29739712b5d4135b239b45113586318dea54e2697d397e176aacb365e79f4c6`。
据此建立 `.99`，摘要为
`8f3579b38dbc6895330db8f02bbbbbf12eb78604d11d2f3f5424724313459f60`。
完整候选、逐项比较和仅版本变化的导出均保留，未修改场景或放松判据。
随后干净 `f48af2d4` 通过不带 limit 的完整正门；最终全部字段与已比较的 `.99`
候选完全一致，并再次独立重算摘要。证据为 `baseline-biography-readable-final.json`
及 `baseline-biography-readable-final-verification.json`。

之前的真实两臂对照发生在此修复之前。当前证据证明来源说明能到达角色，尚未证明
这份新输入能减少实际误述；下一次真实试聊仍需核对正文、引用与费用。

## 第六轮真实旅程：来源可读仍不足以阻止编造当前生活

干净 `803f15a5` 经原公开 interactive CLI 运行 trial06，仍使用独立 debug provider、
新 SQLite、虚拟时钟及本地 CaptureDelivery。固定额度 **0.40 CNY**、wall 600 秒，
原 phase 2 元上限不变。私有 launcher 仅在既有 transport 外核对准入来源，保留原始
请求；4 项离线检查覆盖公共 CLI/DeepSeek/MockTransport/交付/关账，以及 CAS 拒绝、
unknown 和缺失证据保留整笔额度，root 与作者分别通过，独立审查无阻断问题。

| 当地时间 | 测试者输入 | 实际本地交付 |
| --- | --- | --- |
| 10:20 | 你这会儿在做什么？ | 在图书馆这边 刚坐下没多久 你那边呢 |
| 10:21 | 我刚忙完一阵，想歇会儿。你今天准备在那儿待多久？ | 看情况吧 下午要是没课可能在图书馆待到三四点 你呢 歇够了打算干嘛 |
| 10:22 | 先泡杯茶，过一会儿还得继续干活，可能顾不上回你。你也按自己的安排来就好。 | 好 那我也不打扰你了 忙你自己的吧 回头再聊 |

三次原始 slim 均为 `reply_only`、`world_claims=[]`；首轮 `my_state` 也写“上午在
图书馆”。首问 capture 为 `model-input:26aa919094dd429da73585b645693762`，请求
SHA256 `c572dde887854beb92b9ee62d62c90cf2a81e48f19e8027d25441e5c19748bbe`。
新传记来源表确实出现在该实际请求中，六项仅支持季节、学期、学年、年龄、校历和
宿舍居住背景。Location 为 unavailable，近期经历不可用，没有当前活动。该 cursor
的 seq1–29 唯一 Plan 是角色在 10:05 接受的 **14:00–15:00 教学楼诗歌活动**，
没有 Started、Occurrence、Experience、Location 或 Movement。首问因而仍是无来源
的当前地点与行为陈述，不能再归因于缺少这张表；单个样本也不证明模型具体用了哪段
背景。第二轮“可能待到”是未执行的未来意向，不当作已发生的新世界事实。

测试者随后请求推进到 11:30；实际在 **11:07** 因预算准入拒绝结束。10:22–11:07
这 45 分钟没有额外发言，但不能据此判定角色主动选择了沉默：七条 CharacterInterior
终态为两次 LifeChoice、三次 inbound、一次 lifecycle select、一次私人印象更新；
没有主动联系决策、主动联系调用或角色 no_op。第三问对“接下来忙”的理解由角色
自己写出，但无人回复后的长期行为仍未获得充分试验。

全部 **201 条事件** 序号连续、payload hash 有效。10:50 出现第二个角色接受的
校园小径 Plan，窗口 10:50–13:00，并在同刻 Started；10:52 打开并激活其 occurrence。
结束时没有 Completed、Settlement 或 Experience，因此这些后来的事件不能追认
10:20 的发言。另一个待修问题是该角色意图写去图书馆、翻一本借了很久的小说，
typed Plan/Occurrence 地点却都是校园小径；其意图和旧发言均不能证明到达或借书历史。

这轮还直接暴露了 World Author 审查权限的代码矛盾：候选中写入“心里觉得安静又
踏实”等角色内心，focused critic 返回 supported。当前提示在 premise 中禁止新内心，
却允许 outcome 中的 candidate feelings；这种范围区分仍让 World Author
预写角色主观反应。下一片沿用现有 focused critic 修正权限与拒绝坐标，不增加模型
车道；真实已有前提误判与聊天正文漏报仍须分别验证。

15 次实际请求均有完整响应：12 个 atomic JSON、3 个 SSE；三条 SSE 均含 usage、
finish 与 DONE。主账 **15 known / 15 settled**，输入 162,074、输出 4,812 tokens，
已知费用 **0.170109 CNY**，没有新增 unknown。拒绝的是新的 `life_development_draft`：
拟预留 0.282726，此前实际已知 0.1660485，合计超过 0.40；它没有发 HTTP，也没有
reservation、tokens 或费用。同次 drain 随后完成一笔较小 NPC 请求，runner 才停止。
不能将这个终点描述为已花满额度，或角色因心情而停止生活。

原内存 manifest 经 CLI 同一序列化规则与磁盘全字段核对后，原始 usage/预约与响应
cohort 对齐，才释放未用额度 **0.229891**。独立复核确认五个 artifact 与关账/campaign
绑定；没有 capture→reservation 的直接 FK，不宣称逐请求密码学绑定。phase 已知
**1.4833341**，保留原 unknown **0.241365**，共占用 **1.7246991**，剩余 **0.2753009**；
跨阶段累计已知 **2.3654207**。这些仍非供应商发票对账或每月 100 元目标验收。

证据位于 `output/adaptive-companionship-2026-09-08/trial-06/`；关账位于
`output/private-audits/trial-06-launch-state/`。本轮未部署、发真实 QQ 或修改生产数据库。
移动链仍未安装；只纠正了文档中“Plan 承载当前位置”的过时说明，没有激活 dormant
Location 权限，也没有把住址、惯常日程或计划升级为已到达。

## 阻止候选结果由 World Author 预写角色内心

`68f5e26a` 同步修正了作者主提示、能力表、实际 JSON schema 描述，以及既有 focused
critic 的主提示、维度和格式纠错说明。World Author 仍能提供客观动作候选、环境和
后果；新的感受、想法、动机、意图和主观反应属于角色模型，不能靠选择一个包含这些
内容的 outcome token 代替角色自行创作。精确有来源的历史内心仍可作为背景引用。

既有 `unsupported_outcome_prerequisites` 新增 outcome 专属的
`character_interior_authorship`，只接受该 `outcomes.N.text` 的逐字片段。此前此类
合法拒绝会被 parser 当成格式错误；现在一次有效 unsupported 即停止 admission，
不追加模型调用、不进入角色选择，也不创建 Plan/Occurrence。没有新增 reviewer，
没有本地关键词分类或删除、改写模型正文。

新 review `.5` / packet `.6` / subject `.5` 绑定实际请求；缺 marker 的历史永久保留
packet `.4` / subject `.3`，显式 `.5` 保留 subject `.4`。新的审计副本不能通过删标、
改旧标或重算复制哈希绕过原 ModelResult 绑定。改动前由 `803f15a5` 公共 runtime
生成的 packet5 fixture 保留原 10 事件和 9 sidecar，冷回放不改变字节，也不声称
旧审查获得新权限覆盖。旧 pending 候选和已提交经历没有被静默修正。

作者与 root 分别通过 **164 项**相关检查，两轴独立审查无阻断问题。实际 HTTP
MockTransport 主链证明合法拒绝可被正确消费，其他本地 fixture 覆盖错误片段纠正、
历史回放和新提案防降级。完整 **120 个冻结场景**候选与原 `.99` manifest 全字段
完全一致；干净 `68f5e26a` 随后也通过不带 limit 的完整正门。root 独立重算摘要为
`8f3579b38dbc6895330db8f02bbbbbf12eb78604d11d2f3f5424724313459f60`，保留 `.99`，
没有修改基线或判据。证据为 `baseline-outcome-authority-final-verification.json`。

这些是机械权限与兼容性证据。在该提交完成时，修复后的真实 critic 尚未复测，不能据此声称它能
找全内心越权或旧事实前提；聊天空声明编造当前生活也仍未解决。为后续复测，已从
trial06 原始材料逐字复原首个 critic 的完整 messages；第二个样本缺原完整 dialogue
lane，明确标记缺失，不伪造同输入对照。本片没有新增真实调用或费用。

## 相同事实输入复测：内心与既往事实仍被放过

在干净 `e16f2707` 上，root 使用独立 debug key 执行了一次 sample01 focused critic
请求，最多一笔 HTTP、55 秒、分配 0.13 元，无 fallback、重试或 World 接受。原
trial06 的首个 critic messages 已由旧公开 compiler 逐字复原；新请求的
`reviewed_surface`、`pinned_authority` 及所有非 messages HTTP 参数与原请求完全
一致。变化只在权限说明、review/schema、坐标目录及 packet 版本/hash。第二个原
样本仍缺完整 context，没有被拼成“同输入”试验。

新请求 25,543 UTF-8 字节，较原请求增加 788 字节；实际 wire SHA 为
`039e89106cbf6aa6898798129c1f98cd96d31edb000bb90816b7aa6bc1ebe10e`。
约 4.53 秒后返回 **supported**，七组负向 findings 全空。模型明确将“她之前一直在
修改的一首诗”解释成分支内新动作，并遗漏候选中的新满足感、想法与意图。
这是结构合法但语义仍有漏检的真实反例；没有把 unsupported 通路的离线通过当作
真实拒绝成功，也没有将该结果写成新的 World 经历。后续应检查同一 critic 内的
任务表示和权限豁免冲突，而不是增加通用 reviewer 或按反例关键词删改文字。

原始响应完整、唯一主 usage 为 known、唯一 reservation 为 settled；input 6,599
tokens（hit 256 / miss 6,343）、output 294。按原记录时刻的安装价表计费
**0.0217006 元**，释放分配中未用的 **0.1082994 元**。root 的 7 项临时 fixture
通过后才执行；独立只读复核确认整个单调用 cohort、关账凭证和所有旧 campaign
行不变。仍无 capture→reservation FK，亦非供应商发票对账。

phase 已知 **1.5050347**，原 unknown **0.241365** 继续保留，共占用 **1.7463997**，
在原 2 元上限内剩 **0.2536003**；跨阶段累计已知 **2.3871213**。冻结 runner 为
`output/private-audits/run-outcome-interior-control.py`，结果与关账分别在
`outcome-interior-control-sample01/` 和 `outcome-interior-control-launch-state/`。

## NPC 当地时间与沉默观察的边界

`2c3c6b87` 为 NPC 请求补入只读 `npc-civil-time.1`：以当前已提交 Clock 与唯一已
提交 biography timezone 转换当地 ISO 时间，保留原 UTC、地点和既有内心文字。
精确来源只授权两个时间字段，完整传记不会进入 NPC 输入。缺源、读错、hash 不符
或可变 catalog 时区冲突时明确 unavailable，不按地点推断，也不覆盖此前“深夜”的
错误状态。没有增加角色或 World Author 调用。

root 独立通过 **59 项** NPC 相关检查，涵盖公共 MockTransport 的上午/跨日期、
后续状态原文、缺源、引用预算、回放及既有 NPC 能力。120 个冻结场景候选 manifest
与修复前 `.99` 全字段相同，随后不带 limit 的完整正门也通过；root 独立重算摘要
仍为 `8f3579b38dbc6895330db8f02bbbbbf12eb78604d11d2f3f5424724313459f60`，
证据为 `baseline-npc-civil-final-verification.json`。没有更改基线；输入修复不等于
真实模型的时间判断已经通过验收。

trial06 的 45 分钟静默还没有覆盖主动联系决策。seq178 在 10:52 记录的
`social-initiative-context.2` draw 候选为 6/7/8 小时，选中 8 小时；最后用户消息在
10:22，测试在 11:07 已停止。现有机会时序本身尚未到期，故这段日志只能证明没有
额外交付，不能证明角色因尊重、失落或自行决定而沉默。后续长时测试需推进到真实
已声明机会并检查角色 terminal choice，不能缩短生产节奏来制造“通过”。

## 两次有界对照：逐项审查部分命中，NPC 时间仍自相矛盾

在干净 `5a8be681` 上，root 各执行一次 prose protocol 与 NPC civil-time 对照。
新 shim `run-prepared-life-controls.py` 复用冻结单调用 engine 和既有 CAS/费用闭环，
只在隔离模块中替换 capture role、原 pinned 时间和 probe kind 三个标签；原文件不变。
prose 分配 0.13 元，NPC 分配 0.07 元，各最多一次 HTTP、55 秒、无 fallback 或重试。
root 独立通过 11 项新 binding/预算 fixture 后才执行，两次实际出站字节均与准备稿一致。

prose 是可丢弃的私有合同，不是生产 schema。它保留原 sample01 的全部事实和非 messages
参数，澄清“不评价情绪好坏”和“检查谁有权写入”的区别，并要求在同一 critic 响应中
逐项检查 premise 和每个 outcome 的内心权限与既往前提。代码只核字段覆盖、逐字片段、
来源指针、同分支先行片段及结论一致性，不切关键词或自动改写文字。27 项纯函数测试
包含一个“坐标全部合法但语义仍可误判”的负面边界，不能作为真实效果证明。

实际请求 28,757 字节、input 7,250 / output 604 tokens，约 4.648 秒返回。
模型这次将两个 outcome 的内心标为 unsupported，识别出新满足感、决定和思想活动；
原 review 内层 parser 独立验证两个 finding 的路径、kind 和逐字片段均合法。但六个轴
全部漏 `supports`，四个 clear 轴还返回空 `fragments`；冻结私有 parser 原样拒绝，
报 `incorrect object keys`。两个 prerequisites 仍为 clear，旧诗稿依旧没有来源或
同分支创建依据。因此结果是**内心权限部分命中、整体协议无效、既往事实漏检未解决**。
没有补字段或放宽规则来把原响应变成成功，也没有把该协议安装到生产路径。

NPC 对照先用旧 compiler 逐字复原原 6,475 字节请求，再由当前 compiler 增加时间视图。
其余 profile、`my_last_state=null`、图书馆位置、public_world、stimulus 和 HTTP 参数
保持一致；新增请求为 7,737 字节。复原只覆盖模型可见输入，未声称恢复完整 runtime
snapshot 或 catalog。当地时间来自原 193 事件/88 commit 的精确前缀，不伪造 commit。

实际 NPC input 2,098 / output 288 tokens，约 3.285 秒返回 no_op。其 inner state 明确
写出 `11:07 AM local time`，但 impulse 同时写 `quiet afternoon`。当前
`NpcActorDecision` parser 和 `_validate_actor_decision` 在复原的有效视图上均通过；
语义仍存在上午/下午矛盾，不能宣布时间行为修复成功。no_op 本身是合法自主选择。
这次只测模型，不将其结果写入 NPC 状态或 World。

prose 已知费用 **0.027186 元**，NPC **0.008886 元**，合计 **0.036072 元**；原始
响应、唯一主 usage/reservation 和关账凭证一致，均 known/settled，新增 unknown 为 0。
两次释放未用分配共 **0.163928 元**，旧 campaign 行与原 unknown **0.241365** 不变。
phase 已知 **1.5411067**，共占用 **1.7824717**，在原 2 元上限内剩 **0.2175283**；
跨阶段累计已知 **2.4231933**。这仍是按安装价表核算的实际 token 用量，未做发票对账。

原始结果分别在 `output/private-audits/prepared-prose-control-sample01/` 与
`prepared-npc-control-sample01/`；独立审计为 `prepared-life-controls-assessment.md`。
这两个反例继续保留为未通过，不用离线测试、body 完整或费用结算代替语义验收。

## 最后一次对照：推理用尽输出上限，没有审查结论

在干净 `c4737053` 上，root 执行冻结的 `run-prepared-thinking-control.py`。
它使用原 sample01 新合同的完全相同 messages、事实、schema、模型与 4096 输出上限，
调用公共 SDK 的 thinking profile：thinking 从 disabled 变为 enabled，默认增加
`reasoning_effort=high`，并省略 temperature。由于 stock SDK 同时调整三个 HTTP 字段，
这不是仅单字段改变的因果实验；没有用自定义 payload 绕过 SDK。

实际请求 **25,550 字节**，SHA256
`e0bbe2adbfee7686c685b911154872f2b2e627d4df84439b81152e258f97f6a7`；
与冻结稿逐字一致，分配 0.13 元、保守预留 0.116586 元，最多一次 HTTP、55 秒。
root 独立通过 4 项离线 fixture 后执行；测试涵盖真实 MockTransport 出站字节、
reasoning usage、未知用量全额保留与发送前来源/CAS 漂移拒绝，没有第二次调用。

约 **38.765 秒**后收到完整 HTTP 200 响应，`finish_reason=length`，最终 content 是
空字符串。原始 usage 为 input **6,678**、output **4,096**、reasoning **4,096**，
无 cache hit。SDK 因没有非空正文报告 schema error；不是请求超时或角色选择沉默。
没有 review JSON，因而无法判定它是否识别了内心越权或既往事实；不从 reasoning
文本补造 verdict，也没有提高上限重跑或把该 profile 安装到生产。

该观察与供应商的 [Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)
所述输出上限与 `length` 终止相符；这里的 token 分配和空正文结论直接来自本次原始
响应。当前限制下，这个样本未提供可用审查结果，不证明任何推理配置都不可行。

raw body **21,257 字节**、EOF 完整，SHA256
`9545b551693c1240043db7b8f19f69acd3a0b5b4b039aaf3ff0061abc7ec0c2d`。
唯一 primary usage 是 known、reservation 是 settled；按原记账时刻重算为
**0.056898 元**。但冻结 probe closure 要求方法成功返回正文，实际 ValueError 使
`billing_closed=false`。因此 campaign 仍保留整笔 **0.13 元**，不改写旧关账条件或
释放余额；这不是又产生了 0.13 元账单，也不是 provider billing unknown。

原 `assess-trials.py` 只把 campaign 已关账 probes 计入其 `phase_cny=1.5411067`，
这次列入 `pending_probe_hold_cny=0.13`。补充实际已知 primary 0.056898 后，本阶段
可取得的已知费用是 **1.5980047 元**，跨阶段 **2.4800913 元**。原 unknown
**0.241365 元**保持不变；campaign 总占用 **1.9124717 元**，在原 2 元上限内剩
**0.0875283 元**。已知费用与 allocation 占用是不同口径，不能相加重复计费。

原始结果位于 `output/private-audits/prepared-thinking-control-sample01/`，关账凭证
位于 `prepared-thinking-control-launch-state/`。campaign SHA256 为
`d2b23e14bff154071690231f30be703c7faa771066293555b3c0a6cb92af537c`。
本批至此停止付费探索；所有失败样本保留。没有部署、真实 QQ 或生产 World 写入。

## 独立 low 对照：单字段改变仍没有最终正文

在干净 `486f84f4` 上另开一次独立批次，上限 **0.13 元**；执行前已明确告知该上限，
没有修改旧 2 元 campaign。开批前累计已知为 2.4800913 元，保守占用为 2.7945583
元，新增后保守天花板为 2.9245583 元。原 high 的整笔 0.13 元继续包含在开批基数，
不把其中已知 0.056898 元再次叠加。

普通 SDK 生成的请求与原 high 冻结请求仅 `reasoning_effort: high → low` 不同；
messages、模型、4096 上限、thinking、JSON 格式以及 temperature/tools/stream 的
缺省状态均相同。实际捕获 25,549 字节与准备稿逐字一致，SHA256 为
`c3c7ad0996dad19c547dd3206cb4d4a61cec1a1901bcc59e0664ffc3f9012c1b`。
没有重编事实输入；捕获的 virtual time 来自其原 pinned Clock 10:05 +08:00。

新的独立执行器只使用普通 WorldV2UsageStore 的实际请求准入，保守预留 0.116583 元，
最多一次 HTTP，调用与关闭共享 55 秒，stock client 45 秒，无 fallback、纠错或重试。
全局 debug 镜像在构造模型前禁用，仅写本次私有主账。固定执行 manifest 为
`31ac970b911266f8a826a97ec9b3467242abd4ed12300f92f15383c7217550fd`；root 与
独立审者各自在冻结稿上通过 9 项临时 MockTransport/SQLite 检查，随后仅 root 执行。
测试中的
预算拒绝保留原 `budget_denied/legacy` 零 token 记录，不伪造供应商计费结果。

约 **34.269 秒**后收到完整响应，`finish_reason=length`，content 为空，output
4096 token 全部为 reasoning；SDK 返回 ValueError，没有交给 parser 的普通 review。
input 为 6599 token，其中 cache hit 6528、miss 71。原始 body 共 20,752 字节、EOF
完整，SHA256 为 `9e986d19a295592e2f184692a1c5ca384011619fe94c18e324bdcf5df8d62066`。
没有阅读推理正文来补造结论，也没有提高上限或追加样本。

因此预先列出的“有效最终审查、既往诗稿前提、角色内心作者权限”均未获得本次验证。
不能根据一次失败断言所有 low 请求都不可用；也不能把这次较低费用或时间解释成
推理档位的普遍优势：旧 high 与本次的 cache 命中及峰谷计价不同。这不是改后聊天
或真实 QQ 验收，也没有安装此配置到生产。

唯一主 usage 的 token/cache/reasoning 与原始 usage 一致，唯一 reservation 已
settled。按该记录时刻安装的 offpeak 价表重算 **0.0188649 元**；主账四舍五入展示
0.0189 元。新规则在执行前即将计费与正文成功分开，允许完整证据的 known 技术失败
只关闭本次账；因此本次占用降为 0.0188649 元，释放新分配余额 0.1111351 元。
这不改变旧 high 的不同关账条件或其保留金额，亦非供应商发票核验。

执行后的独立只读审计复核了实际 raw、实时 SQLite 与 primary 快照、请求及旧
campaign 字节；费用与关闭状态得到一致结论。该审计没有重跑请求或改写证据。

累计已知 **2.4989562 元**，保守占用 **2.8134232 元**，仍低于本次 2.9245583 元
天花板；新增 unknown 为零，历史差额 0.314467 元保持原口径。旧 campaign SHA 仍为
`d2b23e14bff154071690231f30be703c7faa771066293555b3c0a6cb92af537c`。
本次结果在 `output/private-audits/outcome-critic-low-control-execution/run/`，预先判据
在 `output/private-audits/outcome-critic-low-semantic-rubric.md`。该独立批次不再追加调用。

## World Life Intent 第一片：角色自己安排后续活动

在隔离分支完成 ADR-0019 的执行前置，整体世界后果/角色经历迁移仍在进行。后端
`035a2b68`、前端 `fca6f29a`、逆序冷恢复 `5560dba9`、生产请求与活动读取测试
`5cfa0742`；小屋和角色移动均不在这片范围内。

`world_stimulus_appraisal` 同一个角色回合现在可选独立 `world_life_intent`。
新能力只提供她参与过的精确 WorldOccurrenceSettled；角色自行填写意图、时间和
重要程度，也可不填。没有 Appraisal 仍能提出计划，没有意图或供应商失败则不生成
计划。新 Proposal 显式使用 `.4`，绑定原 ModelResult、Proposal、角色、源事件及
原选择时刻；旧聊天来源仍使用自己的合同，旧无意图事件不加入空字段。

计划进入已有 ActivityLifecycle，开始/完成由角色另行选择。没有地点、他人参与、
发送或既成结果权限。源内容为 withhold 时，计划保留该隐私下限，当前/已结束活动
读取不会将其降级给聊天资料。新 reader 共用对原 Plan 与最新生命周期来源的核对。

先接受 Plan，再消费同次决定的其他部分；独立情绪消费者仍可能先终结原触发器，故
terminal recovery 也查找未消费的 life intent。冷恢复从原 audit 补齐，稳定身份为
world + actor + source settlement，不重算计划时间、不再次询问模型。

验证范围如下，组间有重叠，不能把次数相加当成不同用例：

- 后端源树先通过 200 项兼容组，再通过最终 27 项新权限/恢复测试与 17 项当前和
  已结束活动读取测试，共 44 项。覆盖错误 actor/source/hash、缺少角色审计、反向
  冒用聊天来源、CAS、withhold、实际生命周期与 SQLite 冷回放。
- root 集成首条生产请求时通过 230 项结构化角色、world stimulus、schema 兼容、
  旧聊天意图重试和 context resolver 测试。
- 最终 root 集成通过 145 项，其中包括全部 9 项经 MockTransport 的生产请求、
  1 项情绪先终结触发器的冷恢复、27 项新后端测试及相关读取/语法/既有机制。
  新公共测试通过真正安装的 LifeEcologyComposition，公共 tick/advance 推进开始与
  完成，两次后续聊天请求分别读到 Started 与 Completed 来源。未注入替代 worker
  或 reader，也未手写本链的接受事件。旧 fixture seed 仅用于独立故障恢复测试。
- Ruff 与 diff-check 通过；只读审查未发现新的 P1/P2，审查本身不计为实测。

### 固定场景 `.99 → .100` 的原因与证据

第一次正常正门拒绝旧总 hash。另开临时目录，以诊断模式导出完整 120 场景并逐字段
比较：仅 `npc_world_impact.01.replay_hash` 改变，所有行为断言、消息输出、调用次数、
事件类型序列和回放通过状态都相同。该场景 81 条事件中前 18 条逐字一致，首个变化
为 world stimulus 的 ModelResultRecorded，来自新增能力与合同文案的请求身份。

独立单场景因果对照只在该实验进程中关闭新 capability，并从 `035a2b68` 取回旧
`_contract_view` 文案：其 JSON manifest 每个字段重现旧值，replay hash 恢复为
`e97296ecae4def02ba9c11340a5268f1b7b997182b2524edeeeaca42539a1bd3`。
仅关闭能力仍不足以恢复旧请求；第一次两项对照已恢复 replay hash，但比较器未将
tuple 归一为 JSON list，曾误报整体不同。最后按 JSON 规范复核通过。中间诊断产物
保留，最终依据为 `world-life-intent-baseline-causal-control-final.json`；该对照不是
发布正门，不改变正常路径。

因此建立 `.100`，没有放宽任何场景断言。随后正常代码、不带 limit 的 120 场景正门
通过；所有 run 字段与独立诊断完全一致。新 manifest：
`b405ce3beb2d6f4ab83b21011341fbe26192bbd24cb2dab3fb94979468c565c9`。
`baseline-world-life-intent-100.json` 为 233,258 字节，文件 SHA256：
`080d3b84cf37a6c07cf54732cea10aa7d2d9ab721d4872a73af9a94a03e92e95`。
比较报告 `world-life-intent-baseline-comparison.json` 与以上文件均位于
`output/adaptive-companionship-2026-09-08/`；旧 `.99` 产物未改。

这仍是固定模型/MockTransport 的机械闭环，未取得新版本真实角色选择或长期真人感
验收。World Author 正文可替角色编写行动的反例仍待下一片：环境结算、角色回应、
经历与 Recall/聊天入口必须一同迁移，不能因 Plan 能运行就声称权限漏洞已关闭。

该片新增付费调用为零；累计已知 **2.4989562 元**、保守占用 **2.8134232 元**保持
不变，所有旧批次仍封存。意图复用已有感知回合，开始/结束仍使用原角色生命周期
调用，新 schema/正文也会增加 token，尚不能据此保证每月 100 元。未修改生产数据库
或配置、未发真实 QQ、未部署或合并主分支。


## World Consequence 合同与同回合角色回应（完整迁移尚未完成）

本段基于隔离 `codex/living-continuity` 至 `12337d1b` 的集成结果。用户再次明确小屋
暂时弃用，角色移动与小屋交互排除在本轮之外。没有切换生产 manifest、没有部署或
改写生产数据。该 Goal 仍在进行，以下进度不能作为 WA 越权问题已经消失的结论。

### 已完成的两个端点

- `world-consequence.2` 明确区分环境结果与已获授权尝试的客观结果。Clock、Plan、
  outcome token、以后才出现的 Started 或未确定 receipt 均不能赋予新增行动权限。
  `97d1aa1c` / `cf259ba3` 对原 pin、事件及作者请求进行验证；自然语言是否夹带新动作
  仍需 focused review，类型名不是语义证明。
- `ae490b41` / `e9f24710` 只从原 manifest anchors 选择至多四条可读执行材料，保留
  原角色意图全文与 ModelResult / Proposal 来源。withhold、其他 actor、缺失或错误
  sidecar、缺少可读 Action 正文的 receipt 均不提供执行权限。公共 manifest 编译组合
  证实 Started 经 current_situation 进入 anchors；没有从全集历史补授权限。
- `18a560b0` / `ca548005` 在新作者调用前写入有界、不可变的 messages 原文，metadata
  与最终 Proposal 均精确绑定每次请求；恢复重验 kind/hash/UTF8 长度和原 ModelResult。
  这是模型 messages 原文，**不是完整 HTTP 参数或响应流的抓包证明**。旧 manifest
  不新增字段或 sidecar，不在恢复时猜新合同。
- `3953c22c` / `4ea339ed` 将新 prompt 接入显式 `.2` 的作者调用，移除旧的角色行动
  候选权限。未提供的 execution binding 给同一作者一次精确错误反馈，不由代码代改
  正文。新的作者证据读取器反证原 request / response / ModelResult / Proposal / pin
  与原可读意图；公共调用在作者审计已提交、后续审核前中断后，以及 SQLite 重开后，
  都能读回同一份材料。
- `6de0ddbe` 的新 review 编译与解析分别覆盖环境正文、客观尝试正文及其完整执行材料。
  保留既有 focused 车道；general 仍只负责原 typed-location 边界。新 packet/输出格式
  与旧请求分开。此编译器及作者证据读取器**尚未一同接入完整 live 接受流程**。
- `79ff8cc8` / `eec4ee2c` 允许公共环境 writer 显式提交 canonical `.2` 环境正文，
  标记、正文与 result hash 必须一致；其没有原作者执行验证器，故拒绝任何 attempt。
  请求在任何 sidecar 写入前重验，不能用 model_copy 绕过。实际新公共链还发现原结果
  裸 digest 未转为 TypedObjectBinding 的 sha256 前缀，`d0c7923d` 修复该格式边界，
  接受仍比较同一原始 digest。
- `74d1ce7d` / `f7d57d7e` 在已有 world-stimulus 同次角色调用内生成逐源 life_responses。
  新源必须明确正文或 null；漏字段、重复、错来源交回同一角色纠正一次，仍失败则
  技术失败。`.5` 明确绑定新源，旧 `.4` / 无能力请求不增加字段。回应先于可选 Plan /
  Appraisal 接受；两件新事混合一件旧事、部分 CAS 失败、情绪消费者先终结触发器后，
  都可从原审计补齐，不再次询问角色。直接 Plan 消费者也不能借 `.4` 或缺响应的 `.5`
  提案绕过原决定的完整性要求。回应正文自身仍没有外部事实权限。

### 验证与兼容边界

root 在以上集成树通过 **466 项不同定向用例**（31.27 秒），包含新的执行来源、作者
输入、审核格式、环境提交、角色回应、旧意图和角色协议、相关生产装配及读写测试。
各分支此前的 59 / 85 / 137 / 270 项测试与该组有重叠，不作加总。其后 `12337d1b` 单独集成并通过 10 项全新公共请求审计
测试（1.57 秒）：调用前写入、原审计后中断恢复零调用、临时数据库损坏/缺失/错 lane
拒绝、首次存储失败零供应商调用、provider Timeout 留存请求、旧字段缺省、最终提案
篡改反证及 256000/256001 UTF8 边界。合计 476 项不同用例，生产代码与 466 项时相同。
新增作者审计的 7
项测试明确停在后续接受之前，不能把这段公共前缀验证称为完整 World→Experience 链。
首次未授权 binding 的反例还发现错误坐标缺少 type 导致本地 KeyError，已补入精确
校验坐标，确认同作者第二次请求收到原错误并返回合法完整结果。

新后果兼容 DTO 曾使旧 prompt schema 获得新字段。修复采用显式输出 wire，并独立
从 `4fed62c8` 对比旧 schema 的未排序 JSON：完全相同，12,166 字节，SHA256 为
`58d8f7bf4cb66499e0cced702cc86e685ce8c01b98870edbdd0f1b4aedfba0f8`。
没有通过替换 baseline 隐藏这种兼容性变化。随后正常 CLI **不带 --limit** 的完整
120 场景通过，`.100` manifest 仍为
`b405ce3beb2d6f4ab83b21011341fbe26192bbd24cb2dab3fb94979468c565c9`。
新产物 `output/adaptive-companionship-2026-09-08/baseline-world-consequence-protocol-100.json`
与上一阶段 `.100` 输出逐字一致：233,258 字节，SHA256
`080d3b84cf37a6c07cf54732cea10aa7d2d9ab721d4872a73af9a94a03e92e95`。

固定 `3953c22c` 的原请求/公共环境入口/输出 wire，以及固定 `aef66aa0`（root
`f7d57d7e`）的角色回应接线均完成独立只读审查（后一片包含独立 Standards 与 Spec 两轴），未确认 P1/P2；审查不代替实际测试，
也未覆盖后来 `4ea339ed` 的所有新接线。Ruff / diff-check 已逐片通过。

### 下一段必须完成的链条

生产仍使用旧 manifest 缺省。余下工作是：把原作者证据读取器接入新格式的审核与
单次纠正，保存新 packet/subject/possibility 版本；让 World Author 新候选的结构化
内容通过 Aftermath 和通用 Outcome 两条接受路径反证，再创建同时绑定世界结算与
角色回应的单个复合来源 Experience `.2`。LifeContent、WorldLife、snapshot、Recall
和后置 retention 必须一起迁移，旧 pending 不能默认升级。旧 source-rewrite helper
仍使用历史 schema，需要显式新合同，不能直接开启。

冰雹/手账反例的旧完整链仍是已知缺口，不能因两端的机制测试通过就称为修复。完成
上述接线后再复现、修复并冷恢复此反例，随后另列有界预算做真实角色实验。本阶段
新增付费调用 **0 元**；累计已知 **2.4989562 元**、保守占用 **2.8134232 元**保持不变，
旧批次仍封存。同回合回应减少独立调用需要，但 schema、正文、后续生活决策仍会
消耗 token；每月约 100 元与长期真人感均未取得新验收证据。

## 世界后果与角色经历：作者、接受与读取的集成闭环

本片在 `0832aa46` 之后继续隔离实现，集成检查固定于 `dc00ddd7`。未修改 main、
生产配置或数据库，未部署、发送真实 QQ 或调用付费供应商；小屋和角色移动继续排除。
生产 Capability Manifest 的默认合同尚未切换，以下闭环需要显式新 `.2` manifest。

### 已接通的真实代码路径

World Author 的新 `.8` possibility 从原请求与持久审计反证逐项有序的 canonical
consequence hashes，两个新 review packet 都使用同一份原始作者证据。缺少 focused
语义 critic 时明确技术失败。拒稿只给同一作者一次包含原请求、拒稿及精确失败坐标的
纠错机会，再完整重审；代码不删改自然语言或代写 no_op。原作者/审核决定提交后的
CAS 失败及 SQLite 冷恢复复用原记录，不增加模型调用。相关公共测试还覆盖篡改正文、
缺 critic、旧审计 hash 口径与恢复前后内容一致。

Aftermath 和通用 Outcome 接受后都发布精确选中的 `.2` 世界正文及来源描述符。
世界先结算；角色回应尚未接受时没有新 Experience。已有 world-stimulus 调用中的
逐源 `life_responses` 保留角色原文或明确 null，随后由确定性组合器创建 Experience
`.2`：单个复合来源绑定原 settlement、角色回应及各自事件/修订/hash。摘要保留世界
来源定位和角色原文，不能用 WA 叙述替角色补写动作或日记。回应、经历、可选 Plan /
Appraisal 的部分提交，以及另一消费者先完成触发器，均能恢复而不重新询问角色。

独立复核确认并修复两个通用 Outcome 中断问题：接受接口曾返回后续正文发布的
CommitResult，且接受后失败的正文发布会被触发器恢复逻辑跳过。现在接口返回原接受
批次，恢复先补正文再终结原触发器。另一个公共应用反例验证普通 OSError 被隔离后，
同一调度周期原本仍会调用角色；现必须先验证实际已发布正文。失败时记录
`world_consequence_published_source_unavailable`，角色调用、回应、经历均为零。
冷恢复补正文为零模型调用，待原角色租约到期后只消费一次原来源。对应四项公共
回归由独立分支提供，包含崩溃恢复、原接受结果与继续同周期运行的路径。

LifeContent、WorldLife、snapshot、Recall、Outcome 及 Memory retrieval 读取时先
校验完整材料，再按既有预算截断各字段。环境/客观执行结果与私人回应分别带作者和
认识权限；新 Experience wrapper 不获得 `past_world/shared_history` 权限。私人
回应只进入 reflective 资料；null 不生成虚构反思文本。NPC 只取得世界部分及其
settlement/descriptor 来源，私人反思在原 1200 字预算内读取两个作者。所有这些新
路径都拒绝缺正文、错 hash、错误角色及超出隐私上限的材料，没有 raw JSON 回退。

通用 Outcome 的新候选输入采用结构化分栏，避免裁坏载体 JSON。后置记忆保留复用
原角色调用，改为世界后果/私人回应两组输入，完整来源验证后限制到 3000 字并标记
截断。memory kernel 接纳合法的 Experience `.2` 时，继续核对原 accepted event、
历史 transition 与当前值；不让版本白名单把合法新来源错误拒绝。公共保留测试覆盖
文字/null、明确不保留决定与冷恢复零调用；其角色端口是替身，不代表真实模型的
记忆取舍。新选中结果隐私不得低于 occurrence/candidate/descriptor 的最严者，
withhold 来源不进入新角色回应或经历。上层还修正了读取失败被误标为“可用但为空”
的问题：公共上下文现在明确显示 unavailable，原有效来源仍正常可读。

### 集成检查与剩余资格

在 `dc00ddd7` 上一次 pytest 调用通过 **49 文件、790 项不同用例，59.84 秒**。
涵盖作者/审核/接受、两类 Outcome、角色回应与意图、经历与记忆、主要和次级读取、
隐私、CAS/冷恢复及旧合同兼容。文件清单与完整日志分别保存在本机
`/tmp/girl-agent-consequence-experience-gate-dc00ddd7-files.json` 和同前缀 `.log`；
各分支的 82/115/140 等组与本组重叠，不累加为额外验收。修改过的全部 Python 文件
通过 Ruff，diff-check 通过。

固定 `76830ed4` 的 `.2` reader/retention 边界完成独立 Spec/Standards 只读复核，
未确认 P1/P2；该次复核排除随后根侧 `dc00ddd7` 的 resolver/production 窄接线、
默认协议切换和旧 `.1`。缺正文的 WorldLife 保留真实结算元数据且 content=None，
没有把它当成可读叙事；此行为与经历域的 unavailable 状态不是同一项契约。

正常 CLI **不带 --limit** 的完整 120 场景通过，manifest 仍为
`b405ce3beb2d6f4ab83b21011341fbe26192bbd24cb2dab3fb94979468c565c9`。
新 `output/adaptive-companionship-2026-09-08/baseline-world-consequence-experience-100.json`
与原 `baseline-world-consequence-protocol-100.json` 逐字一致：233,258 字节，SHA256
`080d3b84cf37a6c07cf54732cea10aa7d2d9ab721d4872a73af9a94a03e92e95`。没有更新基线。

这些证据不表示所有生产请求已使用新协议。尚需用实际生产 manifest compiler 完成
新请求切换与旧 pending 恢复资格，验证无 Plan 的突发事件、实际已授权活动的客观
成败及长远生活选择三种新链。旧冰雹/手账 fixture 原样保留且旧路径仍能暴露越权；
不能借新路径的 supported/rejected 替身 critic 宣称真实语义漏检已修好。Aftermath
长远后果输入和动态 Plan 转换也须在新默认启用前检查，不能给旧候选补 marker 升权。
随后须另列有界预算进行真实审核与角色对话，检查可见表达、生活后果、回忆及费用。

本片新增付费调用 **0 元**。累计已知 **2.4989562 元**、保守占用 **2.8134232 元**，
封存的旧批次和历史 unknown 不变。复用回应/保留调用不代表 token 没有增长；每月
约 100 元及长期真人感仍未验收，Goal 继续保持 active。

## 新请求默认合同、活动与长期方向的资格

本段接续 `18462f4e`，最终代码固定于 **`66e373c9`**。实际生产 compiler 在隔离代码中
改为 `life-development-capability.production.3` / `world-consequence.2`。只改变新的
请求；旧 `.1` / production.2 已审计 pending 用原 manifest、原 hash、原 descriptor
恢复。没有修改 main、生产进程、配置或数据库，没有发送 QQ；小屋及角色移动仍排除。

### 新增反例与修复

- 新未来 Plan 不得把另一项已开始活动的执行结果当成自己的结果。明确失败坐标为
  `future_plan_execution_result`，仍交同一作者一次精确重选；实际已开始尝试的客观结果
  继续通过有原始执行绑定的 world_contingency 提供。这是事实权限约束，不决定她做什么。
- 动态 Plan 的 Aftermath 转换保留 `.2` 描述符；`.8` Plan reader 校验原稿完整来源与
  新候选正文。新长期后果先通过 typed reader 核对完整材料及隐私，再交角色选择。
  原角色 Proposal 中的 `character_life_direction` 现在传入实际 settlement；之前遗漏
  该字段会造成 accepted change hash 不一致，合法方向决定无法落地。
- 已审计作者/审核会推进 deliberation cursor，原恢复拿当前 Context 与旧 bytes 比较，
  导致真实 resolver 拒绝正常恢复。新增仅供审计恢复的只读 prefix facade，嵌套读取亦
  固定原 cursor；完整 capsule/snapshot/cursor/model-content hash 一致后才复用原审计。
  当前头 Context 的普通要求不变，恢复不产生新的可信决定句柄。材料漂移、缺失或存储
  失败明确失败且零 HTTP，不能用新材料冒充旧请求。原固定测试 compiler 同步尊重 query
  的 cursor，保留旧断言，避免忽略 query 掩盖这一实际问题。

无预先 Plan 的突发事件走到文字/null 角色回应、Experience `.2`、后续读取。独立实际
角色请求先生成并开始一次尝试，冷重启后的真实 production compiler 给 World Author
提供原 ActivityStarted 及完整授权意图，再接受并结算客观后果。另有完整新活动链：
默认 compiler → 作者候选 → 角色明确选择时长 → Plan → start → Aftermath → complete
→ 角色后果选择 → 文字回应 → Experience `.2`，重启无重复调用。长期方向选择验证
Proposal、settlement、durable coordinate 和后续 null 回应经历；withhold 候选在任何
角色调用前失败。相关测试使用 MockTransport/替身作者和 critic，不是实际语义检出证明。

旧冰雹/手账 fixture 保留，SHA256 为
`0e9783f3efc3b5640b4638a760fd15830d36d2515a49966be8b6eb918c142b8a`。
它仍能揭示旧 `.1` 允许的叙事越权，未删除或标记 xfail。新默认的版本化替代覆盖作者
权限及角色自由回应，不能据此宣称真实 critic 能识别任意自然语言中的越权动作。

### 固定提交的验证与下一项实测

`66e373c9` 一次 pytest 调用通过 **55 文件、846 项不同用例，68.55 秒**；文件清单
`/tmp/girl-agent-consequence-default-gate-66e373c9.files.json`，完整日志同前缀 `.log`。
各片 205/42/23 等组与此重叠，不累加。修改的 Python 文件通过 Ruff，diff-check 通过。
独立只读复核覆盖 `c8e094b1` 的候选隐私、未来 Plan 权限与角色方向传递，以及
`0c0ded96` 的原 Context 恢复 facade/嵌套 reader/完整身份比较，均未确认 P1/P2；该
复核不是生产恢复或真实供应商语义正确性的保证。

正常 CLI **不带 --limit** 的完整 120 场景通过；manifest 仍为
`b405ce3beb2d6f4ab83b21011341fbe26192bbd24cb2dab3fb94979468c565c9`。
新 `output/adaptive-companionship-2026-09-08/baseline-world-consequence-default-100.json`
与前片 experience 产物逐字相同，233,258 字节，SHA256
`080d3b84cf37a6c07cf54732cea10aa7d2d9ab721d4872a73af9a94a03e92e95`，未更新基线。

至此本片新增付费调用 **0 元**，累计已知 **2.4989562 元**、保守占用 **2.8134232 元**
不变。准备独立新批次：上限 **1.20 元**，每个 fresh trial 完整预约 **0.60 元**，继承
并 hash 绑定旧封存证据，不追加旧批次、不释放历史 unknown。最终干净代码和 scenario
先冻结，每次 HTTP 前核对同一批次/代码；账单、capture、manifest 不能闭合时保留全额。
新试聊仍只使用本地 CaptureDelivery。真实 critic、生活链自然使用、后续回忆及每月
100 元目标尚待实测，Goal 保持 active。

## 新默认下的真实 trial-07：两回合已暴露反例

固定干净代码 `8478134495fc3580caad8ff68115329e8f682f43`，新独立批次首次执行
`trial-07`。原始材料封存在
`output/private-audits/consequence-conversation-20260908/trial-07/`，预约/关账证据在
同级 `trial-07-launch-state/`。scenario SHA256
`f84e0a196ff449c71445d757bef7262f8efede41d0d1573fddfaa0c03c9ec2c9`，launcher SHA256
`3b45206e2d85f92182be59d1c6e483e072dfdbc0112cd6e68924844f5c19cf69`。
使用配置中的 DeepSeek v4 flash、character thinking disabled、已配置作者自审；未取得
独立供应商审核资格。交付只落本地 CaptureDelivery，没有真实 QQ。

原计划自适应聊 4–6 回合并快进生活，本次在第 2 回合、虚拟第 1 分钟即按
`budget_admission_denied` 停止。不能把它算成长期旅程完成、忽略用户后自然沉默或
新生活链成功。预约到关账 67.27 秒（runner 执行 65.61 秒），9 次实际 HTTP 都有完整
raw body/usage 与主账结算，
另一次 source rewrite 在 HTTP 前拒绝。精确 manifest 已知费用
**0.28236059999999996 元**（显示为 **0.2823606 元**），从本 trial 的 0.60 预约释放
约 0.3176394 元；不是已经花光 0.60。拒绝的下一笔保守预约为 **0.488604 元**，超过
当时剩余预算。没有抬高上限继续请求，也没有释放旧批次的 unknown。

### 可见对话与来源反例

用户首问：“我刚把一个等了很久的面试推掉了，发完消息反而有点后悔。你这会儿在
干什么？”角色回复包括“我在图书馆，今天没什么课，翻翻书”。首请求
`model-input:99773cd64c6e4b7e84c3b4f49943f415` 的 current/past 世界来源为空，
activity_slices 为空、地点没有 authority、recent_self_experiences unavailable；
只有明确标成背景的惯常作息。原 SSE 参数显式 `world_claims: []`，没有活动意图。
seq15 的表达提案仍为空 claims，seq16 接受，seq25 授权第二条消息。

用户接着问翻什么书，她继续说“在翻一本城市随笔……讲上海老弄堂的”。这两个回合
没有对应的 ActivityStarted/Experience。确定性验证器只检查模型主动声明的 claim，
空数组绕过了实际正文中的事实。这是持续存在的语义漏报，不是 day_sheet 被合法
提升为事实。原输入已有明确禁令；重复加入必填布尔/逐段标签与之前失败的 typed
原型没有实质区别，本轮没有据此做新协议迁移或宣称反例已修复。

### 生活作者、审核与技术失败

World Author 首稿把用户拒绝面试写成角色自己的经历，还补出“昨晚准备了很久”。
结构纠正后的 premise 仍为“知知刚推掉了……”，而同稿 declaration 的来源主体是
用户。focused critic 首稿的 decision 为 unsupported、坐标全空，reason 却说
supported；精确格式重选后，它把四项普通环境生成当成“已有事实伪装成新生成”，
仍未指出 premise 的主体错配。这份来源审核没有证明事实正确，也没有产生可接受
的新生活。原始稿件、两个 critic 输出及失败记录均保留。

纠正请求还有独立成本问题：同一对话反复放入前文已有的完整 schema、硬边界、
manifest 和时间表。作者结构纠正的 48,071 字节 user message 中，这四块占 43,672
字节；拟进行的来源改稿 messages 达 149,407 字节。这是重复权威材料，不能把
下一笔预约很大解释成必须放宽预算；后续只可去除重复，保留原 messages 和失败坐标。

本次预算拒绝后还出现审计异常：来源改稿异常分支写 outcome 却没写配对的 slot，
`RecordedModelResultAudit` 抛出 `slot and outcome audit metadata must appear
together`。已由 `84d098b9` 修复，只补 primary slot，不改变预算、重试次数或 schema。
独立测试先复现同异常，再覆盖真实 UsageStore 准入拒绝、timeout、connection error，
均落为技术失败；拒绝时零改稿 HTTP，原请求 sidecar 与 attempted model 保留，冷开
不重复调用/拒绝记录。相关固定范围 134 项通过；这是模拟供应商证据，未用付费重跑
冒充新生活链成功。

新批次累计已结算 **0.2823606 元**，继承历史后累计已知约 **2.7813168 元**、保守
占用约 **3.0957838 元**。原关账保留未经四舍五入的 manifest 数值。旧封存账本与
历史 unknown 不变，月费 100 元和真人感仍未验收；下一次付费调用须锁定新的干净
代码身份，不能在旧试验上改结果或把这两回合写成 4–6 回合已通过。

## trial-07 后：去除重复纠正材料并追查审核来源投影

`c550b159` 整合仅针对新 `.2` 的两处去重：结构纠正与来源改稿都保留最初的完整
system/user messages，以原 message index、request hash 和字段路径定位同一份
schema、manifest、硬边界和时间材料。`hard_boundary_contract` 明确映射首请求的
`cross_field_authority`。来源改稿仍携带完整拒稿、精确 findings 和独有的完整 no_op
schema；后者不是逐字重复，因此没有为压缩而删除。没有裁剪证据、替角色改正文、
减少审核或改变预算估算系数。旧 `.1` 请求保持原字节；旧 `.2` 恢复读取其保存的
原 sidecar，不用新的构造器重标旧身份。

以 trial-07 冻结材料、原 HTTP 参数和现有保守估算器离线重建：

| 请求 | 原 wire 字节 | 去重后字节 | 原最大预约（元） | 去重后最大预约（元） |
| --- | ---: | ---: | ---: | ---: |
| 作者结构纠正 | 122,885 | 75,047 | 0.408591 | 0.265077 |
| 来源改稿（原试验未发 HTTP） | 149,556 | 83,844 | 0.488604 | 0.291468 |

这比较的是相同旧结果的请求重建与最大预约，不是新的实付费用，也不证明新模型会
返回同样内容。实际首两条 messages、精确失败坐标、拒稿与非 messages 参数均保留。
在 root `c550b159` 的一次六文件检查通过 **143 项，4.01 秒**，包含预算失败审计三类
反例与四项公共 HTTP 去重/旧 wire/原审计冷恢复测试。独立只读复核未确认阻断问题；
分支另有旧 `.2` 原 runtime 写审计、新 runtime 冷恢复零 HTTP 的临时验证，和上述
固定测试范围分列，不累加测试数量。未新增付费调用。

准备“错主体/正确主体”低成本对照时进一步发现：原用户 Fact 和 Dialogue 在 focused
packet 中只有 `capsule_bound_reviewer_baseline_only`，缺少 source_bindings。原合同
不允许用这类资料支持 existing_world，故只把 premise 主体改为用户，也不能把控制臂
预判为来源合格。没有强行补绑定或执行混杂试验；仅留下不可执行的计划 manifest：
`output/private-audits/consequence-conversation-20260908/actor-load-contrast-prepared/manifest.json`，
SHA256 `71790d81d603e1958be57616994cf516262e463f319c9a8f68caab03f54118a2`。

只读追查确认上游 LedgerResolver 已核对原 Fact、Observation、actor、revision/hash；
完整 CapsuleItem 也保留证明。丢失发生在供聊天使用的 `_slice_model_content`：它为
节省 token 主动移除 Fact/Dialogue 的 source_bindings，LifeDevelopment 却复用此
压缩 JSON 作为需要 exact proof 的审核材料，随后被正确降为 baseline_only。
现有测试分别手造“有 binding 的完整值”和“无 binding 的压缩值”，没有贯穿这个
实际 producer/consumer 断点。正在隔离修复 reviewer 专用投影，只读取同一 trusted
capsule 中已选材料的完整证明，保留聊天压缩与旧审计视图。不能直接给 slim 值补原
hash，因为原 value_hash 对应完整值；恢复证据传递也不能宣称已修复模型的语义漏判。


## 精确来源证明已接入 focused review，等待真实复测

根分支 `6ff283fa` 合入专用证明投影，`212a3126` 完成与纠正材料去重的测试整合。
新 capability manifest 为 `life-development-capability.production.4`，对应 focused
packet `.8` / subject `.7`，outcome 仍为 `world-consequence.2`。运行时只给 focused
review 派生局部输入：先重新验证原 typed Capsule 的 compiler tag、完整输出和逐项
source/value hash，再使用其中原已选 Fact/Dialogue 的完整 payload 与 binding。
没有重新检索、扩充选择数量、读取未来状态、改变隐私或增加模型调用；普通聊天和
World Author 输入保持原视图。损坏证明落明确技术失败，focused HTTP 为零，不能
以 baseline-only 材料冒充新资格。不可用 slice 仍不可用。

公开用例贯穿临时 SQLite Observation → Fact 接受 → 实际 Resolver/Capsule →
LifeDevelopment → DeepSeek MockTransport。单 Fact/Dialogue 样本的作者 HTTP 为
45,024 字节、保持不变；focused 由 24,288 增至 25,973 字节（+1,685，约 6.94%）。
这是完整证据的 token 成本，不是新的调用次数或实付账单。旧 production `.2/.3`
审计按原 pin/packet/请求字节冷恢复，新增 `.4` 也通过相同恢复链，均零新增 HTTP；
原 audit_json 未重写，manifest/packet 错配拒绝。

根整合首次检查发现三处旧形状断言：一个新请求仍断言 `.3`，两个纠正测试仍直接
查找已去重的字段。只更新新请求版本及原消息 locator 解引用，保留失败原因、权限
内容、拒绝降级和原请求存在性断言；历史 `.2/.3` fixture 未改成新资格。
固定 `212a3126` 的同一 59 文件检查 **879 passed / 67.82 秒**，日志
`/tmp/girl-agent-selected-proof-gate-212a3126.log`，文件集合为
`/tmp/girl-agent-selected-proof-gate-6ff283fa.files.json`。修改 Python 的 Ruff 与
`git diff --check` 通过。独立只读复核未确认阻断，13 个专用检查亦通过；数量与总门
重叠，不累加。真实模型的语义判断仍未验证。

正常 CLI 不带 `--limit` 的 120 个冻结场景通过，manifest 保持
`b405ce3beb2d6f4ab83b21011341fbe26192bbd24cb2dab3fb94979468c565c9`。
`output/adaptive-companionship-2026-09-08/baseline-selected-life-proof-100.json`
与前片 default 产物逐字相同：233,258 字节，SHA256
`080d3b84cf37a6c07cf54732cea10aa7d2d9ab721d4872a73af9a94a03e92e95`。
没有更换冻结基线。

本片新增付费调用为零，累计已知约 2.7813168 元、保守占用约 3.0957838 元不变。
trial-08 仅准备独立 runner 文件，保留 trial-07 的 runner/原始结果/账单；复用同一
1.20 元批次及每次 0.60 元完整预约，5 个离线启动器检查通过。下一步冻结新的干净
HEAD 后进行真实多轮试聊，查看来源证明能否帮助审核，而不是把机制通过记作主体
错误、聊天正文事实漏报或长期真人感已经解决。小屋与角色移动继续排除。
