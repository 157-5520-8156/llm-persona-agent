# 代码清理盘点与第一批结果（2026-10-13）

基线：`fa6c3700`；工作目录：`codex/role-memory-qualification`。
用户要求：先盘清楚，确定废弃的删除，重复的合并；保留现有功能和角色自主性，不因重构增加新层。
这是本次清理的证据记录，不是第三份并列架构计划。

## 盘点方法与适用范围

- 扫描基线全部受 Git 管理的 Python 文件，构建 AST import 图，包含相对 import 和包 `__init__`。
- 根为 `pyproject.toml` 声明的 app/cli/napcat/test-economy/formal-evaluation 入口及 QQ host composition。
  OneBot 入口与 NapCat 共用同一 Python Module。
- 另计 `scripts/`、`configs/` 和测试的引用闭包。检查动态 import、配置字符串和公开类名引用。
- 这是保守的文件级候选筛选，不是完整函数调用图：导入不等于执行，测试引用不等于生产功能，
  没有入口引用也不自动证明可删。对实际删除项再核实职责、退役契约、替代者和序列化用途。
- 源码行数包含注释和空行；不拿测试/脚本行数当生产代码量。
- 一次性盘点程序、全量逐 Module import/importer 清单、前后计数、原始验证日志保存在
  `output/private-audits/code-cleanup-20261013/`。没有把盘点程序做成新的生产框架。

| 基线分类 | 文件数 | Python 行数 |
| --- | ---: | ---: |
| src | 718 | 387302 |
| tests | 798 | 347062 |
| scripts | 184 | 76425 |
| prototypes | 6 | 774 |

718 个源码 Module 中：662 个从所选入口经 import 可达，29 个另由操作/评测脚本可达，
23 个仅由测试可达，4 个不在任何上述 Python 根的闭包中。清除六个文件后，入口可达的
**662 个 Module 集合保持相同**；操作/评测脚本的源码依赖也未减少。仍不能由此宣称
每个分支都已证明等价；行为证据见验证项。

## 已核实并删除的实现

| 删除项 | 删除依据 | 保留的有效功能 |
| --- | --- | --- |
| `acceptance_compilers.py`（1624 行） | 只有自身测试导入；生产 registration 主动抛 `unsupported_key`，没有安装者；process-local handle 明确不可序列化 | 现行 Fact-v2 sealed registry、plan issuer、Manifest-v3 builder/atomic recorder、各领域 acceptance runtime 和历史 manifest codec |
| `contextual_life_source_material.py`（447 行） | 原 contextual life inspiration author 已按 ADR 0016 退役；只有专属测试导入此编译器，没有当前 writer/reader 或持久化类型引用 | 当前 CharacterInterior Life source view/readings/review、ContextualLife 历史事件/重试/投影继续保留 |
| `relationship_deliberation_worker.py`（164 行） | 没有源码/脚本/测试消费者；旧独立关系作者 worker 已由同一角色回合生成信号后结算的路径取代 | `character_interior/inbound_relationship.py` 和 `world_stimulus.py` 的同作者信号结算、relationship compiler/acceptance/trigger runtime |
| `read_only_tool_proposal_compiler.py`（223 行） | 原 independent tool-author trigger 已确定退役；唯一源码消费者是同批删除的孤立 query reader | `ReadOnlyToolAcceptanceRuntime`、授权校验、工具 executor 和 Action/receipt/replay |
| `read_only_tool_query_reader.py`（81 行） | 没有消费者；只负责从已退役作者的 proposal-audit 模式读取 query | 注入式 `ToolQueryReader` Interface 和现行工具验收链 |
| `read_only_tool_authorization_resolver.py`（87 行） | 没有消费者；只为旧 proposal compiler 准备授权绑定 | 当前最终 dispatch 授权复核，撤回授权后禁止调用的测试 |
| `PinnedTurnCompiler.audit_relationship_source` / `audit_appraisal_accepted` | 前者仅供旧 relationship worker；后者仅供前者。全仓源码、脚本、测试无其他引用；均是再次调用独立作者的方法，不是 replay codec | 当前 inbound 和 world-stimulus 仍由 CharacterInterior 作者决定关系/Affect，再进入原验收流程 |

