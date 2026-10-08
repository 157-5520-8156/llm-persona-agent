# 统一记忆索引的提前准备与增量复用

用户授权：导入后提前计算向量，新增或修改时增量更新，聊天时复用已有索引；后续生活与社交共用机制。基线68f1ad93，隔离工作树，不操作生产QQ。

## 实现范围

- 复用RecallCorpusCompiler的统一RecallDocument：前史、生活Experience/WorldLife、旧对话、事实、私人印象与已接受理解。保留原有权威存储格式；统一的是检索表示而非把主观理解变成事实。
- 每个当前Context依旧按actor/subject/状态/时间/来源编译，删除或权限改变不能靠旧缓存恢复。复用内容向量和不可变索引行，不复用过期的可见性结论。
- 新的语义索引模块按模型版本、维度和精确检索正文复用向量。重复查询只嵌入查询；只新增/改正文才嵌入正文；相同正文的元数据变化重绑当前来源，不重算向量。
- 离线准备入口与普通运行使用同一可信Context来源解析器。启动完成导入后准备；后续背景drain与生活活动提交后准备。前台缺少向量时保留既有有界降级和一次冷启动批处理，不伪装准备成功。
- SQLite向量缓存保留跨重启进度和既有预算；索引是可删除重建的派生材料，准备过程没有World事件、角色选择或对外消息。
- 维护报告区分disabled/ready/degraded，记录水位、变更量和错误。查询排名/字数/来源边界不改；本次不引入ANN或新的LLM抽取/审核。

## 验证入口

使用已存在的RecallCoordinator、RecallIndex、Context解析器和WorldV2TurnApplication公开入口验证；embedding供应商仅在传输接口替换计数fixture。测试准备后查询仅计算query、增量及重启复用、旧新cursor并发隔离、遗忘/权限/来源变动、失败恢复及现有正常回复链路。用户此前要求自行验证推进，本轮不重复询问测试入口。

实库从上一轮accepted-world另克隆，只调用已配置的向量端点，保留原账本与事件哈希；不消耗DeepSeek对话/源稿实验预算。报告真实准备和重复查询耗时，不能等同普通自由聊天语义或整轮性能验收。

## Hindsight参考

沿用已核实v0.10.2差距图 docs/research/hindsight-gap-map-2026-10-07.md：异步写时处理、operation状态、更新水位和源撤销失效值得借鉴。官方滚动[Retain API](https://hindsight.vectorize.io/developer/api/retain)说明同document_id会替换重处理；本项目保留不可变事件，通过当前来源投影更新派生索引，不照搬覆盖原始历史或把整段对话再交给第二个作者。参考[Recall API](https://hindsight.vectorize.io/developer/api/recall)的源证据与结构检索；不据官方指标宣称本地加速或质量收益。
