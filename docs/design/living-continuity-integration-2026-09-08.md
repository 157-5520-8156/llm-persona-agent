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
