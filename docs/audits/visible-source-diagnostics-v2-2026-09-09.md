# 来源拒绝诊断 v2：离线链路资格

截至 `ee695fbd`，诊断已接入入站、主动联系、同角色一次纠正、完整复审及冷验证。
默认仍为 v1；本轮没有真实供应商调用、QQ 投递、生产数据修改或部署。
录制候选仍为 `manual_only / qualification_incomplete`。

## 已改变的行为

第 15 批真实 reviewer 只说明整气泡不闭合，不能据此确定具体争议或认定语义误拒。
新 v2 同时要求争议的 Unicode 起止位置、短原因和相关来源索引。宿主将它们作为
诊断数据反馈给原角色。短摘录明确标为前缀，完整范围及候选、来源表、审核请求/响应
哈希保留。它们不构成新事实、Action 权限、措辞命令或分段放行许可。

Core 的一次纠正、原 pinned Context、原截止和费用估计没有增加。整气泡裁决仍经
旧闭包 parser 归一化；新 schema/prompt/preparation/receipt 各自用独立 `.2` 身份。
冷验证从原候选和材料重新生成请求，再核对独立 reviewer 子审计。

`visible_source_review_version="2"` / `--visible-source-review-version 2` 必须同时启用
required 审核。它与作者工具 1/2/3 的版本选择独立，不能通过响应形状推测启用。

## 验证与反例

| 验证 | 实际结果与范围 |
| --- | --- |
| 根最终新协议、配置、回执及入站链联合门 | 125 passed / 9.62 秒，包含证据大小故障修复；随后追加的三项换版本攻击回归 3 passed / 3.65 秒。两组不重复。 |
| 根原有回执、入站、主动审核门 | 58 passed / 115.24 秒；在最后大小故障分支修复前执行。该修复的故障及正常链在上述新门覆盖。 |
| 隔离主动 v2 与冷恢复门 | 9 passed / 40.75 秒：五种 v2 主动情景、v1/v2 × 接受前中断/完整恢复。包含原截止取消后的已完成记录和未知账单保留。 |
| v1 冻结对照 | 三份准备/回执、工具及三类消息逐字一致；首轮通过与纠正后通过两组冷验证仍返回原哈希。最后大小故障修复后再次通过。 |
| 独立跨版本攻击 | v1 首轮→v2、v1 纠正→v2、v2→v1 三组均重编完整请求并重算自带哈希，自洽回执校验通过，独立审核子审计仍阻止冷验证。已转为上述三项仓库回归。 |
| 大小与 Unicode | 合法单气泡 4096 字符和 16×1024 字符气泡保留全部诊断及完整位置。实际反馈 837 / 3228 字符；引号、控制字符、汉字和 emoji 组合检查通过。 |
| 完整复审 | 纠正作者修好第一处、却在另一气泡新增无依据经历，第二次完整审核拒绝，零 Action。定位非法也不进入角色重选或交付。 |
| 格式化与证据大小故障 | 公共链在完成审核后注入故障，已知物理用量和独立 reviewer 审计保留；没有新增调用或交付。大小反例先红后绿。 |

这些门有部分覆盖重叠，不把各行相加当作不同验收数量。MockTransport 的作者和审核
回答是明确替身；这些断言不证明真实角色能正确改写，也不证明 reviewer 判断正确。
冷恢复为关闭并重建应用/host，不等同于 OS 重启或真实平台回执。

原 final-evidence 超限分支只有裸技术失败，可能漏带已完成审核审计。`ee695fbd`
补齐原作者、reviewer 子审计和已知用量。测试在真实准备及 metered 审核之后降低
证据界来命中该分支，不能声称已经构造自然超过 512000 字节的完整生产样本。

## 字符界与账务边界

Core detail 为 4096 字符、终态 detail 为 4000。v2 每个原因最多 64 Unicode 字符且
JSON 编码最多 96，摘录 JSON 最多 32，相关来源最多 8 项。512000 字节来源表的必需
字段保证索引最多四位。六列行最多 186 字符，16 行与固定外壳保守总界 **3670**，
低于反馈界 3900；不用删诊断或限制合法 span 长度解决空间问题。

供应商预留仍覆盖最终请求/工具的 UTF-8 字节、原 framing 和完整 output limit。
本轮未增加已知或未知真实费用；此前已知累计 `3.112490199999999952` 元、保守占用
`7.0134232` 元保持，第 14/15 批及更早未知分配没有释放。每月约 100 元尚未资格化。

## 可复查入口与剩余工作

仓库回归：`test_visible_source_rejection_diagnostics.py`、
`test_visible_source_diagnostics_receipt.py`、`test_visible_source_diagnostics_runtime.py`、
`test_visible_source_review_version_wiring.py`、`test_proactive_visible_source_gate.py`。
根日志：`/tmp/visible-diagnostics-final-joint.log`、
`/tmp/visible-diagnostics-version-attack-regression.log`、
`/tmp/visible-diagnostics-legacy-integration.log`、`/tmp/visible-diagnostics-final-v1-freeze.log`。
隔离主动日志：`/tmp/review-version-v2-proactive-green.log`。

下一次付费资格先固定来源对照：当前意图与已发生活动、用户当前报告与主体调换；再接
真实角色多轮链。必须重新固定 clean HEAD、新 CLI flag、最终请求哈希和完整预算。
旧第 15 批启动器及证据已封存，不复用或改写。全文诊断在所有取消路径的持久化仍未
得到证明。全库门、真实 v2 对话、完整生活链、QQ 回执、部署及月费验收仍未完成。
