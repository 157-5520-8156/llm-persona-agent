# 本地准备耗时：可继续推进的架构路径

研究日期：2026-10-12。核对代码：`7088fa31`。本次只读代码、既有离线证据与一手资料；没有运行供应商请求、修改运行代码或部署。下述是候选设计，不是已经实现的性能结果。

## 判断与现有证据

仍有空间，但下一步应减少每轮工作量，而非继续给小函数加缓存。当前已经有增量账本 head、向量缓存、单轮一致读取和纯解析缓存；笼统建议“加缓存”“改异步”“上向量数据库”没有回答剩余问题。

上一轮最终安全版本的单次配对回放是：请求就绪 **2.318 秒**，模拟角色模型 **2.055 秒**，响应后到首句 **1.438 秒**，首句 **5.811 秒**。本地终点模型还设置了 **2.25 秒**固定回放等待，但它与入口工作并行：该模型响应时间为2.319811秒，角色请求就绪为2.317921秒，甚至略早。因此不能说角色一定等它完成，不能直接减掉2.25秒当作可省时长，也不能把请求就绪时间全部当成 Python CPU或相加嵌套 span。这里是含合并、CPU、线程和预取等待的 elapsed 关键路径。这是机制回放，不是云端实测、p95 或答案质量验收。[项目审计](../audits/first-reply-and-life-input-cost-2026-10-11.md)；精确时间见 `output/private-audits/model-context-reading-20261010/latency-safe-candidate-timed-20261011/result.json`。

最终日志的 Context resolve 为 **1274.8ms**，其中 domains **900.6ms**、refs **363.4ms**；snapshot 仅 **1.2ms**。`budget_ms=604.0` 包含检索 refresh/prefetch 等工作，并非仅预算算术耗时。来源：`output/private-audits/model-context-reading-20261010/latency-safe-candidate-timed-20261011.log`。这份样本支持优先查材料/检索及提交工作，不支持直接把数据库重放或快照当作主因。

## 1. 优先：按变化增量维护检索与上下文材料

