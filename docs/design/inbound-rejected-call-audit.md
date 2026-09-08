# Inbound 作者拒稿的完整调用审计

状态：修复 `76f8d26b` 中 author 可自行保留的结构拒稿证据。不修改 Core、Faculty、
角色纠正次数、生产配置、来源审核或冷恢复协议。

## 原缺口与修复范围

实际 Core 的首个 `ValidationTechnicalFailure` 转成 `_RoleResultContractError`
时，调用身份与诊断片段可以继续传递，原用量和 authored-candidate 审计没有传出。
公开 MockTransport 反例中，首调用返回 113 输入 / 57 输出，第二调用返回
227 输入 / 83 输出；成功纠正和二次无效两条链此前均没有独立首拒稿 ModelResult。

`_InboundCharacterAuthor` 现在在 atomic provider 方法完整返回后、工具 `unwrap`
之前冻结 `AuthoredCandidateInvocationAudit` 的候选材料。只有完整合并 envelope、
Appraisal 或 Expression 的现有结构验证失败，才将该材料装入技术异常并保留在
有界的 author 缓存中。首稿使用 `purpose=primary_initial` 和
`outcome=validation_rejected`。这不授予 Proposal 或 Action 权限。

缓存身份覆盖原 ModelInput，包括 call/attempt、Capsule、完整三元 cursor、触发
证据与 Context。仅移除 Core 添加的 `inner_life_snapshot.role_result_correction`
坐标以匹配同次纠正。`correct_role_result` 在清理旧候选前取出原审计；成功时附在
获选 ModelOutput，第二次 `ValidationTechnicalFailure` 时合并两次拒稿审计。
第二次的 `role_correction` 身份来自该明确调用端口和真实第二调用 ID。
按 model_call_id 合并时，相同证据只出现一次，同 ID 的冲突证据拒绝合并。

## 哈希和用量含义

`response_hash` 是完整 provider 方法返回字符串的 SHA-256；forced tool 路径中
这对应完整 arguments 字符串，包含尚未移除的 result_kind 和 transport null
字段。它不是 HTTP 响应外壳哈希，也不是 materialized Proposal 的哈希。
`request_hash` 与真实 provider invocation identity 一起冻结，不从当前 Context
重新生成一个过去调用的身份。

原 `role_rejection.rejected_raw_hash` / 800 字符 excerpt 继续作为拒绝材料诊断。
该材料可能已经规范化或只是局部 draft，不能替代完整返回哈希。没有完整返回或
无法匹配原 invocation 时，不创建这个 candidate 证明；保留原技术异常及明确的
证据不可用日志，不用 excerpt、猜测调用 ID、补零用量或新 pin 填补。

获选 `ModelOutput.usage` 保持其已有单次获选调用的含义；首拒稿的精确用量在独立
candidate ModelResult 中。二次无效时，terminal 的原第二次用量与第二个 candidate
可能引用同一个 provider_usage_ref，这是同一物理调用的证据，不能相加。已有的
Recall 汇总也不能再和对应子记录重复计费。本修复不改独立 provider usage 账本、
价格或历史费用。

## 离线验证与明确未覆盖项

`tests/world_v2/test_inbound_rejected_call_audit.py` 使用临时 SQLite app、实际 Core、
whole-candidate author 与 MockTransport，覆盖成功纠正、二次无效、三类结构失败、
五种错 pin（包括只有 presentation 字节不同），以及不能由异常片段补造完整返回证明。
原始 arguments 超过 800 字符，
测试逐条核对完整 hash、传输边界已验证的 request hash、调用身份与不同的用量。
另用内存 provider observer 分别记录两次实际 adapter 调用，不写 spend 数据库。

此缓存是进程内状态。它不覆盖未完成调用、原结果返回后进程崩溃、纠正前的持久
checkpoint、Faculty/Core 自身新增的拒稿、早于这些结构验证点的 Recall 解析异常，
或第二次普通 timeout/exception 的通用异常通道。已有 prepared-turn / Recall
checkpoint 和完整获选输出 codec 不自动补全这些边界。技术失败仍是技术失败，
不能宣称所有失败调用、跨重启两调用上限或正文审核已闭合。
