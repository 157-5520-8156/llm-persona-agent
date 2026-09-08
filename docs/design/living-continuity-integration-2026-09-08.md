# 生活连续性隔离集成检查：2026-09-08

本次检查固定代码为 `a7b54fcb`，分支 `codex/living-continuity`。小屋与角色移动不在范围内；没有部署、真实 QQ 发送或生产数据库修改。

## 已集成的机制

- 每日空生活机会可以交给同一 CharacterInterior 选择一项自由生活意图或 `no_op`。Clock 和机会只授权一次考虑，系统不代写活动动机。已付费 terminal 在重启后优先恢复；没有 terminal 且已出现其他活动时，机会交回普通活动生命周期。
- 已接受活动的完整来源可以支持“正在进行”或“阶段结束”，不能据此宣称意图实现、具体地点或客观结果。旧来源选择中 Situation 遮挡同一 ActivityStarted 的问题仍待独立 composer 解决。
- 原 Capsule 已选 Fact/Dialogue 的完整 proof 可以在事实声明为空时独立准备。原聊天的缩略 value 不被冒充为完整 proof；原 pin、主体和隐私检查继续保留。这是审核准备接口，尚未安装聊天正文语义 guard。
- 离线 Fake 现在支持新活动工具和现有聊天工具载体。原无工具行为、真实 provider、健康门与 45 秒启动期限未改变。此前 daemon 失败是必需工具接口缺失引起的健康降级，首轮 scheduler 已完成，不是死锁。

## 集成证据

在项目 Python 环境中使用 `COMPANION_DISABLE_DEBUG_USAGE_LEDGER=1`、`PYTHONDONTWRITEBYTECODE=1`、`PYTHONPATH=src:tests/support:tests/world_v2`：

- 80 个文件的 pytest 综合门：**1588 passed，176.51 秒**，包括原 isolated daemon 验收、活动/来源/记忆/纠正/审计/恢复等相关测试。文件清单 `/tmp/girl-agent-integrated-a7b54fcb.files.json`；日志 `/tmp/girl-agent-integrated-a7b54fcb.log`。
- 正常 `scripts/verify_world_v2_scenarios.py` 全部 120 场景通过，未使用 `--limit`。输出 `output/adaptive-companionship-2026-09-08/baseline-a7b54fcb.json` 与 `14e63424` 的原 `.100` 基线逐字节一致；未更新预期结果。
- 场景文件 SHA-256：`080d3b84cf37a6c07cf54732cea10aa7d2d9ab721d4872a73af9a94a03e92e95`；manifest：`b405ce3beb2d6f4ab83b21011341fbe26192bbd24cb2dab3fb94979468c565c9`。
- 本片更改的 Python 文件 Ruff 及 `git diff --check` 通过。

这些是临时数据库和离线供应商链的机制证据。固定场景通过不证明新增自由生活的真实模型选择、长期多样性或月费目标。

## 真实模型证据与剩余工作

[trial09](visible-source-diagnostic-trial09.md) 已封存：8 个真实审核请求花费约 **0.0141918 元**，模型判定 7/8 符合预期；另一次错主体 verdict 被宿主结构检查拒绝。它没有经过角色纠正或实际发送链，不能报告为 8/8 语义通过。本次集成检查没有新增真实模型调用。

下一步先把原已选材料组合成可绑定的有序来源表，保留同一 canonical ref 对应的不同材料。完整候选审核需要在任何可见 Action 前结束；考虑采用显式完整候选模式并测量首句等待，不以先发送首句隐藏审核延迟。

恢复方面还需关闭一个独立窗口：`InboundTurnFaculty` 把 `output_ref/output_hash/proposal_hash` 写入 Core decision，但完整 ModelOutput 当前只在内存 `_outputs` 中。Core terminal 已持久化而 Proposal 尚未记录时，重启不能仅凭“通过审核”标记还原候选。后续持久载体必须保留原输出、调用审计、原 pin 和身份；不能自动重问角色、替换结果或给旧审计补授新资格。

