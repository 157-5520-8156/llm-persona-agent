# 生活作者实际输入与原始来源选择的绑定

状态：已接入结构化 Life 作者及可选的持久化回合存储；没有接入语义事实审核。
角色仍决定感受、解释、打算和 null；本次只补来源与调用的身份边界。

## 当前实现

`_LedgerCapsuleInteriorProjection` 在编译 `world_stimulus_appraisal` 快照时保存
`LifeSourceOrigin`：完整、重新验证过的原始 ContextCapsule JSON 和摘要。来源档案进入
快照身份与私有持久化记录，不进入 `model_view()`。世界、角色、完整游标和时间必须一致。
Core 的预取、召回和能力绑定，以及投影的愿望/能力加入操作均保留此档案。
没有从同一时刻重新检索一份来源代替它。旧快照省略新字段并保留旧身份。

`StructuredCharacterRoleFaculty._complete` 使用即将调用模型的同一份 messages、工具、
温度和身份参数创建 `LifeSourceView`，绑定完整角色请求、实际调用 hash、原始 Capsule
和共享来源表。读取时重新编译用途裁剪与最终展示结果，检查实际发送的 snapshot；调用 hash
由保留的完整消息和参数重算，并与结果作者 lineage 对齐。不是仅根据 Capsule ID 判断。
系统消息、工具或角色请求的替换也会被发现。

来源准备字段由宿主附加，模型原有 wire schema 不允许作者自行提供。新档案存在时，Core
不能把缺失准备记录的结果写入 checkpoint；存储和恢复时再次验证来源表、发送视图与快照。
使用现有持久化 turn store 时，这些字段保存在 `authored_state_json`，不作为 World 事实。
没有配置持久化 store 的测试/组合只保留进程内结果；不能宣称它们已获得重启能力。
同角色纠正继续只进行一次，准备记录绑定实际第二次调用，原始 Capsule 不变。

每份原始档案上限 1 MiB UTF-8；消息、调用参数、来源表各自也有 1 MiB 上限。
不截断来源来通过上限。档案/准备失败按既有技术失败路径处理，不替角色填写 null 或话术。
完整档案增加的是本地记录体积，未加入角色提示正文，也未增加模型调用次数。
快照身份值会改变；本次未部署、未对生产中未结束的回合做升级验收。

## 明确尚未证明的事项

- 源表保留原始 Capsule 的完整已选集合；某用途没有展示的材料仍可能在目录里。
  目录成员资格不等于该字段具有支持某个命题的权限，也不意味着作者看到了完整原文。
- 关系/愿望等附加材料、后续预取/召回和 capability 正文不能只凭同一 source_ref 就算闭包。
  `unmatched_visible_source_refs` 只是确切引用差集诊断；为空不证明全文来源覆盖。
- 输出保留 `write_authority=false`、`semantic_coverage=not_assessed` 和
  `source_permission_coverage=not_assessed`。这不是语义审核回执，也不会阻止当前两个已知
  无依据生活经历写入反例；两者继续 strict xfail。
- 下一步需根据实际呈现的材料与各来源读法建立字段级支持，再把全候选事实读取/核验接到
  私有摘要、Appraisal、Thread、LifeResponse 和 Experience 的接受之前。真实读取器漏读
  嵌入前提、误拒自由表达的反例仍需解决，不能拿本次绑定正确替代语义资格。

验证与尺寸记录见 `docs/audits/life-source-view-validation-2026-09-16.json`。