删除两个专属测试文件：`test_acceptance_compilers.py`、`test_contextual_life_source_material.py`。
它们只验证被删除的旧实现，没有其他测试/脚本导入其中的 fixture。未删现行 Fact-v2 recorder、
同角色关系、来源权限、历史回放或只读工具资格测试。并将 `mechanism_closure.yaml` 中过期的
`ReadOnlyToolProposalCompiler` consumer 改为实际受测试的 `ReadOnlyToolAcceptanceRuntime`，
去掉把旧 claimed trigger 当当前必需来源的描述。

## 合并重复实现

1. Fact、Memory、Experience、Commitment 的四份 UTC-only JSON canonicalizer，收拢到已有
   `schema_core.py` 的一个纯函数。没有增加类、注册器或新 Module。
   **不能直接换成已有的 modern canonicalizer**：后者会额外归一化 `+08:00` 字符串，改变历史 hash。
   本次保留两种真实存在的 codec 契约，固定基线向量四路哈希均为
   `528a131bc8d9de471278c3cdac2516f685a2ebecb86e900f684cbdb24ace2474`。
2. app 与 OneBot 面板完全相同的 origin parser 收拢到它们已共用的 Dashboard session Module。
   两个 HTTP 入口各自的拒绝响应格式、loopback 校验和会话验证保持原实现。

## 无入口引用的全部候选及处置

下面按基线大小列出全部 56 个候选，避免仅凭大文件或测试引用批量删除。最后一类需要进一步
判断实验是否仍有价值、测试是否同时覆盖现行能力，以及有没有需保留的外部使用约定。

