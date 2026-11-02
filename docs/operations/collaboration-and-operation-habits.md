# Girl-Agent 协作与运维习惯指南（Session Handoff & Operation Habits）

本文档总结了我们在开发、调试和运维 `Girl-Agent`（World V2）过程中形成的核心协作习惯、设计默契与线上排障铁律。新开会话时，结合 [`AGENTS.md`](../../AGENTS.md) 与 [`CONTEXT.md`](../../CONTEXT.md) 阅读本文档即可快速对齐上下文。

---

## 1. 沟通与产品设计习惯

1. **先讨论对齐，再动手实现**
   - 当涉及机制取舍（如主动破冰、未回消息的心理反应、课表/日程生成、命运与巧合联动）或前端视觉/信息架构重构时，**先和用户讨论方案与边界**，确认方向后再写代码。
2. **用户的“举例”是启发方向，绝不能写死成规则**
   - 用户经常会用具体的生活场景举例（例如：“有的时候学期初课多学期末课少”、“真人几次不回消息可能会觉得算了或者追问你人呢”、“某种命运巧合”）。
   - **铁律**：这些例子是为了说明**世界与角色心理应当具备的弹性**，绝不能把例子里的具体数字、句式、场景直接硬编码成 `if/else`、固定枚举、固定课表模板或固定话术。
3. **坚持“基于心理学仿真”，清除机械限流与写死机制**
   - 角色因为不被理睬而冷淡、各自忙生活，或者因为关系好、有期待而追问“怎么不回消息”，都必须是**基于心理与关系慢变量的自然涌现**，而不是靠“最多两次主动破冰”、“固定防话痨冷却”等写死的计数器限制她的行为。
   - 当需要时间间隔时，优先使用**带随机抖动的心理时间窗**，并把真实的“距上次互动/未回消息过去了多久”、未兑现期待（`ResponseExpectation`）、重访意图（`RevisitIntention`）、当前生活状态如实放进 `InnerLifeSnapshot` / `ContextCapsule`，交给角色模型自己决定怎么表现。
4. **盘点并复用现有机制，不重复造轮子**
   - 新增或修改效果前，先全局检查项目里已有的机制（如 `Appraisal`、`Affect`、`RelationshipState`、`ResponseExpectation`、`InteractionAct`、`LifeEcology`、`Aspiration` 等），查清有哪些机制本应互相影响却没接通、或接通了但没在 Context 里真正生效，优先打通闭环而非叠加新轮子。

---

## 2. 线上环境与数据库操作铁律（防踩坑）

### 2.1 守护进程与服务管理
- **QQ / World V2 主守护进程**：`com.girl-agent.napcat`（由 macOS `launchd` 托管）
  - 标准错误与阶段耗时日志：`logs/napcat.err.log`
  - 标准输出日志：`logs/napcat.out.log`
  - 修改代码后重启生效命令：
    ```bash
    launchctl kickstart -k gui/$(id -u)/com.girl-agent.napcat
    ```
- **本地 Embedding 服务**：`http://127.0.0.1:8190/v1/embeddings`

### 2.2 线上 SQLite 数据库操作禁忌
- **核心数据库文件**：
  - 主账本：`data/companion.epoch2.sqlite`
  - 不可变生活内容侧车：`data/companion.epoch2.life_content.sqlite`
  - 角色内省回合协调库：`data/companion.epoch2.character_interior.sqlite`
- **只读诊断铁律**：
  - 当 `com.girl-agent.napcat` 正在运行时，排查线上状态**必须使用只读连接**：
    ```bash
    sqlite3 "file:data/companion.epoch2.sqlite?mode=ro" "SELECT ..."
    ```
  - **严禁**在线上守护进程运行期间，用外部临时脚本对 `data/companion.epoch2.sqlite` 实例化 `SQLiteWorldLedger`（这会触发全量冷启动历史校验并与线上进程争抢 SQLite 锁，导致前台回复超时）。
- **控制查询输出体积**：
  - `world_v2_events`、`world_v2_head_state_items`（尤其是 `model_result_audits`）和 `world_v2_character_interior_turns` 的 JSON 字段很大；用 SQLite CLI 排查时优先使用 `json_extract(...)` 或 `substr(..., 1, 300)`，避免一次性把数百 MB 的大字段打爆终端或上下文窗口。

---

## 3. 线上常见问题标准排查链路

当用户问 **“为什么角色现在不回话 / 是不是某机制发力了”** 时，不要猜，按以下 4 步拿账本证据回答：

1. **找最新入站消息（`ObservationRecorded`）**：
   ```bash
   sqlite3 "file:data/companion.epoch2.sqlite?mode=ro" \
     "SELECT ledger_sequence, world_revision, json_extract(event_json, '$.event_id'), json_extract(event_json, '$.payload_json')
      FROM world_v2_events
      WHERE json_extract(event_json, '$.event_type') = 'ObservationRecorded'
      ORDER BY ledger_sequence DESC LIMIT 5;"
   ```
2. **查对应 `expression_episode` 触发器状态**：
   ```bash
   sqlite3 "file:data/companion.epoch2.sqlite?mode=ro" \
     "SELECT item_json FROM world_v2_head_state_items
      WHERE field='trigger_processes'
        AND json_extract(item_json, '$.process_kind')='expression_episode'
      ORDER BY idx DESC LIMIT 5;"
   ```
   - 如果 `state="claimed"` 且 `runtime_outcome_ref=null`：说明并不是角色不理人，而是遇到了**技术超时/故障**，正在等待 `expression.technical_retry`（退避时间窗为 `10min / 30min / 120min`）。
3. **查 `ModelResultRecorded` 与 `ProposalRecorded` 区分“角色决定”与“技术失败”**：
   - 看 `ModelResultRecorded` 的 `status / outcome / failure_code`：若为 `main_timeout` / `budget_exhausted` / `primary_timeout`，属于技术超时。
   - 看 `ProposalRecorded` 的 `private_turn_state.inner_state_summary`、`appraisals` 和 `behavior_tendency`：这里记录了她真实的心理活动与是选择回复（`respond`）还是保持安静（`hold`）。
4. **看 `logs/napcat.err.log` 的阶段耗时**：
   - 检查 `pinned turn phases`（`snapshot_ms`, `context_ms`, `model_ms`）、`Context resolve phases`（`domains_ms`, `budget_ms`, `refs_ms`）以及 `ledger commit phases`（`reduce_ms`, `encode_ms`, `sqlite_commit_ms`），精准定位性能瓶颈。

---

## 4. 前端 Dashboard 与视觉/媒体习惯

1. **Dashboard 界面设计**：
   - **视觉风格**：偏好青绿色（Teal / Cyan）科技感主题，搭配恰当的微动效与重点状态高亮。
   - **信息架构**：拒绝“像 Excel 表格一样平铺直叙或重叠挤压”；有上下级/父子从属关系的数据必须按层级结构展示；高价值实时状态突出展示，低价值/调试元数据折叠或紧凑收纳；做好响应式自适应，不随历史数据增长无限撑爆页面。
2. **媒体与贴纸（Media & Stickers）**：
   - 生活照生成强调真实日常感（避免过度精致摆拍的塑料感），猫猫表情包/贴纸与角色当下的情绪、吐槽和对话语境自然结合。
