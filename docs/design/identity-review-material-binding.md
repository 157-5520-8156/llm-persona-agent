# 身份审核材料与原来源哈希一致

公开反例来自原 `_source_closure_evidence` 两个入口：声明引用与 `include_visible_authorities`。稳定身份 ref 的哈希明确排除了完整提示、外貌、背景、日常生活、语气示例和预设开场，但旧 `_identity_source_material` 仍把这些字段附在同一 ref 下。修改其中任意字段，来源 ref 不变，材料却改变；完整材料 hash 也与来源 token 不符。

`companion_identity_source_material` 现在统一提供来源哈希和审核投影使用的精确材料。既有 stable/shared/counterpart 三类来源编号不变；普通角色身份提示也未改写。审核入口仍只暴露原先允许的 stable/shared 两类，不新增 counterpart history 权限。

这片修复没有把 identity_source 升级为 eligible。原有 compact 来源表仍将它保留为 baseline_only；稳定身份中的语气、价值观等字段也不能因此支持任意具体经历。身份的原输入 pin、语义 scope、shared history 的主体资格及完整审核接入仍需单独验证。

回归测试直接调用原 source producer，覆盖有/无声明两个入口、所有原未哈希字段的变更不影响稳定身份材料，并冻结修改前已有的三种来源 ref。RED 为 8 failed、4 passed；这是离线精确绑定反例，没有真实供应商调用。

修复后的专属及相邻五文件门为 **275 passed，8.59 秒**，日志 `/tmp/girl-agent-identity-material-green.log`。原角色 prompt 和 identity_source 的 baseline_only 资格保持；这不是身份语义审核的真实模型验证。
