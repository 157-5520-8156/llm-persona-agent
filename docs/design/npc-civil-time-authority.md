# NPC 输入的世界本地时间

trial-06 的 NPC 请求 `18143602aa4742f4aac989a9e1445a90` 只呈现
`2026-09-08T03:07:00+00:00`，没有本地 `11:07+08:00`；它的回答写了夜晚。
这证明输入表示缺口，不能证明它是误判的唯一原因。该 NPC 的图书馆地点有注册
事件支持，不能连同时间错误一起判成无来源地点。

## 只读权限

`NpcSocialWorldSnapshot.civil_time` 使用 `npc-civil-time.1`，进入
`NpcActorProfile.now`。原 `logical_time` UTC 和 `current_location_ref` 保留。
本地 ISO 时间由当前 pinned Clock 和唯一已提交 `BiographicalTimelineConfigured`
的时区推导，不使用进程时间、城市或地点 ref 猜测。

读取核验事件 ID、World、类型、逻辑时间、commit 成员与 World revision、投影
payload hash 及原 payload 字节 hash，再解析已提交时区。若部署 catalog 存在且
时区名不同，明确 unavailable；不让 catalog 的可变配置替代 World 权威。
换算复用 `LocalChronology`，不会改事件时间或 elapsed-time 算术。

来源复用 `WorldLifeSourceBinding`，只增加 `/logical_time_to` 或 `/timezone_name`
JSON pointer、对应标量值及 `exact_time_field_only` 范围。完整 timeline document
不传给 NPC，来源 event ref 不是其它传记字段的读取或陈述许可。Clock 仅证明时间，
不证明天气、活动、地点或动机；原含混的 `clock_is_opportunity_not_fact` 请求提示
改为 `clock_proves_time_only`。

可用视图的两个来源接入原 NPC 引用验证及状态 provenance。仍使用 32 个引用上限，
优先保留这两个已显示字段的来源；没有可用时间来源时保持原裁剪行为。
缺时区、缺当前 Clock、读取失败、hash 不一致或配置冲突返回明确 unavailable，
不带推测时区和本地时间。完全缺少 wake 时，原公共入口直接拒绝机会且不问模型。

## 保留与限制

不改 NPC 的既有内心、关系数值、位置、行为或 no_op 选择。后续请求同时携带当次
时间和原 `my_last_state`，由 NPC 自己决定如何理解；不以模板覆盖旧错误场景。
没有新 World 事件、模型车道、producer inventory 或额外模型调用。

新视图合同和正文进入原 canonical request hash；已有 ModelResult/Proposal 的
原请求与事件字节不重写，原 replay/recovery 身份公式不更换。旧快照对象缺少
此字段时，默认是 `unavailable/not_compiled`，不会凭空获得新的来源覆盖。

公共 DeepSeek MockTransport 回归覆盖上午、UTC 与本地日期跨越、匹配生产 catalog、
前后两次角色输入及 `my_last_state` 原文、完整来源和未泄漏 timeline document；
本地模型 fixture 覆盖来源缺失、读取异常、hash 破坏、catalog 冲突以及 32 引用预算。
所有测试禁用全局 debug 账本，只有临时或内存 World，无真实 HTTP、QQ 或生产 DB。
这些检查证明输入与来源机制；模型是否因此更少犯时间错误，仍未经真实验收。
