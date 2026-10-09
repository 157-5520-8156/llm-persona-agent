# 一轮 Context 的一致读取与多视图复用

基线af814fa0（runtime d3d7fb06）。继续减少准备耗时，不把两秒当终点；不改变角色语义、原文、检索排序/覆盖、读取预算、deadline或费用上限。

## Interface 与范围

在LedgerProjectionContextResolver核验当前projection后进入 `context_read_scope(store, projection)`，在本轮同步编译结束、失败或返回时释放。现有compile/read_exact Interface保持兼容；未知store没有read_snapshot则不启用共享。

SQLite使用独立只读连接的BEGIN+SELECT固定快照，不在共享writer连接上持有事务；内存Adapter在锁下复制不可变记录表。原store.read_exact仅在当前owner线程且scope有效时路由到快照，普通调用维持原路径。嵌套scope独立，复制context到其他线程不共享；退出关闭lease、清空材料和读取缓存。SQLite :memory:使用独立内存backup。

同snapshot中的缺失/已有内容保持一致；下一轮看到新插入、撤回或损坏。Store只管理原始字节，无角色/权限知识；权限结果复用仅在reader Module且绑定本轮projection、actor、privacy、budget、limited refs和相关对象身份。内容快照最多缓存2048ref/16MiB估算字节，单轮材料最多4096项，不跨请求保留。

LifeContentCompiler同参数视图复用，WorldLife的重复默认视图不再重读；不同预算独立呈现。结构化Experience先形成已核验的完整material，按每次max_characters裁剪，保留单项structured_content_unavailable/总量budget_exhausted的原语义。前史证明只在records/archives/committed_events三组对象与本scope投影完全一致时复用；新scope重新证明，actor/候选/可见故事关系仍另行检查。

材料只缓存不可变结果。已完整证明不可变的嵌套对象可在本scope复用检查；失败检查产生的ID不能进入已知集合，且已知ID对应对象必须由缓存结果保留，避免ID重用。当前没有完整的跨query依赖材料图，也没有跨query授权缓存。

## 验证

先红后绿：快照内补写隔离/下一scope刷新；同cursor外部篡改下一scope拒绝；actor/privacy/budget/限读ref/projection切换；跨线程context复制、嵌套scope、异常释放及WAL checkpoint；未知Adapter原行为；每次新前史读取重新证明；结构化文本/null响应在多个预算下逐字节一致。再以完整前史固定Capsule/corpus对照及真实配置普通入口测量；全suite仅在最终源码冻结后运行一次。

## 原始资料

SQLite隔离文档说明WAL读取事务固定一个快照，换连接的写入需结束本次事务后才能看到；同连接的读写不具备此隔离，所以采用独立只读连接：https://www.sqlite.org/isolation.html 。WAL允许读写并发但长期读事务会影响checkpoint，所以scope只覆盖Context编译并在finally释放：https://www.sqlite.org/wal.html 。ContextVar按context存储、可复制，故额外绑定owner线程并检查closed：https://docs.python.org/3/library/contextvars.html 。这些机制不等于本项目性能结果，效果以固定实验为准。


补充实现：LifeContent先构造完整material rows，再独立执行每种视图的单项/总量裁剪；当前限定范围内的MemoryRetrievalDecision也按candidate、privacy与九组源投影集合身份复用，作用域外沿原函数路径。前史来源证明依赖三个实际集合身份。跨作用域只复用不可变对象的纯结构检查布尔值（弱引用、4096条）；不跨域保留任何资格结论。Scope中的失败/可变结果不会污染已知不可变ID集合。

快照连接路径取自SQLite实际打开的main数据库，避免后续cwd变化；关闭的父store不能新建lease，现有lease退出即失效。当前固定四个Capsule/corpus逐字节相同；冻结后的两组共12次入口样本中位2.070s，4/12<=2s，收益有限，不声明稳定2秒；完整验收与全suite一次超时失败及定向复测见audit。场景覆盖包括文本/显式null回应、多预算原抑制原因、SQLite内存Adapter、lease过期、cwd变化、每轮重新核验。
