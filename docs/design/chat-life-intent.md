# 聊天中的显式生活意图

## 领域边界

`life_intent` 是角色在同一次入站 InnerTurn 中主动提交的未来活动意图。系统不从
`messages`、`wants` 或其他私有文字提取行动。表达、沉默、Appraisal 和生活意图是
同一角色结果的独立部分；Appraisal 缺省或被拒绝不会抹掉合法意图。

当前仅开放 `self_directed`：本人的私人活动计划，不写地点、参与者、NPC、世界事件
或活动结果。`intention` 是角色的原文，不是行为分类或世界事实；来源 reader 明示
`accepted_intention_only_not_embedded_history_or_outcome`。依赖外界配合或新环境的
事情需要另一个有明确权限的能力，不能通过本字段直接结算。

字段为 `execution_scope`、`intention`（1–480 字符）、`start_after_seconds`（0–86400）、
`duration_seconds`（60–21600）、`importance_bp`（0–10000）。整数严格拒绝布尔值。
slim 中与 messages 同级，canonical 中置于 appraisal_draft；reply_only、full_turn 和
沉默均可使用。缺省或 null 表示无生活提案。非法结构在 Gate / 角色物化入口失败，
通过原有同一 InnerTurn 的一次受约束纠错处理；仍非法保留技术失败，不降级成丢字段。

## 写入和消费者

1. `InboundToolContracts` / `AppraisalDraftWire` 保留并校验显式字段。
2. `inbound_appraisal_wire._decision_proposal_from_draft` 绑定宿主 actor 与已经验证的
   Observation，生成 `life_intent.plan` TypedChange。使用通用 Proposal registry `.3`；
   原通用提案缺省仍 `.1`，独立 FactCommit v2 的 `.2` 不变。
3. `WorldRuntime._settle_unified_inbound_state` 消费已审计的原提案。
   `ChatLifeIntentRuntime.accept` 从原 proposal audit、ModelResult 和 InnerTurn lineage
   反向校验并派生 ActivityPlanned。原提案持久化后，表达接受失败也不会取消这个独立决定。
4. Plan 沿用 `ActivityLifecycleWorker` 的既有机会与角色选择，之后才能产生
   ActivityStarted / Paused / Resumed / Completed。reader 向该 worker 提供原意图。
5. `LedgerProjectionContextResolver` 组合 chat 和 Life Development 的
   `ActiveActivityReader`。聊天只获得已接受意图和真实 start/resume 绑定的当前状态；
   当前活动 source ref 沿既有确定性闭包接受校验。

没有新增聊天模型调用或通用审查模型。活动开始等之后的角色选择使用既有生命周期模型
及预算，因此开启更多生活计划仍可能增加后续生活调用；这不等于月度总成本已验证。
`life_ecology` 被显式禁用的配置不会自动启动活动。

## 时间、effect-once 与重放

开始偏移相对原角色模型 evaluated World revision 的权威 logical time。延迟消费沿用
这个原选择时刻；原消息之后再次进入角色重试且第一次选择意图时，使用新 snapshot 的
时刻，而不是旧消息的时间，也不是消费者临时的 now。

同一 World + Observation 只有一个生活计划效果。表达重试改变提案 hash 不会创建第二个
Plan。同样的意图返回已接受的原计划及原模型来源；不同意图不能未经新变更协议替换它。
ActivityPlanned 的 `chat_intent_origin` 绑定原 proposal event/hash、change、model audit、
InnerTurn、snapshot 与选择时间。生产接受与 reducer 重放使用同一反向派生；伪造 actor、
来源、原模型或计划内容均失败。旧 ActivityPlanned 没有此字段时保持原字节布局和读法。

## 技术失败日志

`ChatLifeIntentAcceptanceFailed` 是 Deliberation 事件，不能充当角色意图或生活事实。
它记录原 proposal event/hash、具体 change/hash、actor、当前 World revision、失败次数、
有限的技术原因码和下一次时间。CAS 失败后分别等待 30 秒、120 秒，第三次失败结束该
生活部分；来源/权限非法立即技术终结。新提案的角色选择仍是独立权限，不能改绑旧计划。

`WorldRuntime._drain_inbound_state_settlement_once` 通过这个持久日志恢复原提案，
未到期和终态不占用本轮工作，也不阻止同一提案其他部分或其他后台工作。
`declared_due` 注册 `life.chat_intent_acceptance` 唤醒；成功 Plan 抑制旧失败 due。
失败日志本身的提交仍需当前 CAS；该提交也失败时显式返回 journal cursor conflict，
不能声称已持久化该次失败。存储不可写时无代码能保证落盘，原提案仍是待消费记录。
原始哈希、引用与失败记录在 owner dashboard 的通用字段策略中 withheld。

