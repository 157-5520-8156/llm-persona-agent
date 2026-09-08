# 显式完整正文审核发送闸门

本片把既有 whole-candidate author、selected source composer 和纯 receipt 接入实际 inbound 授权链。默认仍关闭；没有真实供应商、QQ、部署或语义普遍正确的验收。本片不新增角色行为规则、模板或检索。

## 安装与权限

`build_qq_c2c_host` 显式接受 `visible_source_review_required=True` 和调用方已捕获、计费的 `visible_source_review_model`；此时必须显式 `expression_episode_mode=off`。不创建额外 HTTP client。原有 legacy source review、caption/claims reviewer、source repair 均未因此安装；确定性声明/source/Action 边界保留。普通路径一个完整角色调用、一个完整审核调用；合法 unclosed 只进入 Core 既有一次同角色纠正，完整替换稿再次审核。技术失败不触发角色沉默或本地回应。

Deliberation 在原 Trusted Capsule 上生成完整 selected source table，并把原 ModelInput 与表一起作为 host requirement。该字段不进入角色 HTTP messages；Faculty 原输入 hash 仍涵盖它。required Faculty 使用显式 `character-interior-inbound-capability.2` 与 `inbound-reviewed-turn-capability:sha256:` 身份，原 `.1` 字节不变。原 Core terminal 的 output_record 保存 receipt 和全部既有 excluded 调用审计。

接受/冷恢复使用三个独立原锚点：

- 原不可变 CharacterInterior lineage.capability_ref：重算含 requirement 的原 Faculty material，绑定原输入与 Capsule、WR/DR/LS；不按当前 head 补材料。
- 原 ModelResult request_hash：重算实际作者 messages/tools/tool_choice/temperature/完整 identity_extras，从实际 `expression_hard_boundaries.source_ref_aliases` 得到当次 aliases。
- 原独立 provider subcall：完整审核 request/response hash、父调用与模型身份验证 receipt；receipt 的候选是完整 Proposal 与完整 Beat 映射。

新 `model-result-audit.9` 保存原 carrier，UTF-8 总审计上限 1 MiB；carrier/requirement 各有 512,000 bytes 边界，不截断。原 `.1–.8` 不追授或补写。新的 `expression-plan-acceptance.2` 保存 receipt hash；required policy 与原 capability 独立决定是否必需审核，缺 receipt、换表/alias、删 marker 或冒旧审计不得降级。新 required profile 不消费旧 pending minimal reply；旧 profile、旧 manifest `.1` 与历史字节保持。

## 验证口径

公开 SQLite + Core + DeepSeek MockTransport 测试验证：空 world_claims 也审核全部正文；review 未完成前没有 Action；一次纠正完整重审；再拒或坏 verdict 无 Action；Proposal 审计后模拟中断，冷开零新模型调用、重验 receipt 并 effect-once；改表/aliases 再重算 receipt 仍被原锚点拒绝；原 required capability 不能冒旧 audit。独立临时主用量账本中四次调用分别 known、四次 reservation settled；不把 reviewer 用量再并进角色该次用量。

Recall 继续由 Core 执行。最终作者请求/hash 单独绑定，但本片的审核来源表只含原 selected Capsule，额外 Recall 不自动获得世界事实审核资格。历史 Recall 的聚合作者用量展示不应与其独立子调用简单相加；真实主账本逐物理调用结算。

## 明确限制

- 同供应商 reviewer 属相关审核，不是独立事实验收；模型仍可能漏判/误判或错误自分类。结构覆盖不等于语义正确。
- source table 仅当前已资格的 situation/activity/biography/Fact/dialogue/current-report 材料；不补 identity、后续 head 或额外 Recall 权限。
- 本 receipt 首片仅完整 inline text、至多 16 Beats；其他载体不可跳过后发送。角色仍可选择不表达、生活意图等非可见 facet。
- 旧 local Recall conversation 与带 `technical_recovery_failure` 的额外恢复 capability 不在本片资格内；不能以当前普通原 pin 重签这些历史载体。同角色一次纠正沿原 capability。
- 默认仍关闭。原完整 strict author 工具 schema 很大；根实际 CLI MockTransport 测得作者约 150,582 字符，其中 tools 约 114,080，原实际 payload 预约约 0.543558 CNY、审核约 0.090042。不能缩小预约或提高试验额度掩盖；它会限制 `.60` 试验的多轮可运行性。这是冻结完整工具协议的成本限制，本片没有压缩它。

固定提交前定向门：7 个测试文件 **121 passed / 18.50s**，包含新增公开 Core Recall 最终三次 HTTP 与主账本三笔 known；原 `.1–.8` audit 的八个冻结 canonical hash 全部保持。变更 Python 的 Ruff 和 diff check 通过。根另负责 CLI 集成、恢复组合与正常 120 场景门，本片不将这些尚未运行的整合门列为通过。
