# Girl-Agent 代码库索引

本地优先的"赛博伴侣"项目：LLM 驱动的虚拟角色（沈知栀 Celia Shen）住在事件溯源的 World V2 里，通过 QQ 聊天与用户互动。设计宗旨见 `AGENTS.md`（受控高随机：模型有行为决定权，确定性代码只守硬边界）；领域词汇表见 `CONTEXT.md`（World Event / Projection / Pinned Turn / Inner Life Snapshot 等全部术语）。当前业务和架构意图只有 `docs/design/girl-agent-design-intent.md`，实施顺序只有 `docs/design/harness-restructure-execution-plan.md`（2026-08-13 起取代 `root-causes-and-long-coupling-luna-plan.md`，后者降级为 L0–L3 期历史施工记录）。改 World V2 前先完整阅读这两份权威文档、`CONTEXT.md` 和 ADR 0010；其他设计文档仅作为历史证据，发生冲突时不得覆盖这两份权威文档。

## 架构全景：两个世界

- **旧 daemon 层**（`src/companion_daemon/` 顶层模块）：FastAPI、适配器、情绪状态机、旧"图片机"（`event_media.py` 6397 行）、预算控制。多数顶层模块已被 World V2 取代，仅适配器/预算/LLM 客户端仍活跃。
- **World V2**（`src/companion_daemon/world_v2/`，460 模块）：事件溯源核心。Append-only 账本（`sqlite_ledger.py`）+ 确定性投影 + 接受链 + CAS + replay。模型是**提议者**：`Model Result` 带哈希，replay 只重放不重调模型。运输层确定性修补在 `json_wire_repair.py`；钉源短标识还原在 `pinned_source_ref.py`。

## 入口与命令（pyproject.toml）

| 命令 | 文件 | 用途 |
|---|---|---|
| `companion-daemon` | `app.py` | FastAPI 服务（端口 8765） |
| `companion-sim` | `cli.py` | World V2 垂直模拟（`--fake` 不调 API，需 DEEPSEEK_API_KEY） |
| `companion-napcat` | `napcat_cli.py` | **生产路线**：QQ 小号 NapCat (OneBot v11) |
| `companion-onebot` | `napcat_cli.py` | 通用 OneBot 路线 |
| `companion-world-v2-test-economy` / `-formal-eval` | world_v2 下 CLI | 离线评估/夹具 |

常用脚本：`run_napcat_adapter.sh`（生产）、`run_daemon.sh`、`interactive_chat.py`（REPL）、`chat_with_world_v2.py`（走生产 host 但拦截发送）、`run_isolated_daemon_acceptance.py`（双进程真实链路验收）。`run_qq_ws.sh` 引用的 `companion-qq-ws` 入口已从 pyproject 移除，疑似过期。`scripts/` 里的 `celia_v3` / `.cmd` / `.ps1` / `train_celia_*` 是**角色身份 LoRA（她的脸）训练流水线**，产物是仓库根目录未跟踪的 `Celia.safetensors`（Krea2 DiT，rank 32，6500 步）。2026-08-18 成人车道恢复直接依赖这份权重：Civitai 旧 AIR 404 后 Private+Published 重传，模板第 28 行现为 `urn:air:krea2:lora:civitai:2868686@3240992`。不要当无关脚本删掉或忽略。`scripts/inspect_local_celia_loras.py` / `scripts/upload_celia_krea2_lora.py` 是同一条资产链上的只读盘点与上传。

## HTTP 路由（app.py）

- `POST /messages` — 聊天入口（幂等 message_id，World V2 冷启动返回 503 可重试）
- `POST /internal/world-v2/tick` — 调度器时钟推进（需 operator token）
- `POST /internal/world-v2/drain` — Action/后台恢复
- `GET /world-v2/room` / `/world-v2/dashboard` — 只读投影 DTO（`/world-v2/life-state` 已按 ADR-0007 移除）
- `GET /dashboard` — 仪表盘 HTML；`POST /world-v2/dashboard/session` / `POST /world-v2/dashboard/logout` / `GET /world-v2/dashboard/home` / `GET /world-v2/dashboard/app.js` — 仪表盘会话与静态资源
- `GET /internal/world-v2/dashboard-room` — 仪表盘内部房间投影
- `/health` — capture 就绪 + Character Interior 健康检查（fail-closed）

## 消息管线（核心流程）

QQ → `qq_c2c_onebot_app.py` → `qq_c2c_host.py` → `platform_host.py` → `world_turn_runtime.py`（`WorldTurnRuntime.respond`，转 Observation）→ `world_v2/runtime.py` `WorldRuntime.ingest`：

