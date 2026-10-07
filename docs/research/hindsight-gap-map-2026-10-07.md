# Hindsight 机制与 Girl-Agent 差距核对

2026-10-07。本节核对官方文档、官方源码与原论文；未运行付费模型、未访问账号或生产、未修改实现。Girl-Agent 本地实现已逐项核对，下文区分最新候选、旧工作目录及尚未接入的机制。

## 版本与证据边界

- 比较基准应保持项目 10/1 固定的 **v0.10.2 / `5fc4ce20917b916240cef27c212c387a177f115b`**。本次重新从官方仓库检出该 tag，确认 peeled commit 与上述一致；tag 对象自身是 `70831d679eefe9a0a63bb2698d2785a77e7e5b4a`。[官方发布](https://github.com/vectorize-io/hindsight/releases/tag/v0.10.2)
- 在线文档是滚动文档，不能默认与固定版完全一致。2026-10-07 读取到官方 `main` HEAD 为 `9269b88417ed263e5a8350f2e416ca2b322756b1`；以下每项核心机制另给固定 commit 的源码或文档链接。
- 原论文描述 TEMPR/CARA，并包含 opinion network、disposition-conditioned reasoning；当前 retain 文档的直接事实类型是 world/experience，observation 来自后台整理。论文中的 opinion 类别不能直接当作当前 API 的存储类型。[原论文 v1](https://arxiv.org/html/2512.12818v1)、[固定版 retractions 对类型的限定](https://github.com/vectorize-io/hindsight/blob/5fc4ce20917b916240cef27c212c387a177f115b/hindsight-api-slim/hindsight_api/engine/reflect/retractions.py#L38)
- 论文问答准确率与官方操作延迟均不是 Girl-Agent 中文角色系统的实测结果。本次机制调查不能解除既有 reranker、中文检索、成本和来源安全的资格门槛。

## 官方机制核对

| 机制 | 已核实的能力 | 借鉴价值与本项目边界 |
|---|---|---|
| Retain 结构化提取 | 长文分块后由 LLM 提取事实、人物归属、实体、事件发生范围和明确表达的理由；同时保存 learned/mentioned time，建立实体、时间、语义与因果关系。world/experience 按 bank 的 agent 与其他说话人区分，不应单看第一人称语法。 | 可改善“什么时候、谁做了什么、为什么”的索引。提取是对输入的解释，不能把模型说过的故事认证成实际发生，更不能自行授予记忆可见权限。[在线 Retain](https://hindsight.vectorize.io/developer/retain)、[固定版 extraction](https://github.com/vectorize-io/hindsight/blob/5fc4ce20917b916240cef27c212c387a177f115b/hindsight-api-slim/hindsight_api/engine/retain/fact_extraction.py)、[固定版 link creation](https://github.com/vectorize-io/hindsight/blob/5fc4ce20917b916240cef27c212c387a177f115b/hindsight-api-slim/hindsight_api/engine/retain/link_creation.py) |
| Entity resolution | 名称相似度、共现关系和近期出现信息参与实体统一；固定版使用 trigram 及批内去重，也设名称相似度门槛。 | 人名变体、称谓和跨故事实体关联值得评估；中文短名、重名及不相似昵称不能假设自动正确。Girl-Agent actor/subject 身份不能交给模糊匹配覆盖。[固定版 entity_resolver](https://github.com/vectorize-io/hindsight/blob/5fc4ce20917b916240cef27c212c387a177f115b/hindsight-api-slim/hindsight_api/engine/entity_resolver.py#L503)、[在线实体解析说明](https://hindsight.vectorize.io/developer/retain#entity-resolution) |
| 四路 Recall + RRF + cross-encoder | 语义、BM25/全文关键词、图、时间检索并行，按 RRF 融合排名，再以 cross-encoder 对 query/candidate 重排；随后还有轻量新近度、时间接近度与 observation 证据数信号。没有 cross-encoder 的部署存在 RRF fallback。 | 重点是候选互补与对原 query 的联合重排，不等价于把几项手工分数相加。reranker 是否真正加载、中文全文索引是否有效必须分别验证。[在线 retrieval](https://hindsight.vectorize.io/developer/retrieval)、[固定版 fusion](https://github.com/vectorize-io/hindsight/blob/5fc4ce20917b916240cef27c212c387a177f115b/hindsight-api-slim/hindsight_api/engine/search/fusion.py#L51)、[固定版 reranking](https://github.com/vectorize-io/hindsight/blob/5fc4ce20917b916240cef27c212c387a177f115b/hindsight-api-slim/hindsight_api/engine/search/reranking.py) |
| 图多跳与时间覆盖 | 图路沿实体/记忆关系寻找表面语义不相似的关联。固定版时间路把时间窗分成 8 桶，先从每个有数据的桶取强匹配，再补第二层，以免全年查询被高密度月份占满；另有受预算限制的 temporal/causal spreading。 | 可改善“同一人不同故事”“前因后果”“全年回顾”。关联边只能提示检索，不能认证因果。`temporal_window` 只影响时间路与排序，其他路仍可返回窗外结果；它不是严格时间或未来信息过滤。[固定版 graph](https://github.com/vectorize-io/hindsight/blob/5fc4ce20917b916240cef27c212c387a177f115b/hindsight-api-slim/hindsight_api/engine/search/graph_retrieval.py)、[固定版 time buckets](https://github.com/vectorize-io/hindsight/blob/5fc4ce20917b916240cef27c212c387a177f115b/hindsight-api-slim/hindsight_api/engine/search/retrieval.py#L406)、[API 时间窗边界](https://hindsight.vectorize.io/developer/api/recall#temporal_window) |
| 原文扩展与证据预算 | Recall 可返回 chunks；observations 可带原始 source facts 和 source_fact_ids。事实文本、原文 chunks、source facts 有各自预算，不是一个 max_tokens 覆盖整个响应。 | 小片段命中后回到完整事件／原文能恢复人物、时间、否定与上下文；源证据预算用完不能冒充来源完整。当前在线 API 有 `source_facts_truncated` 提示；接入时应核对固定版响应字段，不能依赖未核实的新字段。[固定版 Recall API](https://github.com/vectorize-io/hindsight/blob/5fc4ce20917b916240cef27c212c387a177f115b/hindsight-docs/docs/developer/api/recall.mdx)、[在线 Recall source facts](https://hindsight.vectorize.io/developer/api/recall#source_facts) |
| Observations 跨记忆归纳 | retain 后异步把新增事实与同 scope 既有 observations 对照，由 LLM create/update/delete；记录 supporting source_memory_ids、proof_count、历史与证据。新矛盾倾向保存“曾经 X、后来 Y”的演化。近重复整理有额外语义判断，不只是向量阈值直接合并。 | 最值得借鉴的是可追溯的持久理解，而不是压缩摘要本身。它仍是推断；主观意义／人物评价应由 CharacterInterior 接受或拒绝，不能升为事实。来源删除或不可访问时必须失效／重建。[在线 Observations](https://hindsight.vectorize.io/developer/observations)、[固定版 consolidator](https://github.com/vectorize-io/hindsight/blob/5fc4ce20917b916240cef27c212c387a177f115b/hindsight-api-slim/hindsight_api/engine/consolidation/consolidator.py#L1)、[固定版删除失效](https://github.com/vectorize-io/hindsight/blob/5fc4ce20917b916240cef27c212c387a177f115b/hindsight-api-slim/hindsight_api/engine/memory_engine.py#L10685) |
| Mental models / knowledge pages | 常用 source query 的 reflect 结果持久化；full 重算或 delta 结构化编辑。knowledge page 是 mental model 的预设：从 observations 构建、整理后增量更新、排除其他 pages 以免互相引用形成反馈。页面还可按树组织、全文+语义搜索、导出／投影 Markdown。 | 可以后台维护人物、关系、长期主题的有来源理解，减少每轮重新归纳。需要更新水位、scope、source closure 和失效机制；不能仅缓存最后一次摘要。[在线 Knowledge Pages](https://hindsight.vectorize.io/developer/knowledge-pages)、[固定版 pages 文档](https://github.com/vectorize-io/hindsight/blob/5fc4ce20917b916240cef27c212c387a177f115b/hindsight-docs/docs/developer/knowledge-pages.mdx)、[固定版 refresh schema](https://github.com/vectorize-io/hindsight/blob/5fc4ce20917b916240cef27c212c387a177f115b/hindsight-api-slim/hindsight_api/engine/mental_model_refresh.py) |
| 新鲜度与撤销来源 | 固定版以模型读过的 `last_memory_seen_at` 水位判断同 scope 数据是否变动，而不只看刷新钟表时间。delta 窗口覆盖该水位后写入或编辑的记忆。另检查 based_on 引用是否仍有 live row，处理删除、失效及被清除的 observation；空候选不覆盖最后有效内容。 | “摘要还在，但根据已失效”比检索少命中更危险，值得优先借鉴。不过固定版对全部引用同时失联的情况有保守特殊处理：清理悬空引用而保留正文，防止导入后 ID 更换把整页抹掉；源码承认这可能保留过期句子。Girl-Agent 可见事实应继续 fail closed，不能照搬这个保留策略。[固定版 stale scope](https://github.com/vectorize-io/hindsight/blob/5fc4ce20917b916240cef27c212c387a177f115b/hindsight-api-slim/hindsight_api/engine/memory_engine.py#L1823)、[固定版 retractions](https://github.com/vectorize-io/hindsight/blob/5fc4ce20917b916240cef27c212c387a177f115b/hindsight-api-slim/hindsight_api/engine/reflect/retractions.py#L1) |
| Reflect 工具式取证 | 受迭代预算约束的 agent loop，可搜索 mental models、observations、raw facts，expand 到 chunks/documents，再输出答案；新鲜 models 与 stale observations 决定是否回查底层证据，citation ID 必须曾被检索。可输出 based_on、tool/LLM trace 和 token usage；response_schema 的机器结构另做抽取，可能增加 LLM 调用且单独失败。 | 可借鉴“知道缺哪段证据后换 query／扩展原文”的机制；不应每回合叠加第二个 disposition 作者替 CharacterInterior 作人格判断。只验证 citation ID 不等于验证每句内容被来源蕴涵。[在线 Reflect](https://hindsight.vectorize.io/developer/reflect)、[固定版 agent](https://github.com/vectorize-io/hindsight/blob/5fc4ce20917b916240cef27c212c387a177f115b/hindsight-api-slim/hindsight_api/engine/reflect/agent.py#L456)、[固定版 structured output](https://github.com/vectorize-io/hindsight/blob/5fc4ce20917b916240cef27c212c387a177f115b/hindsight-api-slim/hindsight_api/engine/reflect/agent.py#L251) |
| Filters 与 async operations | bank 隔离；types/tags/tag_groups 控制检索范围，strict 模式与非 strict 模式对无 tag 数据不同。retain/consolidation/model refresh 可作为 operation，跟踪状态与错误；后台归纳和页面刷新存在最终一致性。 | 有助于后台作业去重、失败可见及将写时整理移出回复路径。bank/tag 不能替代 actor、privacy、状态、cursor 与来源权限；“提交成功”不等于记忆已索引、归纳已完成或页面已更新。[固定版 Operations](https://github.com/vectorize-io/hindsight/blob/5fc4ce20917b916240cef27c212c387a177f115b/hindsight-docs/docs/developer/api/operations.mdx)、[固定版 Recall filters](https://github.com/vectorize-io/hindsight/blob/5fc4ce20917b916240cef27c212c387a177f115b/hindsight-docs/docs/developer/api/recall.mdx) |

## 官方材料之间需要纠偏的地方

1. 2026-06-05 mental models 博客把 delta 窗口写作 `created_after=last_refreshed_at`。v0.10.2 的 refresh schema 明确写 `last_memory_seen_at`，并包含 written **or edited**；应以固定源码为准。博客的 full/delta、保留未修改区块、空内容保护仍可帮助理解，但不能充当所有当前细节的依据。[博客](https://hindsight.vectorize.io/blog/2026/06/05/mental-models-deep-dive)、[固定版 watermark 字段](https://github.com/vectorize-io/hindsight/blob/5fc4ce20917b916240cef27c212c387a177f115b/hindsight-api-slim/hindsight_api/engine/mental_model_refresh.py#L151)
2. Recall 架构概述有“temporal filters”的简写，但 API 明确说明这不是全局严格过滤。时间／权限约束需要在索引候选和来源绑定阶段独立执行。
3. 证据数是支持条目数量，不是独立证据数量或校准的真实性概率；RRF/CE final score 也是查询内相对排序，不能当角色确信程度。
4. mental models、knowledge pages、时间桶、归纳失效闭环在本次固定版已存在；这里的“未引入”必须由 Girl-Agent 当前代码对照判断，不能由发布时间推断。

## 最值得进入本地差距审查的五项（建议，尚非实现结论）

1. **带源证据和演化历史的长期理解**：从反复经历形成有来源的 observation／理解候选，让 CharacterInterior 决定是否接受。保留反例、旧理解与时间变化，避免只增加碎片。
2. **增量刷新 + 撤销／权限变化失效**：后台维护常用人物／关系／主题页，记住输入水位与完整来源闭包；来源不再有效时停止将其作为可见事实提供，而不是仅等待下一次检索。
3. **角色可用的自适应证据检索**：给同一个角色作者有预算的 search/expand 能力，按实际证据缺口重选 query、读取事件全文；技术检索失败与真实“没有记忆”分开。
4. **跨故事实体图、多跳与时间覆盖**：先做最小对照证明它能找回向量/词法未命中的信息；图边始终是导航，身份、因果和时间效力仍回原始证据验证。
5. **四路融合与中文 cross-encoder 的受控资格测试**：覆盖直接、释义、关系、时间、反例与无记忆题；在同一 corpus/query/billing 条件下判断是否受益，而不是因官方 benchmark 较高便迁移。

优先顺序要由主任务结合已实现范围调整。原文扩展、过滤、异步作业若本地已有对应机制，应标为“已有／部分已有”，而不是再包装成新功能。

## Girl-Agent 当前代码与实测映射

本节基准为最新专用工具候选`f028929e`（本工作树`codex/role-memory-qualification`）。当前主目录`/Users/geoff/Projects/Girl-Agent`仍检出`b641109f`，该提交没有`memory_understanding.py`；不能把最新候选的能力说成已经合回那份检出或完成生产切换。运行路径没有调用Hindsight服务或客户端，官方实例仅用于历史隔离对照。

| 官方机制 | 当前对应实现与状态 | 尚未具备或证据边界 |
|---|---|---|
| 世界事实／经历／私有理解分层 | 原有World Fact、Experience、PrivateImpression已区分权限；新增Understanding是可推翻的私有读法。这类分层基础先于本轮Hindsight借鉴。 | 存储类型不是官方四类的直接复制；理解不能签发World Fact。[ADR-0021](../adr/0021-sourced-incremental-memory-understandings.md) |
| 异步retain与角色保留选择 | 已接入Fact/Experience/Prehistory候选、角色retain/forget、后台复查、CAS/幂等及回执；生产构造器安装`MemoryConsolidationRuntime`并传入已有BGE端口。 | 不自动retain全部聊天或模型自述；Hindsight式通用文本实体关系抽取没有整体接入。见`production_turn_application.py:4237`、`memory_consolidation_runtime.py:78`。 |
| Observations增量归纳 | **已接线**：新记忆提供整理机会；事实、经历、前史按来源平衡进入批次；同一CharacterInterior看到原文、关联记忆及相关旧理解，可新建、revise、merge、release或明确不更新。 | 模型语义去重和理解并非稳定正确；向量只给候选，不直接合并。验证逐字引文不等于验证总结蕴含。见`memory_consolidation_runtime.py:141`、`memory_consolidation_contract.py:23`。 |
| 引文、历史、来源撤回 | **已接线**：理解绑定成功作者回执、真实保留效果和原文引文；修订继承依赖；遗忘、改正文、隐私收紧或不可读使下游理解不可用。保留历史版本并区分active/superseded/released。 | 这部分不应再列为“尚未做”；语义矛盾判断仍由角色完成，未装通用自动矛盾检测器。见`memory_understanding.py:122`、`:196`、`:222`。 |
| Mental models的持久理解与回读 | **核心部分已有**：有facet/summary/confidence/来源/前驱/形成时间，进入私有内心材料及Recall；标明新证据待整理。 | **完整产品形态未有**：没有Knowledge Pages主题树、按主题source_query管理、独立页全文检索、topic-scoped水位与可控full/delta页面刷新。不能把几条facet理解说成完整Mental Models系统。见`memory_understanding.py:90`、`:270`，`character_interior/production.py:225`。 |
| 四路Recall | 有BGE-M3、中文词法稀有度/覆盖度、显式关系、时间信号及严格actor/隐私/状态/时间过滤。 | 当前仍逐文档加权`lexical*.35+dense*.35+structured*.20+temporal*.10`；词法不是完整BM25，未装四个独立候选路的RRF与cross-encoder。见`recall_index.py:630`、`:1691`。 |
| 图多跳 | **局部已有**：前史的`related_record_refs`双向构成图，BFS扩展到有界故事与六条读取上限；另把旧印象与后来理解关联。 | 不能说完全没有图或只有一跳。缺的是通用实体关系图、跨事实／经历的temporal/causal检索臂及模糊名称归一；显式身份仍由本项目权威管理。见`recall_index.py:1027`、`:1148`。 |
| 时间检索 | 已有时间范围和效力过滤、新近度权重、历史发生范围处理；角色Recall请求可以提供日期范围。 | 未见官方时间路那种桶覆盖候选选择；查询全年／关系演化仍可能被相近片段挤占。见`recall_index.py:1729`。 |
| Chunks／回原文 | **已接线**：长前史窗口检索、有界故事补全、hybrid.14在预算内将已选窗口替换为同源完整父记录；新的quote_ref精确还原允许引用的原文。 | 当前完整父记录仅对窗口化前史、长度≤1024字符且预算允许的来源生效；不是所有Fact/Experience都拥有通用expand_document工具。见`recall_corpus.py:429`、`recall_index.py:973`。 |
| Reflect工具式取证 | 已有自动预取及同一作者主动Recall、回读后再完成决定；后台长期理解进入下一次判断。 | 回复路径共享一个secondary provider slot，Recall会消耗该槽；不是Hindsight的多轮search/expand/learn/done循环。未装按证据缺口多次换查询、扩展文档的通用工具。见`deliberation.py:131`、`recall_runtime.py:813`。 |
| 长期原记忆压缩 | 存在`MemoryRepresentationDraft`并保存不可变草稿；明确`authority=unaccepted_draft`、`author_association=unverified`。 | **未形成已授权原记忆压缩生命周期**。新长期理解不覆盖或删除原文，也不能冒充压缩后的可用Fact/Experience。见`memory_representation_draft.py:1`、`:69`。 |
| 增量缓存与批量刷新调度 | 原生索引已有noop/cursor_only/documents_changed及向量缓存；理解批次以support fingerprint确认已处理的材料。 | 理解读取仍遍历已完成批次重建，没有独立的主题视图水位缓存和Hindsight式Knowledge Page刷新管理全套；不要把已有增量确认和缓存说成完全没有。见`recall_index.py:1381`、`memory_understanding.py:122`、`memory_consolidation_runtime.py:86`。 |

上述代码坐标均相对本工作树根目录。核心实际接线另见[增量理解验收](../audits/incremental-memory-understanding-2026-10-01.md)、[记忆回流修复](../audits/private-memory-feedback-2026-10-01.md)。后者的“尚缺增量理解”是更早阶段结论，随后已被前者的实现补上，不能照抄旧结论。

最近完整虚拟日的冷回放恢复**52个理解版本、4个当前理解、零来源抑制项**，证明接受、持久化和回读路径已经运转；它不证明理解语义质量或人格影响整体达标。[当前验收](../audits/role-memory-qualification-2026-10-06.md)

## 结合本项目的优先顺序

1. **先改善相关故事的排序与覆盖。** 对现有候选做小规模中文cross-encoder试验，并比较独立候选路融合；随后补通用实体关联和时间桶。10/1固定71来源/40题对照中，完整Hindsight多阶段故事来源召回91.67%，旧原生路径77.78%，但CPU重排300候选使检索中位16.765秒。简单来源级RRF融合没有赢过旧原生路径。最新hybrid.14未重新完成这套Hindsight对照；旧数字不当作当前胜负。[历史实测](../audits/hindsight-head-to-head-2026-10-01.md)
2. **把现有理解做成更完整的、由角色维护的主题视图。** 复用已有facet/前驱/依赖闭包，增加按主题的新增证据水位、反例定位、刷新合并与失效缓存；人物／关系／主题名称、理解和修订决定仍由角色提供。这里需要补的是工程完整度与实测质量，而不是再做一次“有来源摘要”的同类原型。
3. **给同一角色作者有限的证据缺口检索与通用原文展开。** 常规回合保持现有预算；复杂跨时期问题可申请更多search/expand步骤，真实检索失败继续表现为技术失败。不能额外叠一个Hindsight disposition作者先替角色下结论。
4. **最后补原记忆压缩与规模资格。** 真正替换长记忆表示必须保留原文可回查、源主体/时间/状态及遗忘失效；当前draft不是已完成压缩。对大规模记忆再测增量读取和持久化开销，不将Hindsight页面压缩优势直接当作本项目效果。

暂不为本次问题更改后端、安装服务或进行新的付费试验。优先项是根据当前代码缺口和历史对照作出的工程判断，尚非新实现或性能保证。

