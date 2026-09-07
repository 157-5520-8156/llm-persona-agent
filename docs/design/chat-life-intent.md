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

活动完成仅证明生命周期结束，不证明摄影内容、遇到的人或任何其他结果；本实现不创建
WorldOccurrence 或 Experience。完成后结果生成和可读的完成来源仍需独立能力闭合。

当前保留一个实际 HTTP 红测：两次 CAS 后 150 秒才接受的计划仍保持原选择时刻的
开窗，接受成功后需要可靠的“新计划”生活机会。现有追加
`renewed_plan_catalog` 路径只在 Life Development 的同一个 ecology wake 内，不能
仅从聊天重复调用已结束的 wake 来冒认该权限。该测试没有改窗口、延长场景或放宽
`completed`，直到独立的已接受计划唤醒路径补齐。普通准时接受的计划已经验证启动、
读取、引用交付，以及选择 complete 后只有 ActivityCompleted 而不产生虚构经历。
