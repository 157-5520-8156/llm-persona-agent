# 每日首次生活机会的自主意图

状态：设计与公共 RED；生产边界待根任务审阅。

## 已确认的缺口

`ActivityLifecycleWorker._advance_once` 在空 Plan 的 `no_openings` 处返回，
因此既有 `day_open` Occasion 尚未进入 CharacterInterior。聊天虽然已经能主动
给出 `LifeIntentDraft`，没有聊天意图且 World Author 选择 no_op 的世界仍没有
独处时提出首个自定活动的入口。

这补完执行计划 H8 明确留下的“首次考虑只是已有活动选择”缺口，不把作息骨架
变成活动，也不扩大 World Author。公开测试替模型明确选择意图，只证明运输和
接受能力；不能证明真实角色愿意行动或聊天事实漏报消失。

## 最小 producer 和 consumer

复用现有生活 due、真实 `ClockAdvanced`、`ActivityLifecycleWorker`、
`activity_lifecycle_choice` 和 `day_open`。资格限定：当天尚未花费首次考虑机会、
存在当前准确 Clock wake、没有可执行的活动 opening。该轮增加有版本的
`self_directed_intent` 能力，角色可以给一项 `LifeIntentDraft`，也可以 no_op。
已有 Plan 的 select/no_op 协议和 timing closure 保持原义。

这一片只提供一项意图，不实现旧愿景中的 0–3 项日计划，不另建定时角色或反思
刺激。`LifeIntentDraft` 的自由正文、相对开始时间、持续时间和重要度原样复用；
不提供地点、他人参与、执行完成或客观结果权限。

现有 activity purpose 是 decision-only。拟保留同一次角色输出，在宿主侧通过
显式版本的 bridge 将 `self_directed_intent` 决定转成完整审计 Proposal；
不能开放任意 Proposal，也不能把 Clock 伪装成聊天 Observation 或世界 settlement。
新 `DayOpenLifeIntentOrigin` 绑定原 world/actor/local day/timezone、Clock event
revision/hash、原 selected_at/cursor、capability hash、Proposal、ModelResult、
InnerTurn 和 snapshot。新接受器只提交 private、空参与者、无地点的
`ActivityPlanned`。Plan identity 由 world/actor/原 day_open 来源确定。

已接受 Plan 继续由原生命周期选择 start/complete；意图本身不是开始事件。
原 `RoleLifeIntentActivityReader` 的来源验证分支必须接新 origin，下一次角色
Context 才能读到原意图和真实状态。原 late-plan initial consideration 也需识别
这个真实来源，不能因日机会已 spent 永远不能启动。

## 恢复、频率与硬边界

- 原 terminal 是作者结果；daily spent 只作频率门，不能替代 no_op 决定。
- 新域 journal 记录明确 no_op、已选择意图和技术失败。已支付 terminal 后、
  journal 或 Plan 提交前中断，先用 `completed_considerations_for_source` 恢复原
  决定，继续原 Proposal/Plan，不重新调用，不改绑后来的 Clock 或时间。
- 正常 daily no_op 为终态，不新建 Plan。技术失败不算 no_op；复用技术退避
  30 秒、120 秒、最多三次以及原机会失效边界，due 合入既有生活调度。
- 最多一个新的 day_open 考虑/当地日；纠错沿原同角色一次。失败重试会额外
  调用，原 actual-request 预算准入保持。31 次/月只是正常考虑次数上限，
  不承诺月费已达标；启动/结算等原有调用另计。
- 原 activity `.1` 决定、旧 capability、旧 Chat/World origin 的审计与回放
  不重标，不获得新 Clock 意图权限；新字段省略时保留旧字节。

## 拟定文件边界

本 lane：activity worker 与 structured role/tool 的新版本窄分支；独立
day-open 意图 contract/acceptance/consideration；ActivityPlanned 新 origin 与
对应 reducer/event/projection 注册；旧 late-plan/角色活动 reader 的新来源
分支及 production constructor/due 组合；新增公共测试。

这些来源闭包是必要依赖，不能仅改 worker 早返便宣称完成。根任务审阅后确定
最终 type/registry 名称。不会修改 World Author、聊天来源协议、QQ、配置或
旧测试/冻结基线来获得通过。

## 公共验收矩阵

首条 RED：真实 SQLite app（空 Plan、无聊天/settlement）接受 Clock → 实际
DeepSeek MockTransport 的 activity role 请求 → 角色明确 intent → Plan。
不手工写 accepted 事件，不直接调用私有 worker。

随后：同源 no_op 不产生 Plan；合法意图 → 原 start → 下一聊天 Context；
错误 Clock/actor/hash/旧合同降级拒绝；terminal 后故障及冷重启零重问、
原时间/字节保持、effect-once；技术失败有界退避与日界线；实际请求字节与
预算准入统计。所有 provider 为本地 MockTransport。
