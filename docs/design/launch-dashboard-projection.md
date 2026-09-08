# 发布演示的只读生活投影

本片沿用 `world-v2-dashboard-home.1` 与现有 sections / highlights / values DTO。
它只补 owner 已获准读取的来源明确的展示信息，不接 UI、HTTP、鉴权、模型或生活调度。

## 原意图与生命周期

`overview_life.highlights[kind=plan]` 保留原状态与最后变更时间。对于有完整证明的
chat / world / day-open 自定意图，标题使用原自由文本摘要，不通过活动目录重新分类。
附加 values：

| key | 来源与含义 |
| --- | --- |
| intention | 原角色决定的完整文本，原合同最多 480 字符 |
| intent_source | `chat` / `world` / `day_open`；显示标签来自封闭映射 |
| execution_scope | 原自定活动的 `self_directed` 能力范围 |
| selected_at | 原选择时间 |
| scheduled_start / scheduled_end | 已接受 Plan 的计划窗口，不是实际开始或完成时间 |

`dashboard_life_intention.py` 只读 captured projection 及其前缀中的不可变事件。它验证
当前 Plan authority、原 ActivityPlanned、原 Proposal 字节哈希，复用各来源既有 derive /
validate 函数重验角色审计与来源，并检查生命周期没有更改原意图坐标。它不重新 `project()`、
不取当前 head 补材料、不扫描相邻事件。新增原文读取仅用于至多三个近期 Plan 展示候选。
证明缺失时保留原生命周期元数据，以上意图字段缺席；withhold 时连标题、状态和值一起隐藏。

## 经历、记忆和私态的边界

- WorldOccurrence 只展示已记录状态与元数据。`world_environment_status` 为 `not_read`
  （已结算但本接口未读正文）或 `not_settled`。提交、激活与结算不混写为“刚发生过”。
- Experience 的 `source_kind` 来自其精确 typed source binding。只有 `.2` 的同一复合源
  能给出 `character_response_status=recorded_private` 或 `explicit_none`；没有从附近
  Appraisal、活动或对话拼接回应。环境正文仍为 `not_read`，角色回应正文不展示。
- MemoryCandidate 的原状态分别标为待复核、已保留、未保留、已遗忘。附加
  `retention_rationales`（封闭枚举的中文标签）、`source_kind`（多个类型以逗号分隔）、
  `review_due_at` / `reviewed_at` / `forgotten_at`。它不宣称候选已成为事实，也不读取
  summary_ref 对应正文。withhold 不展示这些值。
- 既有 Appraisal / Affect 是已接受的安全投影摘要，不是最新完整角色私态或新增自述。
  PrivateImpression 恢复 ADR-0018 要求的 count-only，删除基线曾输出的 reflection_summary。
  关系、线程和承诺仍沿用原 DTO。

内部 refs、哈希、audit/proposal JSON、prompt、provider 材料均不加入显示 DTO。完整原意图
只是允许的字段读取；它不赋予位置、其他参与者、已执行结果或因果链的显示权限。
本片没有环境正文 content-store 端口，没有宣称完整生活链已可视化，也不涉及小屋或移动。

## 验证与录制样本

`test_launch_dashboard_projection.py` 通过 `DashboardHomeSnapshotModule.capture()` 验证：
临时 SQLite 的原角色审计 / Proposal → DayOpen 接受 → 冷开，以及 World 事件自主回应 →
真实 ActivityLifecycleWorker 的开始 / 完成；读取前后账本相等。缺原事件、缺模型审计、
错误 Proposal hash、越过原 pin 和 withhold 均不会输出意图。
另有实际 Fact → Memory Open / Accept、Experience `.2` null / text 的公开生产者验证。
Experience 的共享 app fixture 仅安装 `httpx.MockTransport`，没有真实模型或 QQ 调用。

活动测试在自身临时目录写入 `owner-active.json` / `owner-completed.json`，均为冻结 owner DTO。
这些是**离线角色替身、实际生产者与 reducer 链**的丰富样本，不是生产数据或真实语义验收。
它们与真实稀疏样本应分别标注。测试通过不代表生产部署、真实模型或录制效果已验收。

本片后端门：新 launch projection、既有 home snapshot、owner HTTP adapter、QQ owner
endpoint、dashboard projection / public adapter、runtime observation 共 7 文件，
87 passed（1 条现有 Starlette/httpx 弃用警告）。Ruff 与 `git diff --check` 通过。