1. 提交 ObservationRecorded（触发 TriggerProcess，CAS）
2. `pinned_turn.py` `PinnedTurnCompiler` 钉 cursor + 编译 Context Capsule
3. 模型调用：`character_interior/inbound_turn.py` → `core.py` `CharacterInterior.consider`（8-facet Inner Life Snapshot 是唯一上下文）→ `inbound_wire.py` 结构化表达
4. 审计提交（ProposalRecorded/ModelResult）
5. 接受链：`unified_inbound_decision` 闭式检查 → acceptance recorder `prepare_batch` → `AcceptedLedgerBatchIssuer` 签 digest → `ledger.commit_accepted`（CAS）
6. Action：`action_pump.py` claim → `platform_action_executor.py` 发送
7. 回执：`runtime.settle` → `settlement.py` `SettlementPlanner`

**表达**：生产走 `ExpressionDraft.beats` 多 beat（`timing_choice` now/later/silent、`cadence`、`turn_posture`、`delay_seconds` 控制节奏与已读不回；`max_beats` 默认 8）。生产分流是她选的 `result_kind`（`reply_only` / `full_turn` / `recall`），不是宿主按话题切。`inbound_wire.py` 的 `_is_lossless_minimal_reply_draft` **只用于 quick recovery，不是生产分流路径**（2026-08-13 核实）。`expression_episode.py` 的旧双作者协调器已删，只留验证函数。**2026-08-19**：`reply_only` 在 `timing=now` 时可直接带 `photo` / `media_request`（+ 可选 `media_source_refs`）；`later`/`silent` 仍可见拒绝；非法 ref 仍拒。守门：`expression_decision_channel.assert_expression_decision_channel_coverage`（测试门，防「广告有、通道关」）。

## 子系统地图

### Character Interior（角色内心，`world_v2/character_interior/`）
- `contracts.py` — 全部公开契约（InnerLifeSnapshot/InnerTransition/InnerDecision）
- `core.py`（118KB）— `CharacterInterior` 深模块，仅 `project`/`experience`/`consider` 三入口
- `inbound_author.py`（218KB）/ `inbound_wire.py`（521KB）/ `structured_role.py` — 模型输出物化 + 表达校验
- `snapshot_compiler.py` — 从 Capsule 确定性编译 8-facet 快照
- `world_stimulus.py`（123KB）— 已提交世界事件 → 内心刺激 → `experience()` → InnerTransition
- `production.py` — 生产组装 + 后台驱动（proactive/private impression/silence/reconsideration）。`drain_private_impression_once` 在 `WORLD_V2_PRIVATE_IMPRESSION_DAILY_MODEL_CALL_LIMIT>0` 时打开农场（默认 3/日）；0 仍是 H14 的关农场
- 主观状态事件流：Appraisal/Affect/Aspiration/Thread/Private Impression 都是"模型提议 → compiler → acceptance runtime → reducer 投影"模式。Appraisal/Affect 有完整接受运行时；Aspiration 无独立 acceptance（DomainMutationPayload）。

### 生活生态（Life Ecology）
- `activity_lifecycle_*` — 日常活动：模型从不透明 token 目录选 opening → compiler 派生权威字段 → 原子落账（ActivityStarted/Completed 等）。`activity_timing.py` 是纯规则（完成须 ≥60s 等）
- `life_ecology_runtime.py` — 调度器：clock tick 后按序跑 biographical→activity→aftermath→life_development→npc_initiative→open_world→visual_evidence→media
- `biographical_lifecycle*` — Life Arc 开/关（从已结算 outcome 提取），驱动 NPC 出现/离场
- `npc_ecology.py`（2038 行）— NPC 私有决策（actor 模型）+ 世界裁决（world author），产出 NPC Plan/Occurrence 走普通 aftermath 路径被主角消费。种子在 `configs/world_seed.yaml`（38 处 npc）
- `world_life_context.py` — settled occurrence → 模型上下文（ActiveWorldOccurrencePremise）

