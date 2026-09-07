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
