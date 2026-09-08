---
status: proposed
---

# 为同一角色提供可纠正的来源拒绝诊断

第 15 批真实试验已越过 v3 结构校验，但 reviewer 只返回整气泡 `unclosed`，没有说明
争议片段或来源缺口。角色重选后保留同一短语，第二次审核耗尽截止。不能从这些标签
判断首次拒绝是否正确；也不能把来源拒绝描述成已经证实的 JSON 错误。
证据见[第 15 批](../audits/launch-visible-source-trial15-2026-09-09.md)。

本文是后续接口提案，尚未实现、默认启用或经过真实供应商验收。只增加可解释的硬边界
反馈；措辞、态度、沉默及是否改变表达仍由原角色决定。

## 最薄生产者与消费者

现有 reviewer 的完整 Beat 裁决保留。独立的 provider verdict/tool `.2` 可附加
`rejections`：每个 unclosed Beat 有一个气泡索引、Unicode 字符起止位置、用于解释的
相关来源索引，以及一段简短 `source_problem`。模型无需重复原文，宿主按已经冻结的
原 Beat 截取引用；位置越界或为空直接成为技术失败，不猜测或修复定位。

诊断不能改变原完整 Beat 的裁决，也不能被当作新事实、来源索引授权或角色措辞指令。
原有 actor、时间、状态、权限校验继续决定闭包。closed/source_free 的 Beat 不得携带
拒绝诊断；unclosed 不得漏诊断。不能凭正确定位只发送候选的其余部分。

| 接缝 | 预期变化 |
| --- | --- |
| `visible_source_closure_protocol.py` | 增加独立 `.2` 工具、schema、prompt 和 digest，保留 `.1` 生成字节及严格解析。规范化 `VisibleSourceClosureWire.1` 不包含诊断。 |
| `visible_source_review_receipt.py` | 准备请求 `.2`，解析并单独验证诊断；`VisibleSourceReviewRejected` 保留原 v1 构造方式，新诊断只进入拒绝反馈。 |
| `visible_source_runtime.py` | 将诊断转为有界反馈，绑定原候选、来源表及审核调用哈希，送入现有 `failure_detail`。 |
| 入站及主动作者 → Core | 传递已验证反馈，复用同角色一次纠正和原 pinned Context；不新增状态机、deadline、调用次数或角色行为规则。 |
| 通过回执及冷验证 | 新 `receipt.2` 绑定 request/verdict `.2`；诊断数组为空，授权仍使用原规范化完整裁决。 |

`required.1`、原 capability、source-table `.1/.2`、外层 evidence `.1` 和 ModelResultAudit
不因为诊断附加而扩权。具体启用参数及调用入口必须在实现时覆盖入站、主动和冷恢复；
配置为 v2 不等于真实资格。

## 保留原始身份和成本边界

回执版本只选择固定编译器。冷验证应从原 Proposal、原 requirement/source table 和
实际作者 aliases 重建 expected request，再与独立 reviewer 子审计 request_hash 比较。
不能信任回执携带的请求，不能给旧回执换 `.2` 标签并自行计算哈希就通过。
不得原地修改 v1 prompt 或向旧响应增加 optional 字段，以免改变历史逐字恢复。

Core 纠正字符串现限 4096 字符，终态 failure_detail 限 4000。编码后的诊断反馈需预留
固定说明空间，例如整体限制在 3500 字符以内，并验证最多合法 Beat 数的可表达性。
禁止用字符串切片截断结构 JSON。超限、定位非法和审核取消仍是技术失败；诊断不打开
额外调用或更长截止。相同来源与一次纠正仍不足时按既有技术重试处理。

ProviderSubcallAudit 目前只保存响应哈希和用量，不保存拒绝正文。这个最小改动不能声称
所有取消路径都已持久化诊断正文；新增审计正文需要独立来源、隐私和恢复设计。

## 实现前要锁定的反例

1. v1 请求/schema/回执与冷恢复逐字保持；v1 添加诊断字段仍拒绝。
2. 回执版本换壳并重算自带哈希仍失败；不同版本 request/response 不能混用。
3. 漏 Beat、重复 Beat、只审争议片段，不能因有诊断而通过。
4. 越界、空片段、负数、布尔索引、UTF-8 字节位置混用 Unicode 字符位置均拒绝。
5. 裁决与诊断覆盖不一致不能被本地补齐、删除或改判。
6. 诊断中的虚构事实或其他主体来源不增加事实或 Action 权限；不进入角色记忆来源。
7. 角色只改争议片段、同时在其他段新增无来源事实，完整复审仍拒绝；最多原有一次纠正。
8. 超限、审核失败及纠正取消保留已完成调用与未知费用；没有追加 deadline 或模板交付。

首次真实资格应使用固定来源和有界对照，分别考察当前意图与已发生行动、明确用户报告与
主体调换。不能用只断言诊断字段存在的单测证明 reviewer 判断正确，也不在本片扩张
多主体完整气泡的语义能力。