### 媒体系统（图片机）
- 管线：生活事件 → `event_ecology_media.py` 冻结 PhotoCandidate（12 类 taxonomy）→ `media_selection_occasion.py` 判定是否问（她的 `media_request` / 线程相交 / 对话邻近 2h，不读措辞）→ `media_selection_worker.py` 交角色决定 → acceptance（provider grant+预算+关系；无投递槽不问）→ `media_planning_runtime.py`（桥到旧 `event_media.py` MediaPlanner v5）→ `media_execution_runtime.py`（普通：`image_generation.py` OpenAI 生成 → `OpenAIMediaInspector` 审查 → ≤1 次修复；P3：Civitai Krea2，`specialized_private_workflow_direct` 跳过视觉审查）→ `media_delivery_runtime.py` 自动发送。生成与投递均日 2 张、间隔 2h、同时 1 张。
- **相册（2026-08-19）**：`photographable_inventory.py` 从投影直读可分享事实；条目带闭式 `hold_reason`（`expired` / `already_shared` / `skipped` / `unrenderable` / `failed` / `already_chosen` / `not_available`）。胶囊预算会挤瘦 `shareable_photos` 切片——空相册陈述仍必须出现，否则她会发明相册状态（见 `scripts/audit_context_truth.py` K1）。
- 隐私分层：`media_eligibility.py` `MediaEligibilityRouter` 划 ordinary/personal/intimate。P3 强度由她的 `declared_display` 决定（`declared_display_contract.py`），owner grant 只开可能性；close_friend 是关系地板。
- `image_generation.py` 里 VolcArk/ComfyUI/Fallback **无实例化调用点**（本地 ComfyUI 可行性 2026-08-18 仍在测，结论未定）。Civitai Krea2 仅 P3 车道在 `qq_media_deployment` 有条件安装；缺密钥或模板 fail-closed，不降级 OpenAI。**现网（2026-08-18）** key 与模板齐全，身份 LoRA AIR `urn:air:krea2:lora:civitai:2868686@3240992`；克隆上已出 JPEG。普通生产仍接 OpenAI `gpt-image-2`。Civitai 异步对账、永不二次 POST；AIR 404 预检拒 POST。账本 `cost_actual` 是 Action 预约整数，生图花费在 `usage_events`。

### 调度与健康（2026-08-19）
- **统一到期登记**：`declared_due.collect_clock_wake_dues` 是 QQ 调度器唯一选型入口（`qq_c2c_host._scheduler_once_serialized` 必须恰好调用一次）；AST 守门测试在 `tests/world_v2/test_declared_due_wake.py`。宿主不得再手写 dues 清单。
- **主动车道**：`social_initiative._post_silent_chain_active` 在 post-silent 进程 terminal 后释放 ambient（此前永久占位会饿死触景生情）。健康影子 due：仅当 `peek_next_due is None` **且** `unrecorded_cadence_still_open` 为假时清空（窗内尚未落盘 draw 仍报 `consideration_due`；ambient 过期 / 已结算链不再假 overdue，避免级联 `ledger_event_stream_stalled`）。

### 散文边界（2026-08-19）
- 生活作者写「发出去了」≠ 用户通道完成。权威字段：`user_channel_completion=none`（noticed 时必填）+ 恢复的 LLM 评论家 + 违规种 `completed_user_channel_act`。
- 三条通路都已钉住：Open World / NPC ecology / 私人印象（及 life_development 同源闭包）。散文可以描写，不能冒充已投递的 Action/回执。

### 一致性审计
- `scripts/audit_context_truth.py`：只读克隆生产账本，31 槽位对照「她看见的」vs「账本事实」（K1–K6 覆盖门）。日常应跑；预期 `finding_count=0`。产物默认 `output/context-audit/`，禁写 `data/`。**2026-08-19 晚**：seq 5874 克隆仍为 0；当前生产头 seq 5958 在 Path A 编译阶段因 `current_situation minimum whole-item budget is not satisfied` 未能出 finding 表（胶囊整项预算，与相册挤瘦同型坑）。

### 外部感知
- `world_v2/external_world_perception/` — RSS/NWS/USGS 源 → `hub.py` 采集/去重/嵌入/聚类 → `attention.py` 影子/实时注意力 → 模型决定 → ExternalPerceptionRecorded → 生活影响。靠 registry off/shadow/live 模式门控，半启用
- QQ 附件：`perception_trigger_runtime.py` 闭式语法决定是否分析 → `character_interior/qq_attachment_perception.py` 角色考虑 → `perception_vision_transport.py`（阿里百炼 `qwen3-vl-flash`，思考关闭；缺 `QWEN_API_KEY` fail-closed）

### 适配器与支撑
- `qq_client.py`（官方 HTTP）/ `qq_delivery.py`（双路分发）/ `onebot_adapter.py`（OneBot v11 文本/图片/face）/ `qq_outbound_owner.py`（出站租约锁）
- `conversation_cadence.py` — 对话热度分类器（hot/warm/cold，供 model_call_policy 用），**不是**消息批处理
- `budget.py` + `usage_metrics.py` — 月/日/软日 CNY 预算门 + 带版本价格表。**DeepSeek 峰谷计价**（北京 2026-08-17 起）：高峰 09:00–12:00 / 14:00–18:00 半开区间，官方 CNY 表，不再用 USD×7.2。
- `spend_account.py` — **debug/production 分账**：仅 `data/` 下具名生产库记 `production`；克隆/测试默认 `debug`（`output/debug-spend/model_usage.sqlite`）。审计见 `scripts/audit_deepseek_spend.py`。
- `llm.py` — `DeepSeekChatModel`/`OpenAICompatibleChatModel` + ProviderCircuitBreaker + 用量统计；每命名 turn 最多 8 次 provider 调用，连续 2 次失败冷却 30s。DeepSeek strict tools 把可选 object 编成 `anyOf`，不用 `type: ["object","null"]`
- **备用供应商（2026-08-19 实测定案，`output/fallback-provider/REPORT.md`）**：角色退路主备 = 百炼 `qwen-plus`；对等热备 = OpenRouter `glm-4.7-flash`（compact gate 15/15）。**Hermes / Kimi / MiniMax 出局**当角色退路（Hermes 仍仅作 P3 英文镜头提示词作者）。生产级双供熔断切换尚未接线（配置级可切，工程 1–2 天）。

