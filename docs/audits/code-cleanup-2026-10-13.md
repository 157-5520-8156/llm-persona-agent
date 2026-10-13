# 代码清理与主链路收敛记录

本文件统一记录 2026-10-13 起的清理工作，取代分散、重复的清理报告；不是新的架构计划。
分支：`codex/role-memory-qualification`。目标是降低实际复杂度，保留功能、角色自主性、
来源/权限、CAS、effect-once、回执、重放和技术失败记录。行数含空行及注释，源码、测试、文档分别计数。
生产部署、真实 QQ 与长期故事质量不能由离线回归代替，本系列均未进行生产重启或新增远程模型调用。

## 已完成阶段与证据

| 阶段 | 基线 / 实现 | 改动与限制 | 最终回归 |
| --- | --- | --- | --- |
| 废弃实现清退 | `fa6c3700` / `f83b3ec2`、`989e0bbd` | 源码−2885行，测试−1264行。删除下述六个孤立实现及两个旧 PinnedTurn 方法；合并历史UTC-only哈希及面板origin解析。 | 10617 passed / 19 skipped / 2 xfailed；1704 Python SHA不变 |
| 检索与在用流程收敛 | `989e0bbd` / `37534a3b`、`f28f03d8`、`e818e506`、`f8de9b5b`、`4baab9de` | 源码−138，测试+147，文档+139，总计+148行。初始历史查询改读正文；预取接续归还Recall；两种Life审核共用重试循环；缓存响应跳过新检索/编码。 | 10629 passed / 19 skipped / 2 xfailed；1705 Python SHA不变 |
| 聊天主链路 | `4baab9de` / `2eccc9ba`、`f75e86cc` | 删除退役内层重试的请求准备、不可达成功分支和无效状态；有效纠正继续由Core负责。测试、文档一并收敛。 | 10629 passed / 19 skipped / 2 xfailed；1705 Python SHA不变 |

证据均在 `output/private-audits/`：
- `code-cleanup-20261013/`：完整 import/importer 图、六组AST等价、哈希固定向量、330+106项定向记录；
  `final-regression/result.json` 为终态，pytest1209.71s，exit0。
- `active-path-simplification-20261013/`：28组旧/新Life审核差分（包括请求、scope、失败/取消）逐项相同；
  精确历史坐标19475/20421/39896保留1259前史，固定Memory/World-Life文档1282份。
  查询1024字符元数据→463字符正文，可引用命中1→2；**不是独立相关性或故事质量证明**。
  `final-regression-v4/result.json` 为终态，pytest1215.97s，exit0；先前中断/失败均保留。
- `chat-mainline-consolidation-20261014/`：本轮基线、逐步回归、检查和冻结证据；
  `final-regression/result.json` 为终态，pytest1207.12s，exit0，源码HEAD `f75e86cc`。
三次已完成全库均有1条既有Starlette警告。源码冻结以各目录 `source-freeze.json` 为准。

旧清理报告的完整历史文字可从 `989e0bbd:docs/audits/code-cleanup-2026-10-13.md` 和
`4baab9de:docs/audits/active-path-simplification-2026-10-14.md` 恢复；下面保留处置清单及关键结果。

## 删除依据及保留责任

| 已删实现 | 依据 / 仍保留的功能 |
| --- | --- |
| `acceptance_compilers.py`（1624行） | 仅专属测试引用、生产安装主动拒绝；现行Fact-v2 sealed registry/plan/recorder及历史manifest codec保留 |
| `contextual_life_source_material.py`（447行） | 原作者已退役，仅专属测试引用；现行Life source view/readings/review及旧事件/重试/投影保留 |
| `relationship_deliberation_worker.py`（164行） | 无调用者；现行同一角色回合的关系信号结算/验收/trigger保留 |
| `read_only_tool_proposal_compiler.py`（223行）、`read_only_tool_query_reader.py`（81行）、`read_only_tool_authorization_resolver.py`（87行） | 退役独立工具作者的孤立链；显式注入工具的acceptance、最终授权、executor及回执保留 |
| `PinnedTurnCompiler.audit_relationship_source` / `audit_appraisal_accepted` | 只为旧worker提供额外作者调用，不是历史codec；CharacterInterior的关系/Affect决定保留 |

