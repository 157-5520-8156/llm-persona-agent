# 2026-09-08 自主迭代与逐回合试聊

本阶段由用户明确授权创建 Goal 并持续迭代。仍在 `codex/living-continuity` 隔离分支工作，
生产数据库、配置与实际 QQ 不在写入范围。阶段新增真实模型试验总预约上限 ¥2，每个 fresh world
上限 ¥1；此前三次试验的 ¥0.8820866 持续累计，不能用新数据库抹去测试成本。

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