| Module（相对 `src/companion_daemon/`） | 行数 | 本次处置 |
| --- | ---: | --- |
| `world_v2/private_self_expression_audit.py` | 1751 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/scenario_runner.py` | 1662 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/acceptance_compilers.py` | 1624 | 删除；详见下表的替代/退役证据 |
| `world_v2/ledger_maintenance.py` | 1507 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/character_interior_architecture_guard.py` | 1437 | 保留：架构约束或显式注入工具能力 |
| `world_v2/fixture_acceptance_manifest.py` | 1199 | 保留：操作、验收、导入或离线评测入口可达 |
| `room_compiler.py` | 968 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/longitudinal_journey.py` | 856 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/prehistory_life_materials_runner.py` | 837 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/platform_architecture_guard.py` | 606 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/longitudinal_model_input_capture.py` | 503 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/isolated_daemon_acceptance.py` | 482 | 保留：操作、验收、导入或离线评测入口可达 |
| `mechanism_catalog.py` | 475 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/longitudinal_review.py` | 452 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/contextual_life_source_material.py` | 447 | 删除；详见下表的替代/退役证据 |
| `delayed_trigger_catalog.py` | 422 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/adult_media_authority_provisioning.py` | 400 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/fact_v2_candidate_manifest.py` | 352 | 保留待复核：仅测试可达不足以证明废弃 |
| `world_v2/story_import_runner.py` | 344 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/expression_decision_channel.py` | 343 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/public_information_authority_provisioning.py` | 338 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/story_import_budget.py` | 275 | 保留：操作、验收、导入或离线评测入口可达 |
| `tile_room_compiler.py` | 268 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/relationship_evaluation_draft.py` | 262 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/optional_field_usage_specimen.py` | 256 | 保留待复核：仅测试可达不足以证明废弃 |
| `world_v2/visible_meaning_fidelity.py` | 249 | 保留待复核：仅测试可达不足以证明废弃 |
| `world_v2/authorization_shadow.py` | 239 | 保留待复核：仅测试可达不足以证明废弃 |
| `world_v2/read_only_tool_proposal_compiler.py` | 223 | 删除；详见下表的替代/退役证据 |
| `world_v2/display_architecture_guard.py` | 202 | 保留：架构约束或显式注入工具能力 |
| `world_v2/longitudinal_fixture_model.py` | 201 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/life_author_weight_policy.py` | 199 | 保留：历史权重解释或旧事件回放夹具 |
| `world_v2/memory_representation_draft.py` | 197 | 保留待复核：仅测试可达不足以证明废弃 |
| `world_v2/longitudinal_demo.py` | 191 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/conversation_audit_acceptance.py` | 170 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/context_slice_retention.py` | 166 | 保留待复核：仅测试可达不足以证明废弃 |
| `world_v2/relationship_deliberation_worker.py` | 164 | 删除；详见下表的替代/退役证据 |
| `world_v2/native_story_runner.py` | 161 | 保留待复核：仅测试可达不足以证明废弃 |
| `world_v2/read_only_tool_executor.py` | 161 | 保留：架构约束或显式注入工具能力 |
| `world_v2/mechanical_evaluation_export.py` | 133 | 保留待复核：仅测试可达不足以证明废弃 |
| `world_v2/longitudinal_stdio.py` | 118 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/story_semantic_probe.py` | 110 | 保留待复核：仅测试可达不足以证明废弃 |
| `world_v2/epoch_genesis.py` | 103 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/longitudinal_checkpoint.py` | 98 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/read_only_tool_authorization_resolver.py` | 87 | 删除；详见下表的替代/退役证据 |
| `world_v2/read_only_tool_query_reader.py` | 81 | 删除；详见下表的替代/退役证据 |
| `world_v2/future_life_weight_policy.py` | 78 | 保留：历史权重解释或旧事件回放夹具 |
| `world_v2/npc_initiative_weight_policy.py` | 78 | 保留：历史权重解释或旧事件回放夹具 |
| `world_v2/visible_dialogue_reading_context.py` | 75 | 保留待复核：仅测试可达不足以证明废弃 |
| `world_v2/story_source_plan.py` | 73 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/character_interior/inbound_context_window.py` | 70 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/media_funnel_evaluator.py` | 68 | 保留待复核：仅测试可达不足以证明废弃 |
| `world_v2/read_only_tool_trigger.py` | 66 | 保留：历史权重解释或旧事件回放夹具 |
| `world_v2/native_story_catalogue.py` | 63 | 保留：操作、验收、导入或离线评测入口可达 |
| `world_v2/aspiration_seed_policy.py` | 46 | 保留：历史权重解释或旧事件回放夹具 |
| `world_v2/social_action_draft.py` | 46 | 保留待复核：仅测试可达不足以证明废弃 |
| `world_v2/legacy_deferred_reply_migration.py` | 25 | 保留：历史权重解释或旧事件回放夹具 |

## 仍需深入的维护热点

- `reducers.py` 16510 行、`character_interior/inbound_wire.py` 11614 行、
  `life_development_runtime.py` 8563 行：都在当前入口依赖链上，不能当死代码删除。
  下一步应在各自内部查找重复的契约/状态转换和仅历史使用的分支；不机械按行数切文件。
- `db.py` 的旧 `CompanionStore` 仍由预算、图片默认记账和脚本使用，不能整类删除。
  必须先将实际需要的记账消费者收敛到已存在的窄账本，并证明预留/未知费用/重启语义一致。
- `fact_v2_candidate_manifest.py` 虽只有测试导入，但同一个测试文件还覆盖现行生产 plan/recorder
  的防伪造路径，不能将整个测试文件直接删除。
- 只含试验用途的 memory representation、meaning fidelity、context retention 等保留待复核。
  “尚未接入”与“确定废弃”不等同。
- 历史 weight policy、legacy reply-later codec、旧 schema/reducer 保留；它们负责解释历史，
  不重新引入角色作者或宿主行为脚本。
- 历史文档中关于 `sealed_production_fact_registry_v2` 无引用的判断已过期：当前 Fact-v2
  acceptance runtime/production plan 明确使用它，不能照旧表删除。

## 验证记录

- 第一批定向检查：330 passed，1 条既有 Starlette warning，32.40s。
- `verify_mechanism_catalog.py`：schema=2，26 mechanisms，通过。
- 合并前从四个原始 hash 函数生成固定向量；新增测试验证历史 hash、忽略 authority 包装字段、
  实质数据变化必须改变 hash，以及 UTC-only/modern offset codec 不得混用。
- 关系补充测试、独立 Standards/Spec 审查和最终冻结全量回归：待完成后更新。
- 无新增外部模型请求；未改运行配置、重启生产或调用 QQ。测试证据不代表生产 soak 资格。