完整 guard、持久审核 receipt 和这段输出恢复尚未实现。本 goal 仍在进行，已知正文漏报缺口仍未闭合。

## 后续来源准备集成：`c97de10d`

新 `compile_visible_source_table(request, capsule)` 直接从原受信 Capsule 的已选材料组合审核表，不接受 claim 列表或检索端口。它使用 `visible-source-row-table.1`，以 `(canonical source ref, exact projected material identity)` 分行；同一个 Started ref 的 Situation 与完整 Activity 均保留独立索引，只有符合活动局部合同的材料获得资格。完整有序表、中央去重材料与原 pin 各自参与 hash，供后续 receipt 绑定；旧 compact table 和 knownrefs 未改。

该表当前覆盖 Situation、所支持的活动/窄传记坐标、Fact、Dialogue，以及由原已选 Observation 证明的当前报告。它明确报告未支持的类型，包括 identity_source；不是所有来源的资格闭包。返回值只保存不可变 canonical JSON，导出的字典每次独立生成。

身份材料另外修复了原 hash 与投影字段不一致，已有三类身份 ref 保持。组合后的十文件门为 **324 passed，22.83 秒**，日志 `/tmp/girl-agent-composer-identity-integration.log`，Ruff 与 diff check 通过。这里没有新增真实模型调用，也没有启用聊天 guard。

## 完整作者载体：`c2d6b882`

已集成默认关闭的 `whole_candidate_mode`。它使用原 atomic decision/Recall，正常、同一 Core 的一次纠正以及 Recall 最终稿都保留完整 Beat；即使供应商支持 stream，纠正也不会切回增量头。兼容 head 入口返回完整稿，随后误请求 tail 明确失败且不再调用作者。最终 composition 仍需配套 `expression_episode_mode=off`，本片没有启用 guard。

完整 author、Deliberation、durable lineage 与新模式四文件门为 **235 passed，16.83 秒**，日志 `/tmp/girl-agent-whole-candidate-author-integration.log`。测试使用实际 Core 和 MockTransport，不是新增真实供应商试验。

同时记录两个尚待分别核实/修复的边界：

- 原无效作者调用经 Core 纠正后，World ModelResult 中缺少其用量记录；已验证获选稿和 Recall 控制转移的绑定，尚未据此证明或否定独立 provider 用量主账本完整性。
- 公开重启测试若通过 Clock 推进来令旧租约过期，也会改变 World revision。此时新判断可能是合法的 fresh decision，不能同时要求零新调用与旧稿被直接授权。该反例不构成“重复调用必然是 bug”的证据；后续将同 pin 的原终态恢复、原证据留存和新世界状态下的重判分开测试，不放松 CAS 或用规则压制角色回应。

## 完整调用记录、恢复与审核凭据：截至 `56f6e371`

首拒稿调用审计已由 `66c9fcc4` 合入。它保留完整原始 arguments 的 hash、实际调用
身份与用量，经同一 Core 的一次纠正后仍可审查；获选稿原用量语义保持单次调用，
独立 provider 主账本未改。四完整文件门 **231 passed / 20.36s**，日志
`/tmp/girl-agent-rejected-audit-integration.log`。这不补全通用异常或中途崩溃窗口。

新 inbound `.2` decision 保存完整原输出和三类原来被普通序列化排除的审计数组。
同 pin 的公开 adapter 消费口可从安装的原终态恢复，真实子进程会重新验证并恢复
原 Recall proof，追加 HTTP/检索为零；旧 `.1` 不升级。这仍不是普通冷入站的
自动审计恢复。第一次综合门 `11909fb5` 为 **1779 passed / 2 failed / 225.13s**：
两条活动第二回合的实际回归暴露了 prefetch 的合法 presentation-only 格式。
`56f6e371` 修正了该判断，并验证缺失/替换仍拒绝。合入后的七文件门
**198 passed / 85.21s**，日志 `/tmp/girl-agent-prefetch-recovery-integration.log`。

