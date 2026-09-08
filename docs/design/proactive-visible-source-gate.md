# 主动联系的完整可见正文审核

本片将显式 `visible_source_review_required` 接到实际生产主动联系链；默认配置不变。
角色仍通过同一 StructuredRole 工具选择完整 Decision，包括 now/later/silent、全部
Beats 和私态。系统只验证原来源权限与完整审核凭据，不删句、不编写角色回复、不替角色沉默。

## 接缝与原始权限

生产 application 将 required 标志同时传入主动 Deliberation 和 Expression 接受 policy。
原 Trusted Capsule 上已有的 `compile_requirement` 生成原 selected source table；新
`character-interior-proactive-capability.2` 将该 requirement、原 ModelInput、世界、角色
绑定进 `proactive-reviewed-turn-capability:sha256:`。私有 requirement 不进入作者正文。

`ReviewedProactiveStructuredRoleFaculty` 继承原 Structured Faculty；原工具构造、作者解析、
Core 生命周期和一次纠正仍是唯一角色路径。局部 metered facade 使用同一个注入模型和
HTTP pool；调用捕获使用 ContextVar 和当前 task 身份，不在模型实例上保存当前请求。
原 metering、准入、timeout 和 cancellation 语义保留，不新建客户端或放宽预算。

完整 Decision 形成 canonical Proposal 后，无论 `world_claims` 是否为空，所有可见 Beats
进入既有 `visible_source_runtime.review_candidate`。合法 unclosed 或精确 typed claim
source-lane 错误只花费 Core 的一次同角色纠正，完整替换稿再次审核。审核技术失败或宿主
准备失败直接记录技术失败，不消费语义纠正。非可见角色 Decision 不调用可见正文审核。

接受和回放核对三个独立原锚点：

- 原 CharacterInterior lineage 的显式 proactive purpose、世界、角色、causal sources、
  epoch、inner turn、opportunity 和 capability hash，不能借用 inbound 或其他 purpose。
- 原 actual author request hash：`visible-source-proactive-author-request.1` 保存实际
  messages、tools、tool choice、temperature、完整 identity extras；aliases 只从该次
  `citeable_sources.items` 提取。原 capability 的私有材料由原 requirement 重建并核 hash。
- 原独立 reviewer subcall、完整 Proposal/Beat 映射与既有 receipt；缺失、改正文、改表、
  改 alias 或删 required marker 均不能绕过接受。ExpressionPlanBudgetPolicy 与原 capability
  各自要求凭据，ModelResult `.9` 降成历史 audit 也被 reducer 拒绝。

Core 终态保存有界 `character-interior-proactive-reviewed-decision.1`，包括完整 ModelOutput
和普通 model_dump 排除的调用审计。恢复时先验原 Core 作者，再用原 request 重验 receipt；
原 `.1` proactive capability、既有 inbound author carrier 和历史 audit 不迁移、不补授资格。
`whole_candidate_mode=True` 可独立于 required 标志；精确旧 `.1` capability 保留原路径，
损坏的 reviewed capability 不能降级。source table/compiler 与 receipt 合同沿用现有版本。
`.2` verifier 复算原 proactive source frame；若未来改变该 frame 的 canonical 构造，必须
保留本版本解释并另开新 capability 合同，不能用新 prompt/source 映射重解释旧 receipt。

## 离线验收与限制

公共 QQ host 测试只替换外部作者、审核供应商 HTTP 和 QQ delivery。真实 host 执行
inbound、时钟、主动机会、Core、Proposal、Expression 接受、Action、回放；套件禁止 socket
联网并禁止读取环境凭据。空 claims 的明确无来源反例，及非法非空 source ref，均验证一次
纠正和失败零 Action；完整合规两 Beat 主动正例实际授权、fixture 收到两份文本，review=1。
同 host 的 inbound 正例保持 review=1。审核时断言零主动 Action；作者、纠正与审核共用
同一原绝对 deadline 和 budget 对象。actual body/capability/source table/receipt 篡改和
旧 audit 降级被拒绝。

冷恢复覆盖两个窗口：已有主动 Action，以及 Proposal 已写入而接受前模拟中断。冷开时
只驱动原 composition 已安装的主动 owner 和 Action drain；从原 Proposal/receipt 授权，
零新模型调用。该 QQ fixture 只返回 provider acceptance；下一 due 泵把缺少终端回执的
两项 Action 如实结为 unknown。测试先断言该明确终端，再重复同 owner/Action drain，
完整 projection hash 不变、projection 等于 replay、无重复授权或发送。没有据此声称
完整 background 调度、Recall 或实时 soak 通过。

逐物理调用在同一 SQLite 使用账本准入与结算，已知用量不与父作者重复计数。第一审核已
返回 unclosed 后，第二作者显式抛 TimeoutError 或第二审核返回非法 verdict，仍保存先前
已完成作者和审核的公开审计；未知账单保持 `unknown` / `billing_unknown`。

这些证据不是供应商语义准确、真实 QQ、生产延迟或成本资格。特别地，provider 主动抛出
TimeoutError 不等于外层 Deliberation deadline 取消：本片只验证局部 facade 保持
CancelledError 传播；外层取消时此前已完成调用审计的终端持久化尚未 qualification。
主动 Recall 额外取得的材料不自动升级原 selected source table 的权限，尚无本片完整
Recall 资格证明。background 原 ModelCall meta 的 world/turn 归属并非本片新增或修复范围。

本片提交前离线定向门：11 个测试文件 **190 passed / 60.35s**，含本片 19 项公共 host、
篡改、冷恢复、调用隔离测试以及既有 inbound/主动生产链、receipt/source table 回归。
变更 Python 的 Ruff 与 `git diff --check` 通过。日志：
`/tmp/proactive-visible-source-targeted-final.log`。没有运行全套测试或真实供应商/QQ。
