# 生活输出约束与实际传输修复

范围：用户同意继续优先生活连续性与速度/成本，近况语义仍暂缓。基于上轮保存的真实
失败请求修复确定性接口，不更换角色、不补造生活事件、不放宽来源或本地校验。

## World 环境字段

实际 `e54ac…` 输出为合法 JSON，且通过原 tool.2 schema，但环境四字段全 null，
本地模型要求至少一项非空；这是两端结构约束不一致。

`d3f1bdce` 的新 tool.3 从 canonical 环境字段构造完整 `anyOf`，字符串用 `pattern`
表达非空。环境整体仍可为 null，没有强制补天气或光照。旧 tool.1/tool.2 原 schema
及哈希保持，历史纠正恢复其原工具。新的紧凑工具定义为18,066字节，原 tool.2 为16,400。

82项相关回归通过；独立检查实际坏输出为 old2接受/new3拒绝，环境整体null以及各
字段分别为非空或单独换行均合法。固定旧工具forced/auto字节保持。数组数量、对象
local_ref唯一、来源子集及位置值匹配仍由本地校验，不能用native schema代替事实权限。

此方案使用[DeepSeek 官方工具文档](https://api-docs.deepseek.com/guides/tool_calls/)
支持的严格对象、anyOf、pattern；其不支持数组minItems/maxItems及字符串minLength/
maxLength。JSON Schema的组合语义参见[标准说明](https://json-schema.org/understanding-json-schema/reference/combining)。

## Appraisal 传输

实际初稿786b…及纠正d5c…都未带strict，因而此前不能把未转义引号归因于“严格模式
仍不保证JSON”。纠正携带完整且hash绑定的原输出，未发现其证据丢失，不改纠正布局。

新DeepSeek请求复用现有严格schema投影和beta路由，采用显式v2工具/result封装；
standard v1原字节保持。240项相关回归通过，含真实适配器的beta URL、一次纠正、
原始错误字节保留、SQLite恢复及禁止重编旧工具的v1恢复。新封装仅用严格JSON解码，
不修补引号或打捞片段。已保存的
LifeSourceView仍按原provider_controls_json恢复，不重新编译为新工具。

同一真实manifest及48个source tokens的工具定义：v1为12,719字节，v2为8,460字节。
这只是schema字节差，不能宣称等比例token、延迟或费用下降。现成引用压缩仅能再省
157字节，未为此增加额外表示。

## 真实复验边界

继续点为上一轮 `after-source-fix/` 的1027/413、1793 usage/1790 reservations/74既有unknown。
账本时间04:40:01Z，生活重试真实到期05:10:01Z；旧近况待回复05:00:30Z会先到期。
测试将用正常模拟入站开启一轮新对话，随后自然推进到生活到期，保持原账目、来源和
退避。阶段结束时遇新失败停止，另有20次物理调用及240秒总墙钟硬限。
本轮不部署、不发送真实QQ；完整交付、生活后果、冷恢复、耗时和费用分别验收。