两个删除测试文件只覆盖对应旧实现；现行能力测试保留。`mechanism_closure.yaml` 的过期consumer改为
实际 `ReadOnlyToolAcceptanceRuntime`。四份旧哈希canonicalizer合并时，特意保留UTC-only与modern
两种真实契约；固定向量 `528a131bc8d9de471278c3cdac2516f685a2ebecb86e900f684cbdb24ace2474` 未变。

## 本轮聊天主链路

- `complete_bounded_validation_reselection` 已长期固定拒绝；wire还会在调用它之前直接抛出
  typed failure，但旧请求/schema准备和“成功后”处理仍留在主函数里。删除这些不可达部分，
  显式保留流取消、原错误/原文哈希/调用身份、来源审核及其费用、deadline相关失败分类。
- paired作者的旧内部纠正只会返回None或抛错，不能成功。相应成功分支、无生产者标记、
  多余回忆纠正状态、无效结果类型及纯重试载体删除。失败缓存只保存错误原文和准确pin身份；
  换pin后由当前Context重新生成的路径保留，不把旧失败当新时点的角色决定。
- `correct_role_result`、Core一次同角色纠正、最终来源复核、拒绝输出的完整调用审计均保留。
  禁用的旧内层重试与当前有效的Core纠正是不同路径，不能混删。
- 旧测试构造了从不执行的模拟响应。合并为plain/forced/metered三种零请求断言；原两种可选角色
  字段情形改为直接执行schema验证和unwrap断言，测试数量保持，字段覆盖从不可达变成实际执行。
  这也暴露旧样本漏掉life_intent字段，现显式给出null，未放宽生产schema。
- 本轮456项基线与清理后定向回归一致通过；第一次精简误改变typed error分类、一次机械清理
  留下旧局部变量引用，均被定向用例发现并修复，日志保留。新字段测试63项通过。
- 扩展检查643项通过；9组旧/新真实入口比较的输出、异常详情、请求及审计相同，见
  `entrypoint-equivalence.json`，范围只限这些固定向量。Ruff/diff check通过。
- 独立Standards审查发现一组传输测试引用已删私有helper；迁移至保留的final入口与canonical
  expression schema，四项参数化用例及原选择/身份断言保留，相关38项通过，复审关闭。
  Spec审查无阻塞。冻结全库结果见上表；用例数量与基线一致。
- 结构变化：author→wire导入符号30→18；wire主处理1016→677行；同pin失败续接143→21行。
  这些是删除不可能成功的流程和无生产者状态后的结果，未按行数拆文件。
- 没有新增生产Module、注册器、备用角色或本地角色话术。
- 相对`4baab9de`的最终净变化：源码−1538行，测试−161行，文档−154行；总计−1853行。

## 初始候选盘点

`fa6c3700` 的718个源码Module中，662从所选入口import可达，29另由操作/评测脚本可达，
23仅测试可达，4无上述Python根可达。首批删除后662入口集合不变。此为含包初始化器的保守文件级
图，另查动态import、配置字符串和序列化责任；导入可达不等于运行，更不自动等于能删。

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

## 仍待处理

`production_turn_application` 的装配/health职责、其余在用聊天与Life实现仍需逐条收敛。
`CompanionStore` 仍有预算和图片记账消费者；历史权重、旧schema/reducer和reply-later回放保留。
仅测试可达的实验实现要逐项判断；Fact-v2 candidate测试同时覆盖现行防伪造，不能整文件删除。
历史文档“sealed_production_fact_registry_v2无引用”的判断已过期，现行Fact验收确实依赖它。
