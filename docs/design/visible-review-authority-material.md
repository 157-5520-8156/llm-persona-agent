# Visible review 的已选可读材料

本片依据 [CONTEXT](../../CONTEXT.md) 与 [ADR-0010](../adr/0010-controlled-high-variance-character-agency.md)，只修复一个审查准备接口。普通聊天未安装语义 reviewer；本片不增加调用、开关、来源检索或角色行为规则，也不能证明 `world_claims=[]` 中的漏报已经解决。模型仍可能把外部事实错误判作 `source_free`；材料和结构验收不是语义穷尽证明。

## 已证实的输入缺口

当前 `_source_closure_evidence` 已选出 biography 的 `material`、Context 的 `item/slice` 和当前报告的 `message/messages`。旧 `compact_source_reference_table` 仅保留七个 row 字段，丢掉前两类可读值；Fact 内部 `value.subject_ref` 也不进入旧顶层 actor/subject 字段。这使 reviewer 可能只收到 opaque ref，且无法区分用户 Fact 和角色经历。

原 trial08 请求仅能重建部分运行时视图，未将它作为本片全量证明。这里的集成 fixture 来自公共 SQLite Fact runtime → ledger capsule compiler，再经现有 source evidence producer → public table → public review messages。biography 使用既有 provider presentation fixture 和公开 coordinate reader；它不是 SQLite 真实 biography，也不是原试聊。

## 投影与资格

新准备输入标识为 `visible-source-materials.1`，输出 verdict 仍为 `.1`。旧七个 row 字段、ref 顺序、索引、普通 `_known_capsule_source_refs` 集合保持原样。每个 entry 只构造一次显式投影；packet 将相同材料集中到 `source_materials`，row 用 `material_index` 引用。

新 packet 不再序列化旧 row 的 `evidence_text` 旁路，正文只从经过隐私投影的材料进入。原报告正文已完整位于新 `message.text`，被 withhold/unavailable 排除的正文不能从 legacy 字段重新出现；内存中的历史 row 与旧无材料手工 packet 字节仍不改。新纠正提示使用 `support_subject_role` 并显示原支持主体与资格，避免 eligible 用户 Fact 被旧 `subject_role=None` 掩盖；旧纠正请求的 hash 单独冻结。

- entry 仅保留显式 kind/lane/scope/authority/actor/ref/privacy/availability 与报告权限字段；不复制任意未知顶层字段。
- biography 保留完整 coordinate contract、parent ref、scope、field path、value 和 logical time，原 ref 必须匹配完整材料 hash。
- Context item 仅保留明确 item/source refs、source/value hashes、bindings、privacy/availability/authority scope，以及已选 `value` 的完整原字段。slice 仅带已有选中 items 和明确 slice 元数据，不扩大选择，不恢复 capsule 中其他值。
- 当前报告只带原 actor/event/observation/hash/revision/channel/text/time 及已选连续报告的 text/time/sequence/ref；不复制附件或平台路由材料。

当前有验证资格的形状为 exact biography、当前带原 Observation 绑定的文本报告、以及有完整 value/source hashes + bindings 且包含已定义正文属性（text/summary/source_excerpt）的 Context item/slice。其他形状保留为 `baseline_only`，不能据此声称已完成所有 source 类别的接入。

普通 chat Fact 视图已裁字段，但带的是完整 payload 的旧 value hash。本片保留其当前可读值，不补取完整 body；不匹配的 hash 显式改名为 `unverified_value_hash`，该材料仅作 baseline。完整 source-bound 视图使用原完整 body、hash 与 bindings，单独测试，不接入 chat。withhold/unavailable 不恢复正文；private/personal 可读 Context 仍保留原隐私，资格不授予对外披露权限。

`support_subject_ref/support_subject_role` 从原材料的明确主体与同次 evidence.subjects 精确匹配。不会按 actor 前缀、名字或正文猜测主体；旧 `subject_role=None` 不覆盖新材料的实际主体。未知或混合主体只作 baseline。

只有显式提供 `source_references` 的新 parser 路径校验材料资格、索引对齐与实际主体；`closed` 引用无正文、metadata-only、attention/advisory、hash 不匹配或错误主体会拒绝。空表可用于 negative review。历史未传 table 的 parser 和旧手工 packet 不重标、不补资格、不改变原字节。

## 离线证据和成本口径

`tests/world_v2/test_visible_review_authority_material.py` 包含公共 Fact 选择、重复 refs 去重、完整/裁剪材料、主体错配、metadata-only、四层 withhold/unavailable 和历史边界。测试不调用任何 provider；存在支持判定的 parser fixture，不代表模型语义识别能力。

以下是同一 fixture 的 UTF-8 canonical **messages** 字节数，不是完整供应商 HTTP body、token 估计或已付账单：

| 输入 | refs / entries | 旧 messages | 新 messages | 原 row metadata | 集中材料 | 假设完整 entry 按 ref 复制 |
|---|---:|---:|---:|---:|---:|---:|
| 普通 slim Fact + 当前报告 | 9 / 2 | 5852 | 9629 | 2738 | 2366 | 18557 |
| 完整 source-bound Fact + 当前报告 | 10 / 2 | 6109 | 11284 | 2977 | 3575 | 25121 |

可读输入有成本，集中材料避免同一 entry 按 refs 重复复制。没有为降低成本修改 token 估价、预约系数或调用预算。测试中的 `fixture_wire` 输出可重复这些测量；临时 ledger 由 pytest 提供，原试聊与原账本不改。
