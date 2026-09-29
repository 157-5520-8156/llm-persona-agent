# 前史与 NPC 接线审计

只读检查前史和 NPC 接线。边界见 [CONTEXT.md](../../CONTEXT.md#npc-actor-isolated-semantic-domain)、[ADR-0010](../adr/0010-controlled-high-variance-character-agency.md)。未访问生产/API；联动未实现。

1. **没有历史人物到运行 NPC 的映射。** `PrehistoryRecord.participant_refs` 只校验档案实体（`character_prehistory.py:51-61,132-139`），读取也按历史 ID 闭包（`prehistory_memory_source.py:114-125`）。导入/reducer只接收审阅档案（`production_turn_application.py:3801-3805`; `character_prehistory_reducers.py:19-77`），未见绑定事件。NPC 独立经生活目录 `NpcRegistered` 注册（`production_turn_application.py:4842-4875`）；`promotion_edge` 连临时世界实体与 NPC，不连前史人物（`npc_identity_view.py:82-140`）。同名不能绑定。

2. **已有独立 NPC 上下文，尚无前史记忆接入。** NPC 胶囊给它自己的状态、目标、计划、关系和共同经历摘要（`npc_actor_profile.py:9-49`），只为当轮选中的 NPC 编译（`npc_ecology.py:952-1012`）。共同参与不让 NPC 读取主角私有体验；只提供经参与/隐私门控的独立 World 后果（`npc_identity_view.py:207-240`）。共同结算推导的 `protagonist_relationship` 与 NPC 自己提交的关系判断分开（`npc_relationship_view.py:115-177`; `npc_ecology.py:177-195`）。`_record_actor_state` 已持久化NPC自己的内心摘要、目标及对主角的关系判断（`npc_ecology.py:1614-1688`），不是完全没有主观状态。上述短摘要不等于完整自传体记忆/RAG；前史仍主要是 `statement`，不能把主角回忆/感受复制给朋友。

3. **NPC 当前能做什么。** `NpcActorProposal` 可提出活动，声明参与者、地点、时长、可见度或未来计划（`npc_ecology.py:198-230`）；进入通用 plan/occurrence 事件机（`_commit_plan:2072`, `_commit_occurrence:2131`）。主角参与且事件结算后，生活/体验链才读到结果。生产只在配置开启、模型存在时接入（`production_turn_application.py:4416-4437`），Life Ecology quiet wake 调用（`life_ecology_runtime.py:643-669`）。这是虚构事件；schema 固定 `user_channel_completion="none"`（`npc_ecology.py:198-202`），无 QQ/第三方直发消费者。

4. **样稿风险。** 罗嘉禾、陈曼仍是候选身份，不映射现有 NPC。知栀只知道亲历、看见或嘉禾告知的事；两人的私下感受、未告知的经营分歧不能灌入知栀记忆。陈曼只拥有自身亲历/获告知范围。样稿中的赴杭州工作已发生于候选历史，但尚未经系统接受；结尾赴上海换工作和短租仍属考虑，不能当作已迁居、已入职或启动后的已接受计划。审阅可提供过去的共同历史；启动后的关系与选择仍由各 Actor 决定。

5. **最小路径/唤醒。** 审阅身份后增有来源、可撤销的双 ID 绑定；逐条确认共享范围，分别向双方私有 Actor 提供来源闭合材料，另一方理解由自身形成。缺口：绑定/撤销事件、NPC 前史隐私门控与带历史来源的长期主观记忆读写；应复用现有独立状态/关系写回，不重建平行系统。`PrehistoryMemoryRuntime` 只服务主角。NPC 不必每 tick 调用：`advance_once` 先续计划/待决策，处理刺激；无刺激才抽机会并受周上限约束，每次只选一人（`npc_ecology.py:650-790,1384-1414`）。
