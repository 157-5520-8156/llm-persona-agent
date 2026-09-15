# 表达 schema 顺序与既有事实契约一致

## 原始证据

上一轮实际初稿和纠正稿都收到录取材料记忆的完整原文。输入没有“后来一直留着袋子”的既存
叙述；纠正稿新增了这项经历。输入也明确区分活动结束与意图实现，散步结果只记载under way，
并未记录走完或返回。内部摘要却写成刚走完回来。不能用这些发现宣称所有历史内心材料已查清。

另有一个可直接修复的接口矛盾：角色提示要求world_claims及其他字段在beats之前，实际
DeepSeek strict v3 schema却在主对象和三个时间分支中均把beats放在world_claims前。
实际模型也先写正文再声明原始记忆，新增细节没有进入声明。

## 改动

增加默认关闭的`evidence_first_schema`选项，显式版本为
`expression-evidence-before-beats.1`。隔离CLI用`--visible-author-evidence-first-schema`。
仅strict atomic v3可用，可与本地schema引用压缩组合。

`expression_schema_order.py`只重排schema properties中已有的表达字段，让beats居末。
变换前后Python字典必须相等：除属性排列外，每个schema值、约束、required顺序、分支、枚举
和默认值完全一致。示例或枚举中的普通JSON对象不会被当成schema重排。`schema_nodes.py`
提供两种schema变换共用的子节点遍历，引用压缩不依赖表达排序。

初始化、回忆后、最终表达和一次纠正都接到同一契约编译器。顺序敏感的schema指纹和版本进入
契约identity，工具说明也显式标记新版本，使请求哈希能够区分新旧表示；默认关闭时保留旧字节。
消费者不强制模型输出JSON键的排列，不替角色填写事实声明或选择表达。正文里的未声明事实
继续经过原有完整审核；字段顺序不能充当事实完整性证明。没有新增模型调用或提高重试/超时。

## 参考与适用边界

[Lost in the Middle](https://arxiv.org/abs/2307.03172)研究问答和键值检索中的输入位置效应，
提示应测量材料组织方式；它没有证明本项目的schema顺序导致了编造，也不是DeepSeek v4的
资格证据。本次改动的直接依据是项目自己的提示、实际schema与返回顺序互相矛盾。

[Large Language Models Cannot Self-Correct Reasoning Yet](https://arxiv.org/abs/2310.01798)
研究缺少外部反馈的推理自纠正。本项目已经提供外部拒绝反馈，不能直接套用其结论说纠正必然
无效。应保留同角色纠正与独立核验，并继续检查反馈是否能被模型实际使用。

新顺序对真实讲述和纠正的效果必须另行测量。一次输出顺序改善、来源审核拒绝错误，均不能
证明自然对话成功或长期生活/成本合格。
