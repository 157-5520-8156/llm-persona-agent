# 连续性与调用成本迭代记录（2026-09-07）

本记录属于[唯一施工计划](../design/harness-restructure-execution-plan.md)，不是新的路线图。
基线 `21d0177d`，集成分支 `codex/living-continuity`；承接上一批
[连续性生活修复](living-continuity-2026-09-07.md)。目标是让已经付费的选择留下后果，同时让约 ¥100/月
的设计目标有可信的账目基础。所有验证使用本地 fixtures、MockTransport、临时 SQLite；没有真实 API
调用、生图、QQ 消息、生产库修改、服务重启或推送。

## 这批改了什么

| 断点 | 改动 | 验证范围 |
| --- | --- | --- |
| 原聊天已完成，paid keep 保存失败后没人再收尾 | 直接以原付费 DecisionProposal 为 durable outbox；生产后台先恢复已付费选择，再检查独立反思预算 | 安装的 SQLite application、重启、关闭反思农场、原角色调用仅一次 |
| 审计/typed 提案/接受分次写，时钟一推进就无法完成 | 新 paid 留存用精确限定的五事件原子批次；原作者、原解释、来源和接受链不变 | 保存切点、提交成功但回执丢失、并发执行者；反例拒绝伪造 lineage/来源/额外 effect |
| 重启扫描与空闲 opener 反复解析历史 | 新审计增量扫描；仅对实际 eligible appraisal 查 paid 作者，技术失败索引只读新增后缀 | 无候选时的读取陷阱、较旧候选、增量索引、持久退避 |
| 先查余额再另一次 INSERT，多个调用一起越界 | SQLite `BEGIN IMMEDIATE` 内完成共享查询与 reservation | 原码 8 个并发 store 全部通过同一余额；修复后仅一个获准 |
| 先释放预留再写账，落账失败造成凭空余额 | 用量写入与预留状态一次事务；同 reservation 的最终账单幂等，冲突拒绝 | SQL trigger 注入失败、重启恢复、重复与冲突结算 |
| 超时/缺 usage 统一记零费用 | 保留 known/unknown/not_billed；已有真实 token 在无效响应、取消、异常后仍保留 | 流/非流、确定未发出、同 scope 不同请求、收到用量后的故障 |
| 生图、识图绕过文本预算 | OpenAI 生图与 QQ vision 工厂注入同一 Settings 的共享 usage store，HTTP 前预留 | 文本与图像互相占额，拒绝时 0 HTTP，成功重放 0 新调用，未知账单跨重启保留 |
| 图片估价被当成最终账单 | 缺/坏 usage 的 HTTP 200 保留 provisional row 与未知占用；补录真实账单更新同一行 | 估价→真实费用、重复补录、图片计数保持、明确鉴权拒绝释放 |
| 远程 embedding 启动预热在计费包装之前 | QQ 预热移到已安装的持久 cache/budget 之后，其他构建默认不预热 | 首次调用前已有预算记录，子预算拒绝时 0 HTTP |
| health 只看文本已结算，月底才发现超支 | 月/日费用包括图片与 embedding 日桶；在途/未知金额分列，按用途归因并接月末预测 | 图片镜像不重复计价、预算拒绝不算模型调用、实际观测不足 24h 不预测 |
| 文档已关闭的价格错峰仍藏在费用 gate | 移除按 DeepSeek 高峰价延迟 life/private/NPC 机会的分支 | 不同 UTC 小时的同一合法机会可准入，额度约束仍有效 |

## 成本指标如何解读

`WORLD_V2_MONTHLY_COST_TARGET_CNY` 默认 100，是预测目标。现有 `MONTHLY_BUDGET_CNY`（默认 80）、
`DAILY_BUDGET_CNY`（3）、`SOFT_DAILY_BUDGET_CNY`（2）仍是原来的部署限额；没有修改运行中的环境配置。
可见入站绕过 soft gate，显式 hard gate 仍有效。目标与限额不混用，费用拒绝不得解释为角色沉默。

health 的 `cost_forecast` 按本月真正观测过的时段外推，首次安装前的日子不当作零花费；至少观测 24h
才外推。在途和未知费用只加一次，不乘整月。70/80/90/100% 压力是观测信号，不替角色决定表达，
本批没有安装新的自动降载规则。`monthly_purpose_cost_cny` 是已记录费用的用途归因，未知占用单独列出。

价格使用仓库现有版本表；这是按用量估计的 CNY，不是供应商发票。QQ vision 的预留采用单图 token
上界加文本、输出余量，不把 base64 字符当文本 token（[阿里云视觉文档](https://www.alibabacloud.com/help/zh/model-studio/vision)）。
本批没有更换角色模型、删除来源背景、恢复独立语义审查模型或增加模型调用目的。

## 证据与边界

- 成本底层新增回归先复现 6 个失败，包括并发、部分落账、未知用量和跨月丢预留，再修复。
- LLM 用量证据回归分别复现缺用量/错误内容及未发出失败；修复后的 LLM 组合 93 项通过。
- 生图独立审查提出两个账单边界问题，按真实未计费拒绝及 provisional 结算修复，未将估值固化为最终账单。
- 完整集成验证结果在本批全部检查完成后记录于施工计划。

尚未证明或尚未闭合：

1. 约 ¥100/月是设计约束，尚未用这版代码的跨日真实 workload 验收。模型/图片价格表也不是新获取的实时账单。
2. 未知账单有持久占用与同 reservation 对账入口，尚无自动拉取供应商账单的 worker；持续未知会占容量并在 health 告警。
3. Civitai 的原币 buzz 没有可靠人民币换算依据，显示未定价费用告警，不说免费。
4. embedding 子账已进入总额观测，子调用仍按自己的已安装预算预留；不同调试/资格化数据库没有跨库账户总账。
5. 历史 paid 半写入若已跨 World/时钟前缀，仍记录技术拒绝，不擅自迁移其语义。新 paid 写入的原子批次消除了该中间状态。
6. NPC 独立通信、无发送的私有再访和角色主动放下等待等产品能力仍是上一批列明的余项；本批没有用规则伪造这些行为。

## 最终检查

- 主体 `666b15a4`：`scripts/test_fast.py --tier full` **6221 passed / 19 skipped / 0 failed**，433.81s。
- 收尾 `de0fb6df` 的预热异常清理：启动/embedding **33 passed**；该小修后未重复整库。
- 独立审查两处问题已由原审查者用临时库复核关闭；没有真实 API 调用。
- 24 个改动 Python 文件 Ruff 通过；diff whitespace 检查通过。
- 冻结 `.93` 120 场景通过，原指纹
  `92ec85bce2396318298ac53bdd5cd19b72a6ddd04e388cbef69358a1471353c3` 保持不变。
- 机制目录、平台架构检查通过；formal fixture 产物结构通过，评估仍 `synthetic / blocked`。
- 并行的五个临时 checkout 已删除，提交分支保留。原项目目录的 `da8aae88` 和用户未跟踪文件未改。
