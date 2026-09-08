# 完整候选的作者载体

状态：显式、默认关闭的作者载体能力。尚未安装完整正文审核 guard、持久审核凭据或
生产配置；这份验证也不代表模型语义或生产验收。

`_InboundCharacterAuthor(whole_candidate_mode=True)` 使用已有完整 atomic
decision / Recall 工具合同。`propose` 返回一次作者调用的完整 appraisal 和
expression；`propose_stream_head` 仅作为兼容入口委托同一个完整调用，返回的
`ModelOutput.semantic_stream_part` 与 `provider_parent_model_call_id` 均为空。
不会创建增量 session，随后请求 tail 会报告 continuation unavailable，而不会
再次调用作者。原有 Beat 数量、顺序、能力边界、候选物化和作者用量字段继续保留。

Core 的一次受约束纠正通过 `correct_role_result` 清理旧候选后仍走 atomic，
即使同一供应商支持 stream。Recall 仍由 CharacterInterior Core 处理，第一次
可选择召回；召回后的最终调用沿用 `character_inbound_after_recall_v1`，返回
完整决策且不能再次开启召回。这里不使用 compact `reply_only` / `full_turn`
载体，不将旧 stream 字节重标为新的 atomic 调用身份。

接入时必须同时将 Deliberation 的 `expression_episode_mode` 设为 `off`。
这使主调用直接走 `propose`，也不预启动 stream tail。单独报告
`stream_provider_available=False` 不能替代这项组合设置：旧 stream 调度明确
拒绝自动退回完整调用，且 atomic 头与已启动的 tail 混用可能重新询问作者。
本切片不修改这些既有调度语义，也不修改任何生产设置。默认 `False` 时，原
stream 模式与 compact 合同保持不变。

未来 whole-candidate guard 应在完整作者候选产生之后、首个可交付 ModelOutput
释放之前审核。等待完整候选会增加首 Beat 延迟，后续还需计入实际审核耗时；
这里不承诺原 stream 首 Beat SLO。来源 composer、审核语义、一次纠正的拒稿证据、
持久 receipt、冷恢复和 replay 接受边界仍由各自的接口闭合。

## 离线证据与已确认限制

专属公开测试组合临时 SQLite app、实际 CharacterInterior Core、Deliberation
`off` 与 DeepSeek `httpx.MockTransport`，覆盖正常一调用、两调用纠正、二次无效
零 Action、Core Recall 后完整最终稿及两个作者入口。测试核对完整 Beat、原
snapshot/cursor、获选作者 lineage 与 ModelResult/Proposal 引用；Recall 的
控制转移单独留存 100 输入 + 100 输出，获选汇总为 200 输入 + 200 输出，不能
将父记录与汇总再次相加计费。

原始 RED：在同样的 app `off` 组合下，支持 stream 的供应商首次收到
`character_inbound_initial_v1`；Core 收到非法私态后，第二次请求却变为
`stream=true` 和 `character_inbound_compact_gate_v2`。新增作者模式使两次
都使用 atomic 工具，完整两句进入原有 Action 流程。

在基线 `2256967d` 还确认一个独立限制：上述两次 MockTransport 响应各含
100 输入 + 100 输出，但 World V2 的 ModelResult 事件只留存获选调用的
100 输入 + 100 输出。`InboundTurnFaculty.consider` 将首个
`ValidationTechnicalFailure` 转为 `_RoleResultContractError` 时未携带其用量。
本切片只验证获选调用的身份与用量绑定，不声称纠正链全部失败审计或费用闭合，
也没有修改该转换。测试禁用了 debug usage ledger，因此没有验证独立 provider
usage 主账本是否保留两次调用；不能将这一 ModelResult 缺口直接等价为实际账单漏记。

专属测试：`tests/world_v2/test_whole_candidate_author.py`。
