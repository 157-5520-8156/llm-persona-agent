# 继续降低模型前准备耗时

基线7f717b08（runtime47dfcfd0）。用户明确两秒只是一站，目标是尽量减少准备占用，给角色模型留更多推理/生成/重试时间。不能改模型权限、记忆内容/覆盖、检索排序/预算、deadline或费用账本，也不增加模型抽取/审核。

当前实际普通入口六轮median2.593秒，Context1.228–1.733秒。复用相同source clone、配置、六个自然问题和HTTP无网络截获，改动先保持固定Context/corpus逐字节一致，再独立测完整入口。不能把profile、startup、截获后的失败落账或模型生成混入请求待发指标。

最新工作线程profile仍有两次Memory.compile、三次LifeContent.compile；主要重复是Pydantic读取对象构造、来源绑定转换和已有前史正文hash。三种设计比较：
- 显式ContextReadFrame生成多视图，可少读同轮资料，但预算裁剪/异常和scope故事边界需要保留。
- 按来源依赖维护增量材料图，扩展性较好，但不能缓存最终授权结果，需要完整失效管理。
- ContextVar请求内共享能保持旧Interface，但内容库还缺显式快照Interface，不能只凭同请求共享缺失/可变sidecar结果。

本轮先选第二种设计中边界明确的纯材料复用，而非整份Context/权限缓存：
1. 单来源前史记忆的原始读取对象按当前候选、不可变来源摘录与评估范围复用。复用前仍检查当前候选、来源、隐私和主体；每轮重算物质更新时间并收窄当前可见故事关系/episode。对外返回复制后的excerpt，私有完整原文指针不可污染缓存。
2. 当前prefix和所需ref筛选后，复用不可变事件到RecallSourceBinding的纯转换；RecallCorpus重查已绑定正文时共用现有不可变record hash，不按自报hash跳过正文。
3. 账本semantic hash流式消费原有排序canonical bytes，保持历史hash完全一致，避免全量字节拼接副本。
4. 向量内积用相同乘法与求和顺序的C层iterator代替Python generator；保留维度错误，逐float位型对照，不采用改变舍入次序的近似算法。

新增公开Memory读取跨时钟/私有指针隔离、真实ledger增量commit无需全量semantic拼接回归；旧版先红。数学微测1024维*2000次约50.6ms->29.2ms只是局部计算，不当作聊天提速。当前79相关回归通过。

后续根据测量继续选择有效方案；需要完整阅读/权限或预算行为变化的共享方案不能为达标硬上。最后双轴复核、冻结源码全量一次、核对源码hash、提交审计。测试不碰生产或真实QQ，原共享账本上限及未知预留保持。


本轮补充：故事episode的opaque ID和来源值按episode计算一次，位置/可见成员仍每次计算；RecallSourceBinding以完整五个primitive字段复用纯构造，异常/非标准类型保留原构造验证；同一次Memory.compile将已核验前史行输出到局部表供读取使用，此表仅写入新证明、不消费外来条目，每次新读取都重新核验。

序列化实验已撤销：1634份文档原canonical序列化中位46.6ms、实验26.1ms，全文字节相同，但六轮入口中位2.134s相比前版本2.120s无可靠整体改善，增加约22.5MiB缓存。未保留对应缓存/通用模块/代码，只保留私有实验证据。没有引入ContextReadFrame、ContextVar权限共享或完整依赖图。
