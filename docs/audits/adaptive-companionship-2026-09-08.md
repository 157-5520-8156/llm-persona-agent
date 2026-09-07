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