## 关键不变量（改代码前必知）

- 一切写路径走 `WorldRuntime` 单入口；model-facing 的调用必须记录 ModelResult 供 replay
- 提交批次经 `batch_invariants.validate_commit_batch`；CAS 冲突抛 `ConcurrencyConflict`
- `vertical_registry.py` `assert_bounded_vertical_coverage` 是启动门
- Producer-First Authority：新 authority 必须和第一个生产者同批落地（见 CONTEXT.md）
- `configs/mechanism_closure.yaml` 标记 dormant 机制（如 resource_authority 四权威、v16 harness）

## 已确认的死代码/未接线（2026-08-19 晚复核）

- `world_v2/scenario_runner.py` — 仅测试与 `scripts/verify_world_v2_scenarios.py` 引用，生产 runtime 不导入
- `world_v2/scenario_corpus.py` — 被 `scenario_runner.py` 与离线 `formal_evaluation_pipeline.py` 引用，生产 ingest 路径不导入
- `aspiration_seed_policy.py` — 仅测试引用；`src/` 无生产 import
- `npc_initiative_weight_policy.py` — `npc_ecology.py` 仍 import，且 `_weighted_actor_decision` / `_weighted_world_decision` **仍定义**，生产路径无调用点（H23 拆除短路后的残留）
- `appearance_state` / `visible_physical_state` 记录者 — 宿主 seam 存在（`production_turn_application.record_*`），`src` 内无生产调用者，仅测试调用 `record_appearance_state`。投影读取已被 media snapshot 使用；2026-08-18 P3 `private_transition` 会冻已有 `appearance_state`，仍不产生 record 调用
- `resource_authority_*` 四权威 — 官方 DORMANT（`mechanism_closure.yaml` 的 v16-situation-constituents）
- `expression_decision_channel.py` — 守门断言，**仅测试/探针调用**；生产启动不跑（与 `assert_bounded_vertical_coverage` 不同）
- **已不再是死代码**：`drain_private_impression_once` 在默认日上限 3 时会真正 `open_once` + `advance_due_once`（H31）。`limit=0` 或未绑 policy 才安静 `return None`（H14 行为保留给测试）。执行计划 H14/H22/H23 剩余缺口里「恒 return None」的表述已回写。CLAUDE.md 本清单此前从未单列该函数。
- **已不再是死代码**：`collect_clock_wake_dues` / `declared_due` 已接线生产调度器（2026-08-19）。

## 测试布局

- `tests/` 顶层 30 文件：适配器、预算、媒体选片契约、房间编译器
- `tests/world_v2/`：character_interior 最大；含 ledger/sqlite、expression、npc_ecology、life_*、migration golden、formal_evaluation。2026-08-19 新增/加厚：`test_expression_decision_channel`、`test_declared_due_wake`、`test_social_initiative`（post-silent 释放）、相册/散文边界相关断言；离线机制基线 `world-v2-offline-mechanism-baseline.82`
- **无直接测试**：`conversation_cadence.py`（间接）、`qq_outbound_owner.py`（间接）、`world_media.py`。`cli.py` 有 `tests/world_v2/test_simulator_cli.py`。顶层 media_* 多数已有对应测试；无独立测试文件的是 `media_moment.py` / `media_interaction.py` / `media_domain.py` / `media_authenticity.py` / `media_camera.py` / `media_facial.py` / `media_address.py`
- `tests/support/` 是共享 fixture 构造器（非适配层）；`tests/js/` 是房间渲染器 JS 测试
- 日常运维：`.venv/bin/python scripts/audit_context_truth.py`（31 槽）

## 文档指引

- 当前权威业务与架构意图：`docs/design/girl-agent-design-intent.md`
- 当前唯一执行计划：`docs/design/harness-restructure-execution-plan.md`
- 历史施工记录（L0–L3 期，可查证但不产生任务）：`docs/design/root-causes-and-long-coupling-luna-plan.md`
- ADR：`docs/adr/0010-controlled-high-variance-character-agency.md` 必读
- 其余 `docs/design/` 文件、成本与形象文档均是可追溯的历史或专项证据，只能由上述两份权威文档按需引用，不能成为并列路线图。
