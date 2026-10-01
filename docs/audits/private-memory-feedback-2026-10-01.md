# 记忆回流：第一轮修复与验证

用户要求借鉴 Hindsight 的有效机制，检查角色自己的感受和理解能否反向落账、持续影响后续判断。本轮在 `codex/life-materials-prehistory` 工作树进行；生产数据库只读备份到隔离副本，没有切换生产、改写历史或发送 QQ。

## 结论与现有机制

系统已有回流入口，不能描述成“完全没有记自己的感受”：生活 Experience 可以携带独立的 CharacterResponse；入站角色选择 `keep_impression` 后可复用当轮模型结果落 PrivateImpression；后台角色也可选择 retain/consolidate/supersede/release。感受保持可修订的私有理解，不获得外部事实签发权。

实际问题之一是已经写下的理解不能可靠地重新进入内心材料。生产副本 sequence 25774 中有 187 条 MemoryCandidate（170 active、17 forgotten），来源为 71 prehistory、104 experience、12 fact；55 条 PrivateImpression 中 52 superseded、1 released、2 active。这些是各类记录数量，不是独立人生事件总数。

## 已修改

1. 历史理解不再在构建检索语料时直接删除。普通 Recall 仍只允许 active/released；角色显式 `include_historical` 才能取回已替代、过期等历史理解。
2. 最近 32 条 Appraisal 窗口不再使长期印象丢失依据。新增 `reflection_appraisals` 仅提供印象实际引用的解释基础，不把全部旧 Appraisal 再生成一遍检索文档。主体、用户渠道限制及完整来源检查保留。
3. 根据角色当时实际选择的印象来源构建关联；显式历史检索可在已有字节和条目预算内携带关联的后来理解。来源关联不是替代关系证明，不根据同主体或相似措辞自动合并记录。
4. 新 hybrid.13 阅读保留 status 和 interpretation_scope。区分当前可修订理解、历史理解、过去的生活感受；回忆旧情绪不等于现在再次产生该情绪。旧 hybrid.12 等回执保留其原有阅读形状。
5. 检索正文进入 `remembered_understandings`，同时进入 private_self/selective_memory；不与 active-only 的 living_impressions 混合。原先正文可能被私有印象字段白名单丢掉，或被“活跃集合优先”覆盖。
6. 某条理解的形成时间使用该版本的 last_supported，而非 consolidation 继承的 first_seen。第一次在意一件事和后来形成这段理解不是同一个时间。
7. 反思能力说明允许短感受/no_change、不同事情分别记下、已有理解不变时不重复改写。是否保留、如何理解、是否改观仍由同一角色模型决定；没有增加情绪或行为规则。

## 验证

- 针对检索、来源解析、印象接受、内心上下文、角色协议、记忆复查及生活感受保留的回归：**401 passed**。随后将新增阅读处理限定在私有理解上，相关 **54 项复验通过**；两组有重叠，不相加。Ruff、git diff --check 通过。
- 全套测试曾启动，在约 168 秒、2294 passed 时主动中断。此后又有局部修改，最终验证以 401 项针对性检查为准；**不宣称全套发布门禁通过**，两个数字不可相加。
- 用生产副本重建独立反思检索语料：旧来源选择方式只能得到 1 条可读印象；增加历史印象及完整解释基础后得到 51 条。这是可读语料恢复，不是回答准确率。
- 初始三个宽泛回想探针均未精确命中指定版本：两条旧 Epoch 迁入记录缺少实际解释基础，按原来源规则继续不可读；另一条虽进入候选，仍被大量相似改写挤掉。不能报告初始探针全部成功。
- 在有来源的历史理解中另行冻结三个带时间范围的探针，实际 BGE-M3 检索：旧路径 0/3、新路径 3/3 命中指定版本；六次均 embedding_status=used。这只验证来源完整的、带日期条件的历史回想，不证明任意改述或海量记忆覆盖。
- 三次实际 V4.1 独立阅读诊断：两次能概括过去理解与后来重心变化；一次对 active 来源过度保守，仍称缺少当前理解依据。它们不是 CharacterInterior 新决定的接受、QQ 投递或人格因果实验。已知 3506 输入、1123 输出 tokens，按项目价格表估算阅读费用 CNY 0.01549，非供应商账单。
- 私有材料位于 `output/private-audits/memory-feedback-20261001/`，包括一致性副本、探针、冻结问题、原始 BGE 返回、实际阅读及费用账本。未公开 API key 或全部生产私有记忆。

## 借鉴边界与下一步

参考官方 [observations 文档](https://hindsight.vectorize.io/developer/observations)：后台整理、来源绑定、理解可以更新且旧版本保留；以及 [mental models](https://hindsight.vectorize.io/developer/api/mental-models) 的预计算思路。这里复用角色自己的 PrivateImpression，未加入第二个模型自动决定人格，也未整体迁移 Hindsight。

本轮没有增加每次聊天模型调用，仍沿用现有后台调度和入站付费结果搭载。增加的是可重建的本地索引材料和有界阅读；没有由此证明每日成本或聊天首条延迟达标。

长期机制仍有实际缺口：当前 memory_consolidation 主要做 retain/forget，真正的压缩仍只有未授权草稿；前史和运行经历的跨时期理解，需要接入后台反思的完整可选择证据，而不只停在一般快照里；大量相似改写的精确定位，以及旧 Epoch 缺失来源的处理仍待解决。后续应沿同一角色的“已有记忆→新解释→接受→冷恢复→后续选择”链路做有/无记忆对照，不能用本轮三次阅读代替该证据。