`11909fb5` 同时加入独立完整审核准备/凭据模块。全候选、有序来源表、alias、
Beat 映射、实际逻辑请求及完整返回都参与绑定；自洽收据还必须对照外部原审计。
合法语义拒绝与错调用/错主体/缺 Beat 的技术失败分别处理。专属 18 用例连同
composer/protocol 共 **41 passed / 9.88s**，日志
`/tmp/girl-agent-visible-receipt-integration.log`。固定 `.1` golden 防同版本漂移。
这仍未接入 ModelOutput、Acceptance 或发送链，不能宣称正文漏报已修复。

## `.101` 机械基线的依据

正常、无限制的 120 场景 CLI 在 `11909fb5` 如实拒绝旧 `.100` 校验值：
旧值 `b405ce3beb2d6f4ab83b21011341fbe26192bbd24cb2dab3fb94979468c565c9`，
实际 `.100` 标签候选为 `ac9948399ce55250262dbaac521c6a3c2b2e20a9c17fcd2f0bdc84983eb0fbe0`。
独立逐项比较确认 120/120 仅 `replay_hash` 改变，其他 manifest 字段逐字相同。
仅在诊断进程中恢复旧 Faculty `.1` 生产者，就能完整复现旧 manifest。该对照
不作为正式验收；正式代码仍保留完整记录和原全部场景断言。

`ordinary_share.01` 的前 8 个事件完全一致；第 9 个事件首先变化的是
`audit.character_interior_lineage.decision_hash`，随后为派生审计/接受/FK 身份。
原作者请求/响应/调用、Proposal hash 和 snapshot 均保持。因而分配新的
`world-v2-offline-mechanism-baseline.101`，预期 manifest hash 为
`96a7d21de8efe0cd3e257d481eaab6569d1a7af6c5188f8cb38b423efa4ea796`。
新版仍须正常 CLI 全量检查通过；不能将诊断中导出的候选算作通过。

证据保存在 `output/adaptive-companionship-2026-09-08/`：

- `11909fb5-manifest-diff.json`：`d0099afd6d8656b2bdf46f5c8ae899e90df2bd8cb32506261aa4f542dbbf6ec9`。
- `11909fb5-event-leaf-diff.json`：`c7fef9ce3bb7a5eeb8a4ba4adca468112edb21a2d9d2265b9804417cfa46d184`。
- `11909fb5-inbound-v1-causal-control.json`：`b48afc6e25a0368ad40cb470670d1c8ff412f8edccdbdf89703ba44909f70a29`。

本轮以上工作均为离线临时账本/MockTransport，无新增真实供应商支出；此前费用
封存记录保持。每月约 100 元、长期真人感以及已知正文漏报仍需后续实际链验证。

## `fa5906f4` 正式合并门与新增反例

相同 91 文件的完整合并回归为 **1784 passed / 214.27s**，日志
`/tmp/girl-agent-integrated-fa5906f4.log`，清单
`/tmp/girl-agent-integrated-fa5906f4.files.json`。正常 CLI 未带 `--limit` 的全部
120 场景通过，输出 `baseline-fa5906f4.json`，文件 SHA-256 为
`74c9a0b8d233c5a7c23805a838d31d4317a96b73d44419824b2adbf51daec06e`，
manifest 为上述 `.101` 的 `96a7d21d…a4ea796`。重新逐项比较仍只有 120 个
`replay_hash` 变化。此次更改文件 Ruff 和 diff 检查通过。

独立代码复核随后发现一个尚未由这些绿门覆盖的真实生产者组合：selective Recall
会将首轮 local prefetch 升级为后续 semantic prefetch，两次实际 presentation
因此可以不同。当前恢复器仍要求所有 presentation 等于最终 snapshot 的一份
prefetch，可能误拒该合法链。Core 原 Recall checkpoint 持有 initial_snapshot，
但最终 prepared `.1` 覆盖后只剩其 id/hash 和 presentation 列表，初始完整来源
已丢失。下一窄片将从原 Core 值保存显式新版 initial_snapshot 证据并分别核对，
不能把旧字典自洽当作重签依据。该反例还未关闭；Goal 保持进行中，绿门不等于
恢复完备或生产资格。
