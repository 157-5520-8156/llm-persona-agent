# 完整正文审核凭据的准备与核对

状态：独立纯模块，未接入 ModelOutput、持久审计、Acceptance 或运行时发稿。
小屋和角色移动排除；本片没有真实模型调用。

`prepare_visible_source_review` 从完整 DecisionProposal 和原已选来源表编译实际
审核 messages、forced tool 合同及完整 logical request identity。它绑定整个候选
（包含不可见字段）、每个 expression change 的有序 Beat、原表/材料/pin、alias
映射和工具 schema。当前合同支持 1–16 个全部为 inline text 的 Beat；不跳过
不可读取载荷，不截断过量 Beat。没有可见表达的决定不需要本模块的通过凭据。

`record_visible_source_review` 先校验原作者、候选材料、审核调用、请求和完整
返回 hash，再解析完整 verdict。绑定正确且合法的 `unclosed` 是语义拒绝；
错调用、错主体、漏 Beat 或非法 wire 是技术错误，不能要求角色据此重写。
此版本仅记录一次初始审核；没有 reviewer correction 请求、隐藏重试或作者重选。

`verify_visible_source_review_receipt` 需要调用方提供原 preparation 和独立的
author/reviewer 审计。冻结对象、自洽 hash 和收据里写了某个调用 ID 都不构成
调用已发生的证据。不得把 receipt 自己的字段抄成 expected；后续接入必须从
原 ModelOutput/CharacterInterior lineage 和真正的 ProviderSubcallAudit / 已落盘
ModelResult 取得锚点，并执行读侧 join。仅通过本模块不产生任何 Action 权限。

作者 `proposal_material_hash` 是完整宿主物化 Proposal 的 hash；审核
`response_hash` 是 provider 方法返回的完整工具 arguments 字符串 hash。
它们与 HTTP 响应壳、诊断 excerpt、含 Recall trace 的外层 ModelResult hash
含义不同，不可互换。实际供应商请求需要携带 preparation 的全部 identity_extras。

## 不可漂移的版本合同

`visible-source-review-request.1` 和 `visible-source-review-receipt.1` 冻结了
候选 canonical 序列化、Beat 投影、来源表投影、messages、工具 schema、identity
extras，以及 verdict parser 的规范化和资格规则。恢复会重新编译并逐字核对。
未来若修改这些行为，须新增版本并保留原版本的 compiler/parser 分派；不能让旧
receipt 使用变化后的当前 helper，也不能只把一个常量改成 `.2`。

固定离线文件 `tests/world_v2/fixtures/visible_source_review_receipt_v1.json`
由公开临时 SQLite Fact/Dialogue 生产者及 typed Expression materializer 生成，
调用审计是明确的测试夹具。去掉末尾换行后 SHA-256 为
`6bb35e49113924e8ffc66f09bd3e0e99977eb41772a622a0e9348961d2f5f7a9`。
它用于发现同版本漂移，不证明实际供应商审计接入。

## 验证与后续边界

专属测试包括真实 adapter 的 MockTransport 请求核对、完整候选/尾句/顺序变化、
alias 和材料 scope 变化、同 ActivityStarted ref 的 Situation 与 Active 材料
交换、伪造调用 ID，以及语义/技术失败分类。来源测试使用公开临时账本生产者，
未伪造已接受的活动事件。四项错调用加 `unclosed` 的反例先失败，修复后通过。

来源覆盖仍继承 composer 的有限范围；没有部署身份原材料 pin、额外 Recall
材料或所有内心来源的完整资格。准备材料上限为 512000 UTF-8 bytes；这不是未来
持久审计载体的容量承诺，持久化还须核对全对象字节/节点及 audit JSON 的限制。
下一片需要独立的 required policy，不能因为删掉 receipt 就退回旧合同。
旧 ModelResult `.1`–`.8` 和旧 expression manifest 继续保持原字节和原资格。
