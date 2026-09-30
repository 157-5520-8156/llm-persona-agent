# Hindsight 对本项目的适用性

2026-09-30。依据官方仓库、论文与文档；未安装 Hindsight、未向其云端上传角色数据、未完成两系统检索质量实测。

结论：值得借鉴其记忆基础设施，但现有证据不能证明整体替换是更优解，更不能用其公开问答基准成绩承诺本项目的中文角色回忆质量、编造率、成本或速度。

## 已核实的设计

- [官方仓库](https://github.com/vectorize-io/hindsight)：区分世界信息、经历、整理后的观察、mental models；retain 提取实体、时间与关系，recall 检索，reflect 推理。
- [Recall 文档](https://hindsight.vectorize.io/developer/api/recall)：语义、BM25、图与时间通道融合并重排；结果有 document/chunk、发生时间等坐标；可以带原始文本 chunks、观察的原始 source facts。facts 的 max_tokens 不包含其他元数据，chunks/source facts 有独立预算。尤其注意 temporal_window 用于排序而不是严格时间排除，不能直接替代本项目的时间/权限边界。
- [Retain 文档](https://hindsight.vectorize.io/developer/api/retain)：支持异步写入与 operation_id 防重复入队；提取需要 LLM。部分供应商的异步 Batch API 有折扣，但不能推定当前 V4.1 路由同样支持。
- [Reflect 文档](https://hindsight.vectorize.io/developer/api/reflect)：模型自主使用检索工具后合成答案；structured output 可能额外进行提取。每轮聊天调用 reflect 再调用角色作者会引入额外调用，并且第二个 disposition-aware 作者不应代替 CharacterInterior 的主观判断。
- [原始论文](https://arxiv.org/abs/2512.12818)：检索与推理评测不能覆盖角色生活生成、NPC 主观性、来源权限、长期情绪或 Action 回执。

## 接入边界

| 方面 | 本项目的职责 | 可借鉴／可接入范围 |
|---|---|---|
| 真实经历 | 已接受的 World/Experience/前史档案及完成状态 | 同步这些已认可来源到可重建的只读检索索引 |
| 记住什么、忘记什么 | CharacterInterior 对 MemoryCandidate 的有来源选择 | 索引只镜像当前可访问记忆，不自动把全档案变为角色的记忆 |
| 记忆关联 | BGE-M3、词法、时间和明确故事关系 | 对照评估多通道融合、实体索引、原文扩展及重排 |
| 反思／人格 | 角色模型拥有理解、动机、情绪和表达权 | 后台摘要只能作为有来源的候选理解，不能自动覆盖人格或世界事实 |
| 隔离与权限 | actor、subject、privacy、cursor、status 等精确边界 | bank 隔离可辅助，但返回内容仍须重新绑定本项目有效来源 |
| 速度与费用 | 整个真实回复链路与账单 | 单独计入提取、整理、反思、embedding、reranker 和部署开销 |

不采用“自动把全部聊天写入记忆”的 wrapper。角色说过一个故事，只证明她说过，不能据此认证该故事真的发生过；否则会把编造变成下一轮的证据。Hindsight 的提取器不是本项目的事实签发者。

## 本次采用的优化与后续评估

这次直接优化现有请求的稳定前缀、重复字段与不可变前史哈希，不引入新的在线反思调用。DeepSeek [官方缓存说明](https://api-docs.deepseek.com/guides/kv_cache/)确认仅重复前缀可以命中缓存，因此排序必须发生在 request-local ref 分配之前，不能让每轮新对话给稳定材料重新编号。

若进一步评估 Hindsight，先用同一份72条前史和生产可访问经历做只读对照，覆盖跨故事、时间混淆、人物归属、遗忘／压缩／隐私收紧、原文完整性及不存在的经历。记录检索命中、有效来源比例、延迟和包含后台费用的总账；只有对照显著获益后才引入可替换检索适配器。无需重写整个 World 或迁移事实签发权限。
