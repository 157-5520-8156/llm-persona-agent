# 原已选 Fact/Dialogue 的 visible review 准备

依据 [CONTEXT](../../CONTEXT.md) 与 [ADR-0010](../adr/0010-controlled-high-variance-character-agency.md)，本片只准备原已选来源。角色声明为空不代表可见正文没有事实，也不应导致审核材料遗漏原 Capsule 已有的完整来源。语义审核仍决定某句话是否被来源支持；本片没有审核调用、纠正、receipt、Action 授权或生产启用。

## 接口与绑定

`compile_visible_selected_source_context(request=original_model_input, capsule=original_capsule)` 是纯函数，无 ledger、检索或供应商端口。它输出兼容 `source-closure-evidence.3` 的独立 entries，并附 `visible-review-selected-source-proof.1` 标记。

入口先重验整个受信 typed `ContextCapsule`，包括 compiler tag、完整输出身份、slice/value/source hashes 与 bindings；再核对原 ModelInput 的 Capsule ID、trigger、三段 cursor 和完整 `model_content_json` 字节。返回标记固定列出 `relevant_facts`、`recent_dialogue` 两个 lane，记录原 snapshot、actor、cursor、Capsule/ModelInput hash 与每个 lane 的选择状态。它不宣称所有可见来源已经齐备。

经过 alias 或动态 presentation 改写的 ModelInput 不能反当原 pin。后续调用方必须在原 Capsule 仍可用的位置显式保存原输入，并分别绑定审核用的 presentation；本片不把新字段注入 ModelInput，也不改普通 chat/World Author wire。

## 选择、权限与隐私

- 不接收 draft 或 `world_claims`，因此空声明不抑制原已选材料，宿主也不能伪造声明来扩大选择。只保留原选中成员与顺序，没有当前 head 补取或新检索。
- 原 Fact/Dialogue 的完整 value、value/source hashes、bindings、privacy、subject/speaker、时间和状态原样保存。接受过的 Fact 来源与 Observation 报告身份分开；Dialogue 只标明 counterpart report 或 companion expression record，不升级为独立外部事实或交付证明。
- `withhold` 成员使准备失败，不返回其正文；不可用 lane 保持不可用且不生成 entry。private/personal 仍保留其原分类，审核可读性不授予对外披露权限。
- actor 只取 typed Fact 的 subject 或 Dialogue 的 speaker，不按 ref 前缀或正文猜测。当前 counterpart 映射还要求原 TriggerMessage 与已选 Observation Dialogue 的事件、hash、revision、actor、正文和 observation 身份一致；缺少这份已选证明时不建立 counterpart 映射。
- 本片不负责 biography、identity、activity 等来源的组合或资格登记。每种 Body 是否可支持 closed 仍由对应协议的显式合同校验；出现完整 Body 不等于所有类型均已合格。

当前旧 producer 的共享 alias 先到先得行为保持不变。特别是 Situation 与 ActiveActivity 可能共享 ActivityStarted ref；删除共享 ref 又可能丢失活动唯一有资格的支持索引。后续 composer 需要明确的投影权限优先合同或 ref 到多份 material 的绑定合同，不能把本片 Fact/Dialogue 准备当作该问题已解决。

## 兼容性与离线证据

`life_context` 仅抽出原验证及完整已选项投影的内部 helper；`compile_life_review_context` 的 `.1` 输出、检查顺序和失败边界保留。旧 claim-only producer、表索引、chat、World Author、历史 `.2/.3` audit 恢复没有改动。

专属公共测试通过临时 SQLite 的 Observation → Fact acceptance → 真实 LedgerProjectionContextResolver → ContextCapsule 构造输入。公开 RED 已证实：在 Capsule 已有 Fact 完整 proof 时，旧 producer 即使接收完整 life view，`world_claims=[]` 仍产生空 Fact entries。新接口将两类原成员送到 dormant 的公开 visible packet；测试同时覆盖错 pin、损坏原 proof、withhold、未选成员、后续 World/Observation 变化、错误 actor 映射和原 life/chat 字节不变。

相邻 life-review 测试继续覆盖历史 `.2/.3/.4` 冷恢复身份与公开 MockTransport 请求。本片没有真实模型语义资格、真实 HTTP、生产账本或纵向生活验收证据。