## 验证与未证明的部分

公共 HTTP 测试以实际 QQ host、真实 provider adapter 和 SSE transport 跑临时 SQLite
与重启，只把网络换成 MockTransport；测试不访问 QQ、真实供应商或生产数据库。
覆盖显式提案、普通愿望无效果、两种格式/沉默、一次角色纠错、原 Appraisal 恢复兼容、
CAS/重启、表达重新生成的 effect-once、原选择时间和启动后的可读来源与最终交付。
这些证明机械链条，不能证明角色会自然选择该能力或穷尽正文未声明的事实。

已完成的聊天自主活动现在通过 `recently_ended_activities` 提供最近最多 3 条可读材料，
每条保留原先接受的意图（最多 480 字符）与实际 `ActivityCompleted` 结束来源。
读者校验同一角色、隐私、当前 cursor 和原计划来源；仅精确的结束事件可在 `past_world`
引用，原 ActivityPlanned、意图正文或哈希不因此成为过去事实权限。planned / paused
不能冒充完成；这项视图不进入 World Author 的私有意图材料，也不进入经历摘要或长期 Recall。

`completion_scope=activity_lifecycle_ended_not_intention_fulfilled` 明确表示生命周期结束，
不证明目标实现、摄影内容、遇到的人或任何其他结果。本实现不创建 WorldOccurrence 或
Experience；结果生成仍需要独立、来源闭合的能力。来源范围校验不能检测任意正文把意图
改写成成果的语义漏洞，也不能证明模型没有漏声明事实。

已验证的本地 HTTP 五分钟场景包含计划接受、启动、结束、重启和下一轮回答：最终
Proposal 仅引用该轮 Observation 与精确 ActivityCompleted，随后产生 ActionAuthorized。
第二条本地捕获回执为 `provider_accepted`、`is_terminal=false`，不能声称终端交付。
场景及冷启动重放通过，但本次完成来源能力没有真实模型或真实 QQ 验收。

迟到接受计划的公共 HTTP 红测已闭合：两次 CAS 后 150 秒才接受的计划保留原窗口，
接受事件使现有 `life.ecology` 的下一次考虑提前到接受后 1 秒（若开窗更晚则等开窗）。
宿主提交真实 ClockAdvanced，原 ActivityLifecycleWorker 将接受事件及其原审计来源
加入该次能力与因果来源；角色仍通过已有 select/no_op 选择，未复制生活执行器。

`ChatLifePlanConsiderationRecorded` 在 Deliberation 中记录该计划初始机会的结论。
它绑定接受 Plan 的 event/hash、原聊天选择来源、当前真实 Clock，以及完整原角色结果
及模型审计；select 还绑定原生命周期提案。原 Plan 窗口和状态由原生命周期事件负责。
合法 no_op 只表示这次新计划机会已考虑；关窗仍能成为后续合法机会，不自动放弃计划。

技术失败与角色 no_op 分开：同一来源最多三次尝试，每次仍由原角色获得一次
精确纠错；失败按 30 / 120 秒退避，attempt_ordinal 和 next_retry_at 落入同一
journal。已有 life.ecology 到期读取汇入该证据，未到期不调用模型，重启保留次数。
次数用尽或下一次已超过合法活动窗口时，明确记录技术耗尽或窗口过期；不写角色拒绝。

若 Core 已持久化合法 no_op，而进程在生活 journal 提交前中断，恢复通过
CharacterInterior 的窄只读终态入口校验原 terminal / prepared 字节 hash、typed
snapshot、作者与 private lineage、原能力 ref/hash/source binding，并补原 declined。
原角色 Clock 与当前记录 Clock 分别绑定，不从 spent 标记猜测决定，也不在新 Clock
重问角色。原 snapshot 没有完整能力 payload，这里不声称恢复了不存在的原文。
一个已选择但尚未物化的原生命周期结果则仍是消费技术失败，进入有界恢复尝试。
公共 HTTP 崩溃加重启测试验证只有一次原 no_op 模型请求、没有第二次生活选择。

新路径不增加聊天模型调用或审查模型；新接受计划提供一个已有生活角色机会（仅技术失败按上述次数重试），
仍受原角色调用和预算机制约束。机械测试不证明每月 100 元预算或真人感达标。
