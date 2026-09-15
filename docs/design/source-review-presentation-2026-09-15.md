# 来源审核的工作上下文与原始证据分离

## 实际问题

`release-chat-independent-20260914-04` 的作者耗时4.6–4.8秒、并行读取的慢者3.3–3.6秒，来源核对仅剩1.8–2.1秒后被截止取消。一个实际来源请求包含79386字节材料，重复坐标也占据输入。每个事实还要求一段explanation，但后续来源验收和同角色纠错不读取此字段。普通12秒截止保持不变。

## 参考与取舍

[Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)强调精简、相关的工作上下文以及按需检索。[DeepSeek context caching](https://api-docs.deepseek.com/guides/kv_cache/)要求重复前缀，缓存是best effort，不能将缓存当作时限保证。

这里先采用更窄的无损改动，而不按语义筛掉可能反驳候选的材料：原始来源树和目录仍用于固定编译、权限检查与凭据；模型工作视图只把重复长字符串存一次。所有原文及结构都还在，引用字典可完整展开。原始请求保持读取兼容。这不是新增审核步骤，也不是语义摘要或记忆遗忘。

## 职责

- `shared_string_view`只编码/解码JSON值，不依赖角色、世界、供应商或来源模块；避免原文与引用标记冲突，拒绝无法还原的字典。
- `visible_independent_meanings`拥有来源任务的呈现方式。新`.2`格式使用字典，仅输出逐事实的id、支持判断与证据id；去掉未被消费的解释文字。原`.1`请求编译不变。
- `visible_review_protocols`集中入口、运行时和凭据版本身份，避免多个入口维护不同白名单。
- 显式version 11在角色生成前固定protocol `.3`。它沿用version 10的两个独立读取及一次结构纠错规则，仅改变来源请求呈现及输出格式；新凭据`.11`不能冒充旧请求。生产默认不切换。

实际旧来源树的完整无损呈现从79386降至59325字节（54个共享字符串）；这只是离线测量，尚不等于供应商token减少、语义准确或完整聊天通过。需要真实运行验证。
