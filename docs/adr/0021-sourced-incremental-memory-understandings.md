---
status: accepted
---

# 在既有记忆复查中形成角色自己的长期理解

## 问题

原有复查只选择 retain/forget。前史、生活后果和角色自己的反应虽可保留，
跨经历的理解仍缺少稳定的增量整理、历史回看和后续决策读取链路。
Hindsight 的 observations/mental models 值得借鉴，但不能引入另一个作者决定角色的人格。

## 决策

复用 `MemoryConsolidationRuntime` 和同一 `CharacterInterior.consider`。
一次后台调用同时处理保留复查与理解更新，不增加聊天前的整理调用。
新记忆可在接受四小时后开启合并机会，批次最多八条；时间只提供机会，
保留、遗忘及下次复查间隔仍由角色决定。跨 fact/experience/prehistory 的证据轮流进入批次，
避免大量同类生活记录挤掉整个前史。补充最多四条精确关联故事/既有理解的来源。

同一个角色看到既有长期账本，可以 retain 新方面、revise 已有方面、merge 重复方面、
release 搁下理解，或者明确返回空数组。方面名称、内容、信心、来源选择和合并判定
均由角色提供。BGE-M3 只排序相关旧理解，不决定哪些观点同义、更不决定行为。

新协议只在 evidence_quotes 中选择一次来源，并验证摘录确实存在于对应原文。
保留旧回执的显式 source_tokens 读取格式；不再让新输出重复两套来源声明。
关注来源可使用本轮给出的 m/k/u 令牌，宿主仅恢复精确身份。
这不是事实蕴含的语义审核，不能由引文存在推断整段总结绝无扩写。

理解是一种**可重建、可修订的私有记忆阅读视图**，不新增 World Fact。
它需要完整的已完成复查回执、绑定的角色模型审计与摘要哈希、所有实际接受的保留效果、
准确的 actor/world/epoch 以及有效的来源。
单独保存的付费决定、未完成批次或被修改的 sidecar 不获得读取资格。
`TriggerProcessCompleted` 仍是 deliberation receipt，按 immutable_payload 绑定，
不能冒充 committed World event，也不签发 Action 权限。

视图由 `MemoryUnderstandingReader` 恢复。每次读取重新检查当前支持材料；
修订/合并继承来源依赖，遗忘、来源纠正、隐私收紧或正文不可读不能通过子摘要绕过。
旧版本不删除；仍有访问资格的历史版本进入显式 historical Recall，
当前版本进入 private_self/selective_memory 与 BGE 检索。
普通聊天保留六条阅读预算；后台能力明确给出的八条必须全部得到来源绑定。
存在未整理新记忆时标记 freshness，不能将旧理解视为已经考虑所有新证据。

事实摘录保留 subject_ref/speaker_ref，防止把对方说的“我”整合为角色自己的前史。
生活的 WorldConsequence 与 CharacterResponse 继续分别呈现。
理解不会自动写入 Affect、关系、目标或行为脚本；后续角色读到它以后仍自行判断。

## 后果与边界

后台整理不删除原始记忆，不替代尚未安装的 Fact/Experience 压缩表示授权。
来源引文和支持依赖是硬边界；理解、信心、是否改变与如何行动是角色决定。
一次调用最多三条更新；不为每条观点另外调用合并/审阅模型。
模型输出错误仍走同作者一次受约束重选，失败记录技术问题，不伪装沉默。

当前实现通过现有成功批次重建视图，较大历史规模的增量缓存仍需实测。
测试与隔离副本不等于生产切换、长期人格合格、聊天首条延迟或月度成本合格。

参考：[Hindsight observations](https://hindsight.vectorize.io/developer/observations)、
[Mental Models](https://hindsight.vectorize.io/developer/api/mental-models)。
