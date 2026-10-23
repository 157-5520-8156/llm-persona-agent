# Claude 生活记忆与作者视图交接

承接 `claude/review-removal@54ea7ecf8db0da949ba851d0b13754bc3b106ef2`。
用户明确保留取消 Life 语义审核、宿主清理无效引用两项决定。
本次完成该分支已复现的兼容性、呈现和检索缺口，不重写命运机，不恢复定时任务或切换生产 QQ。

## 修复范围

- 旧 `.29` 快照继续使用原目的过滤 profile 与原预取呈现；新生活联想从 `.30` 生效。已保存的请求必须能按原输入验证，不能重造请求代替恢复。
- `.31` 精简视图对表格按列与行一起投影，保留空单元格位置；包括 Affect 引用表和稳定行/末行缓存形式。`.30` 已保存的精简视图仍按原字节重建。
- 对话 episode 同时遵守 45 分钟会话边界、8 行、16 个来源和最终 1024 字文本上限，不再先拼长文再截掉尾句。跨段的连续回复可以携带同一会话中最近一条真实用户话语作为上下文，引用闭包与双方归属仍完整；单条原始记录继续可主动检索。
- 自动联想在原检索器排序前筛选结构性环境记录和单气泡；查询保存 `automatic_attention=lived-moment-recall.1`，进入查询哈希、准备缓存和审计。旧查询省略该字段，主动 Recall 仍可找全部原材料。同步预取、异步本地回退与语义通道使用同一入口，不再取完 top-k 后二次删结果。
- 默认生产组装的 World Author 使用 `structural`，Character Life candidate 语义审核关闭，宿主机械引用处理开启，真实角色请求使用精简视图。显式注入/开启旧 reviewer 仍是独立选择，不能把旧验收脚本的 `WORLD_V2_LIFE_CANDIDATE_REVIEW_ENABLED=True` 带入这个新默认配置。

不按题材或措辞去重，不替角色决定提起记忆、制定计划、追问或保持沉默。隐私、时间、来源权限、CAS、effect-once 与原始失败记录保留。

## 验证与限制

最终 Python 源码冻结 `c602d8c8c734dbfe3021db9fbd8d4de8ad56dceb`，1,728 个文件与 Git archive 逐项一致。全部 11,376 个用例按文件分四组完整覆盖：**11,355 passed / 19 skipped / 2 xfailed**。来源清单、分组清单、各组原始日志及 SHA、最终选用结果见 `output/private-audits/claude-handoff-completion-20261023/full-final/qualification-result.json`。

- 373 项相关回归通过；清理多余参数后 22 项呈现回归通过；Ruff、Python 编译和 diff 检查通过。Standards 复查 0 个硬错误，1 个多余参数已移除；Spec 复查及基线增量复查均 0 项发现。
- 旧 `.29/.25` 原始反例现在直接验证通过；提供样本的 `.30` 原精简 wire 与 Claude HEAD 字节相等。
- 初次串行全量保留为 11,346 passed / 1 failed / 8 fixture errors。8 项是旧夹具通过自动联想索取环境记录，改为主动 Recall 后原正负断言全部保留；另 1 项为预期回放身份漂移。独立运行原 Claude 与修复版的全部 120 个冻结场景，所有业务行相同，仅一个 NPC 场景的 replay_hash 改变；据此升级 `.119` 基线，原漂移检查继续启用，见 `scenario-baseline-119-2026-10-23.json`。
- 最终分组执行中，私有启动脚本把测试目录放在仓库内，触发两项“必须使用系统临时目录”的检查。源码和测试未变，完整重跑该组后 2,825 passed / 19 skipped；其他三组结果直接保留。初次失败日志仍在 `full-final/result.json`，未将其改写为成功。

真实短链路使用新建隔离 SQLite 夹具、GLM-5.3-Flash low/json_object，世界初始雨事件和过去记忆由夹具提供，角色回应自主生成。两次真实调用首稿接受，没有纠错或语义审核调用；实际产生生活回应、Experience、Appraisal 和 Affect。旧雨天记忆进入实际请求并影响了角色联想，原 LifeSourceView 验证、逐事件重放、独立冷重开均通过。作者快照 7,102 字符，宿主完整视图 9,930 字符。两次 HTTP 等待约 4.120/6.848 秒，总过程 11.972 秒；供应商报告 16,671 tokens，未知/待计费 0，CNY 未知。

真实调用使用 `6fa6de0b`；其运行代码与最终源码的差异仅为两个离线场景基线常量，已通过文件差异及 AST 对照核实。完整请求、原始响应、计费记录和语义审读见同审计目录的 `live-chain/`、`live-input-proof.json`、`live-final-runtime-equivalence.json` 和 `SHORT-CHAIN-READOUT.md`。

**语义限制仍保留：** 模型在私有 summary 将来源的“高中”具体化成“高三”，另一条 meaning 把“放学”补成“下午”。没有形成新的客观 World Fact，但此样本不能算严格事实保真通过。未用重试、关键词纠正或恢复已取消的语义审核掩盖它。

之前的审查和反例保留在 `output/private-audits/claude-branch-review-20261022/`。本次未切换生产 QQ、主候选或原保留世界；`glm` 定时检查仍为 PAUSED。

本分支修复的是实际输入与记忆传递。即使短链路与离线回归通过，也不直接证明长期剧情丰富度、重大分叉、持续后果到命运/规划/课表的完整闭环，或真实 QQ 发布资格。