**一手依据。** Microsoft 的 CQRS 文档允许读写模型使用同一数据库；读模型可为实际查询预先组织资料。物化视图文档强调增量更新、版本化完整发布、权限传播以及刷新成本。这些模式不要求引入 Redis、Kafka 或另一套数据库。[CQRS](https://learn.microsoft.com/en-us/azure/architecture/patterns/cqrs)、[物化视图](https://learn.microsoft.com/en-us/azure/architecture/patterns/materialized-view)

**已核对落点。** `recall_runtime.py:569` 的 `refresh` 每次先全量 `corpus.compile`，再 `rebuild`、`snapshot`。`recall_index.py:1659` 虽然复用向量并避免未变行实际更新，仍读取现有文档行、遍历全部新文档并执行 `model_dump`/canonical JSON、集合哈希。`snapshot` 也读全表，但文档/向量解码已有4096项缓存。真正缺口是“索引内容基本没变，仍走全库准备”，不是“每次重新调用模型嵌入整个前史”。

**本项目候选。** 把不可变正文及结构化阅读材料按来源 ID、内容 hash、编译合同版本维护为可重建材料；分别维护故事成员、计划状态、实际执行、来源绑定的依赖。生活/聊天有新事件时只生成受影响文档的新增、替换、撤回集合。聊天固定新 cursor 后，复用已完成材料代次，合并该代次至当前 cursor 的变化，再进行本轮主体、隐私、时间及来源资格判断。请求所需新变化没有补齐时须同步补齐或明确失败，不能悄悄读旧代次。

关键约束是**材料复用与授权分离**：旧正文可以复用，旧“允许角色读取”结论不跨轮复用；actor、privacy、权限撤回、时钟、故事连接、编译器版本变化都必须触发对应失效。最终 pinned Context、Recall corpus 和检索覆盖需与原路径一致。若现有集合 hash 要求全量 canonical 字节，先保留该合同；不能仅为追求 O(增量) 换成另一种 hash 后冒充兼容。

这应是第一项较大实验；但项目10月9日曾试过仅缓存 canonical 序列化，局部约省20ms、增加约22.5MiB，入口无可靠收益后撤回。因此候选必须覆盖全量编译/装配的依赖，而不是重做同一个 JSON 缓存实验。[既有设计与撤回记录](../design/context-materialization-2026-10-09.md)

## 2. 优先：词法兜底与语义预取共享同一次基础检索准备

**一手依据。** PostgreSQL 文档用公共中间结果避免重复计算昂贵函数，同时指出过度物化可能阻碍过滤下推、反而增加工作。这里借用“共享精确公共子计算”的原理，不建议迁移 PostgreSQL。[公共表达式物化](https://www.postgresql.org/docs/current/queries-with.html#QUERIES-WITH-CTE-MATERIALIZATION)

**已核对落点。** `recall_runtime.py:680` 的 `schedule_prefetch` 同步执行 `semantic=False` 的本地兜底搜索，后台 `_prefetch_context` 再搜索语义通道。`recall_index.py:677` 的共同流程仍包括候选资格筛选、故事读取压紧、词法特征装配及语料词法评分。单文本词法缓存已存在，但不等于两个搜索共享了这次查询的候选与评分准备。

**本项目候选。** 对相同 pinned snapshot、query/lexical_text、actor/privacy、时间窗、link refs、memory kinds、story/episode window、检索策略等完整依赖，构造一个不可变基础检索帧。词法兜底先使用它；语义通道补上当前查询向量和 dense/focused-attention 计算，各自保留原排序、命中通道、来源及 audit trace。任何依赖不同即不能共享。若需要并发构建同一个基础帧，可以采用同 key 只构建一次的机制；Go 官方扩展包 `singleflight` 是这一模式的成熟实例，不是本项目依赖建议。[singleflight](https://pkg.go.dev/golang.org/x/sync/singleflight)

收益来自少做一遍共同准备，不能删除词法兜底、缩小记忆范围、把语义命中伪装为词法命中，或者让慢语义接口阻塞兜底。建议作为第一项的配套实验，先做逐命中/排序/来源/trace 等价对照再测入口。

## 3. 随后：把同一角色决定的必要落账收敛成更少批次

**一手依据。** Transactional Outbox 将业务状态和待发送消息在同一数据库事务里持久化，提交后再由发送器执行。它解决的是发送与状态提交之间的崩溃窗口；发送器仍可能重复执行，因此仍需要幂等机制。[模式作者原文](https://microservices.io/patterns/data/transactional-outbox.html)

**已核对落点。** 上一审计记录响应后到首句经历6次提交，合计约0.735秒；这不等于全部都是 fsync，Python验证、归约、hash和投影也在提交内。`sqlite_ledger.py:4117` 已支持一批事件；`_commit_locked` 顺序归约所有事件后才最终编码/投影。`runtime.py:1648` 则分角色 Appraisal/Affect、关系等 facet 结算。现有 `serialized_commit_sequence` 明确有些 proposal 与 acceptance 合同必须分别提交，故不能直接把所有调用拼接。

**本项目候选。** 在不增加模型调用的前提下，先从已审计的同一 CharacterInterior 决定，按当前 cursor 编译必要内态变化及表达授权；为可兼容的批次执行一次 CAS 和持久化。ActionPump 仅在这批必要状态提交后处理已授权动作。只异步执行外部发送或可重建派生维护，不能把影响下一轮判断的内态结算丢到后台。

这需要明确的批次/权威合同，不是性能补丁：旧事件/旧收据继续按旧合同重放；source closure、失败 facet、发送失败后的已付费内态保留、未来 not_before、预算、claim、effect-once、回执均要维持。表达与内态具有独立成功/拒绝含义时，也不能为了原子化把一个 facet 的失败扩大为删除全部角色决定。必须先证明故障语义等价。

此前“先发送，再结算内态”的候选已因下一轮读到旧状态等问题撤回。Outbox 方向是**先完整持久化必要状态和发送意图，再交付**；不重启已撤回方案。[撤回依据](../audits/first-reply-and-life-input-cost-2026-10-11.md)

## 已做的配套：WAL checkpoint 已移出同步提交

SQLite 官方说明默认自动 checkpoint 可由跨过阈值的 COMMIT 线程执行，造成少数提交变慢；可在其他线程/进程安排 checkpoint。但长读事务会妨碍 checkpoint，WAL增长也可能拖慢读。[SQLite WAL](https://www.sqlite.org/wal.html#performance_considerations)

本项目 `sqlite_ledger.py` 构造器已经设置 `PRAGMA wal_autocheckpoint=0`，注释明确由有界维护路径处理 checkpoint；也已经有独立、短生命周期内容快照。因此“把checkpoint搬后台”“启用 WAL / 短快照”均不是新优化。[既有快照审计](../audits/context-read-scope-2026-10-09.md) 当前证据也没有证明 checkpoint 是主要瓶颈，只应保留提交 phase、WAL大小和维护状态的关联排查。不要通过 `synchronous=OFF` 或降低掉电持久性来美化回复时间；也不要在模型等待期间持有读事务。SQLite 单写者和快照隔离仍是约束。[SQLite 隔离](https://sqlite.org/isolation.html)

## 不优先采用的办法

- 关闭来源/权限校验，或对未经验证的数据使用 `model_construct`，不满足当前合同；将一致性内态后台化还会复现已经确认的跨轮旧状态问题。
- 增加 Python 线程不自动增加 CPU并行度。Python 3.13 官方说明 `to_thread` 通常适合阻塞I/O，受GIL影响；释放GIL的扩展等情形另论。因此先删除重复工作，再按profile判断是否值得切换计算内核。[Python asyncio.to_thread](https://docs.python.org/3.13/library/asyncio-task.html#asyncio.to_thread)
- 现有记忆向量已经预计算/复用，不宜直接迁移近似向量检索。若精确相似度计算成为剩余主因，可离线评估本地批量计算或Faiss的穷举 `IndexFlatIP`；官方将Flat列为精确搜索，但其数值累加、量化精度、归一化和tie排序仍可能与现有实现不同。必须对照位级舍入、命中阈值、排序和权限过滤，不能把算法称为“精确”就宣称项目结果等价。[Faiss索引说明](https://github.com/facebookresearch/faiss/wiki/Faiss-indexes)

## 离线验证顺序与验收口径

1. 冻结相同完整库、配置、自然问题和响应回放；全面阻断网络，复用已付费向量。先分清关键路径、并行重叠、CPU与等待，禁止相加嵌套 span。
2. 第一、二项先比较完整 Context/corpus、所有命中及排序、来源绑定、通道 trace；覆盖无变化、只添一条生活、权限撤回、故事关系变化、库被外部修改、冷启动和并发代次切换。
3. 记录全库访问/序列化次数、变更文档数、重复基础搜索次数、CPU、RSS及模型待发时间。目标应是未变材料不再全量重做；不以单函数微测替代入口改善。
4. 第三项另做逐提交崩溃/取消、用户快速追问、CAS竞争、发送失败/重试、冷重放。下一轮必须读到该读的已提交内态，外部发送只能依据正式授权。
5. 稳定改进后才考虑有限真实验证。外部资料没有给出本项目可达到几毫秒的保证；2–4秒仍由本地关键路径与真实角色模型等待共同决定。

这些方案不减少生活频率、不删生活资料、不替角色选择行为，也不重新加入同步语义审核。它们把确定性重复计算减下来，可能同时减少后台 CPU；DeepSeek费用是否下降仍要由实际输入/缓存/调用数证明。
